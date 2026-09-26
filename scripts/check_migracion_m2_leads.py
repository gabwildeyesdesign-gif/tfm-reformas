"""
Verificación del tope de superficie (m2): CHECK chk_leads_m2_rango en la
base de datos y le=500 en Pydantic.

Casos, todos con ejecución real:

  A. m2 > 500 en la base de datos     -> el CHECK lo RECHAZA.
  B. m2 = 500 exacto                  -> ACEPTADO (límite inclusive).
     (y 500.01 rechazado, para ver el límite por los dos lados.)
  C. m2 no numérico ("abc")           -> el cast ::numeric falla con
     DataError, NO con violación de CHECK. Se comprueba además, contra el
     servidor REAL, que ese fallo llega al manejador global de
     app/main.py: 500 genérico, sin traceback, y fila en logs.
  D. Clave 'm2' ausente del JSON      -> el CHECK NO lo bloquea. En
     Postgres, ->> devuelve NULL, la condición queda en "desconocido" y
     un CHECK solo rechaza lo FALSO. Queda documentado: el CHECK
     garantiza el RANGO, no la PRESENCIA; la presencia la garantiza
     Pydantic en el único camino real de escritura (POST /leads).

  E. La puerta HTTP: POST /leads con m2 fuera de rango -> 422, sin tocar
     la base de datos. Es la otra mitad de la defensa doble.

Los casos A a D van dentro de UNA transacción que termina en rollback, y
cada uno en su propio SAVEPOINT: en Postgres una sentencia fallida aborta
la transacción entera, así que sin savepoints el primer rechazo esperado
impediría ejecutar los demás (patrón ya usado en
check_migracion_calculate_estimate.py y check_migracion_umbrales_gate.py).

Los casos C-HTTP y E necesitan el servidor, que este script arranca por
su cuenta con uvicorn.Server (sin --reload) y apaga al terminar.
"""

import sys
import threading
import time
import uuid
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

import psycopg2
import requests
import uvicorn
from psycopg2.extras import Json

from app.config import DATABASE_URL, WEBHOOK_SECRET
from app.schemas.common import MAX_M2_LEAD

NOMBRE_SAVEPOINT = "asercion"
NOMBRE_CHECK = "chk_leads_m2_rango"
PUERTO = 8015
BASE = f"http://127.0.0.1:{PUERTO}"
AUTH = {"X-Webhook-Secret": WEBHOOK_SECRET}
EMAIL = "check-m2@example.com"

# CORRECCIÓN 2026-09-26: que el script solo lea y borre SU log de error.
# Antes borraba todos los logs 'sistema' de la ruta /calculate-estimate, y
# esos los escribe el manejador global para CUALQUIER 500 real de esa
# ruta, también los del uvicorn que sirve a n8n.
#
# La prueba C corrompe un campo del lead con un texto que no es booleano, y
# Postgres copia ese texto en el mensaje del error
# (invalid input syntax for type boolean: "..."). En vez de "abc", el texto
# es ahora MARCA_M2 + 8 caracteres al azar de esta ejecución. Así:
#   - leer: el log de ESTA ejecución es el que contiene VALOR_CORRUPTO;
#   - borrar: cualquier log cuyo mensaje contenga MARCA_M2, de esta
#     ejecución o de una anterior que se cortara antes de limpiar.
# Ningún log real puede contener "m2check-": solo lo escribe este script.
MARCA_M2 = "m2check-"
# uuid4().hex son 32 caracteres hexadecimales al azar; con 8 basta para
# que dos ejecuciones no coincidan.
VALOR_CORRUPTO = MARCA_M2 + uuid.uuid4().hex[:8]

ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    global ok
    if condicion:
        ok += 1
        print(f"  [OK]    {titulo}  {detalle}")
    else:
        fallos.append(titulo)
        print(f"  [FALLO] {titulo}  {detalle}")


def ejecutar_en_savepoint(cur, sql, params=None):
    """
    Ejecuta una sentencia aislada en su propio punto de guardado y
    devuelve (error, filas), sin dejar la transacción abortada. Misma
    función que en los otros dos check_migracion_*, copiada a propósito
    para que cada script se pueda leer entero por separado.
    """
    cur.execute(f"SAVEPOINT {NOMBRE_SAVEPOINT};")
    try:
        cur.execute(sql, params)
        filas = cur.fetchall() if cur.description is not None else None
        return None, filas
    except psycopg2.Error as error:
        return error, None
    finally:
        cur.execute(f"ROLLBACK TO SAVEPOINT {NOMBRE_SAVEPOINT};")
        cur.execute(f"RELEASE SAVEPOINT {NOMBRE_SAVEPOINT};")


def insertar_lead(cur, cliente_id, m2):
    """
    Inserta un lead saltándose Pydantic, con el m2 que se le pida (puede
    ser un número, un texto o None para no poner la clave). Es la única
    forma de probar el CHECK: por la puerta HTTP, Pydantic rechazaría
    antes estos valores.
    """
    datos = {"tipo_reforma": "bano", "nivel_acabados": "medio",
             "incluye_cambios_estructurales": False}
    if m2 is not None:
        datos["m2"] = m2
    return ejecutar_en_savepoint(
        cur,
        "INSERT INTO leads (cliente_id, canal, datos_estructurados) VALUES (%s,%s,%s) RETURNING id;",
        (cliente_id, "check_m2", Json(datos)),
    )


cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()


def limpiar():
    cur.execute(
        """DELETE FROM logs WHERE entity_type='oportunidad' AND entity_id IN
             (SELECT o.id FROM oportunidades o JOIN leads l ON l.id=o.lead_id
              JOIN clientes c ON c.id=l.cliente_id WHERE c.email=%s);""",
        (EMAIL,),
    )
    cur.execute(
        """DELETE FROM presupuestos WHERE oportunidad_id IN
             (SELECT o.id FROM oportunidades o JOIN leads l ON l.id=o.lead_id
              JOIN clientes c ON c.id=l.cliente_id WHERE c.email=%s);""",
        (EMAIL,),
    )
    cur.execute(
        """DELETE FROM oportunidades WHERE lead_id IN
             (SELECT l.id FROM leads l JOIN clientes c ON c.id=l.cliente_id WHERE c.email=%s);""",
        (EMAIL,),
    )
    cur.execute(
        "DELETE FROM leads WHERE cliente_id IN (SELECT id FROM clientes WHERE email=%s);",
        (EMAIL,),
    )
    cur.execute("DELETE FROM clientes WHERE email=%s;", (EMAIL,))
    # Logs de error de la prueba C (de esta ejecución o de una anterior
    # interrumpida), reconocidos por la marca propia. Ver MARCA_M2.
    # "%%" es un % literal dentro de un texto con parámetros de psycopg2.
    cur.execute(
        """DELETE FROM logs WHERE entity_type='sistema' AND accion='error_no_controlado'
             AND detalle->>'mensaje' LIKE '%%' || %s || '%%';""",
        (MARCA_M2,),
    )
    cn.commit()



# Limpieza AL EMPEZAR, no solo al terminar: si una ejecucion anterior se
# corto a mitad (por ejemplo, por un fallo de una asercion), pudo dejar
# el cliente de prueba escrito, y el INSERT de mas abajo chocaria con el
# UNIQUE de clientes.email. Con esto el script es repetible siempre.
limpiar()



try:
    print("=" * 78)
    print("0 - La restricción existe en la base de datos")
    print("=" * 78)
    cur.execute(
        """
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = 'leads'::regclass AND conname = %s;
        """,
        (NOMBRE_CHECK,),
    )
    fila = cur.fetchone()
    print(f"  {NOMBRE_CHECK}: {fila[0] if fila else '(no existe)'}")
    comprobar("chk_leads_m2_rango existe", fila is not None)
    comprobar("el tope del CHECK coincide con MAX_M2_LEAD del código",
              fila is not None and str(MAX_M2_LEAD) in fila[0],
              f"(MAX_M2_LEAD={MAX_M2_LEAD})")

    # Fila de preparación: un cliente del que colgar los leads de prueba.
    # Va sin savepoint: si fallara, no tendría sentido continuar.
    cur.execute(
        "INSERT INTO clientes (nombre, email, telefono) VALUES (%s,%s,%s) RETURNING id;",
        ("Prueba m2", EMAIL, "600000000"),
    )
    cliente_id = cur.fetchone()[0]

    print("\n" + "=" * 78)
    print("A a D - Comportamiento del CHECK (cada caso en su SAVEPOINT)")
    print("=" * 78)

    # --- A. por encima del tope -> rechazado ---
    error, _ = insertar_lead(cur, cliente_id, 501)
    comprobar("A  m2=501 rechazado por el CHECK",
              isinstance(error, psycopg2.errors.CheckViolation)
              and getattr(error.diag, "constraint_name", None) == NOMBRE_CHECK,
              f"({type(error).__name__ if error else 'sin error'})")
    error, _ = insertar_lead(cur, cliente_id, 60000)
    comprobar("A  m2=60000 (el caso que reventaba al calcular) rechazado",
              isinstance(error, psycopg2.errors.CheckViolation))

    # --- B. el límite, por los dos lados ---
    error, filas = insertar_lead(cur, cliente_id, 500)
    comprobar("B  m2=500 exacto ACEPTADO (le es inclusivo)",
              error is None and filas is not None,
              f"({type(error).__name__ if error else 'insertado id=' + str(filas[0][0])})")
    error, _ = insertar_lead(cur, cliente_id, 500.01)
    comprobar("B  m2=500.01 rechazado",
              isinstance(error, psycopg2.errors.CheckViolation),
              f"({type(error).__name__ if error else 'sin error'})")
    error, _ = insertar_lead(cur, cliente_id, 0)
    comprobar("B  m2=0 rechazado (el CHECK exige > 0)",
              isinstance(error, psycopg2.errors.CheckViolation),
              f"({type(error).__name__ if error else 'sin error'})")
    error, filas = insertar_lead(cur, cliente_id, 0.01)
    comprobar("B  m2=0.01 aceptado (el mínimo por abajo)", error is None)

    # --- C. valor no numérico -> DataError, no CheckViolation ---
    error, _ = insertar_lead(cur, cliente_id, "abc")
    # InvalidTextRepresentation es la subclase concreta de DataError que
    # usa Postgres cuando un texto no se puede convertir al tipo pedido.
    comprobar("C  m2='abc' rechazado como DataError, NO como CheckViolation",
              isinstance(error, psycopg2.DataError)
              and not isinstance(error, psycopg2.errors.CheckViolation),
              f"({type(error).__name__}: {str(error).strip().splitlines()[0]})")

    # --- D. clave ausente -> el CHECK NO lo bloquea ---
    error, filas = insertar_lead(cur, cliente_id, None)
    comprobar("D  sin la clave 'm2' el CHECK NO bloquea (NULL pasa un CHECK)",
              error is None and filas is not None,
              "(documentado: el CHECK garantiza el RANGO, no la PRESENCIA)")

finally:
    # Deshace todo: cliente y leads de prueba nunca llegan a existir.
    cn.rollback()

# ======================================================================
# Parte HTTP: hace falta el servidor de verdad.
# ======================================================================
servidor = uvicorn.Server(
    uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="critical")
)
hilo = threading.Thread(target=servidor.run, daemon=True)
print("\n" + "=" * 78)
print("ARRANQUE DEL SERVIDOR (uvicorn.Server, sin --reload)")
print("=" * 78)
hilo.start()
inicio = time.time()
while time.time() - inicio < 30:
    try:
        if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
            break
    except requests.exceptions.RequestException:
        time.sleep(0.25)
else:
    print("  El servidor no respondió en 30 s")
    sys.exit(1)
print(f"  Servidor arriba en {time.time() - inicio:.2f} s")


def cuerpo_lead(m2, sufijo=""):
    return {
        "nombre": "Prueba m2", "email": EMAIL, "telefono": "600000000",
        "tipo_reforma": "bano", "m2": m2, "nivel_acabados": "medio",
        "incluye_cambios_estructurales": False, "lead_token": f"tok{sufijo}",
    }


try:
    limpiar()
    print("\n" + "=" * 78)
    print("E - La puerta HTTP: Pydantic rechaza antes de tocar la base de datos")
    print("=" * 78)
    cur.execute("SELECT count(*) FROM leads;")
    leads_antes = cur.fetchone()[0]
    cn.commit()
    for etiqueta, valor, esperado in [
        ("m2=501", 501, 422),
        ("m2=60000", 60000, 422),
        ("m2=500.01", 500.01, 422),
        ("m2=0", 0, 422),
        ("m2=-5", -5, 422),
        ("m2=500 (el límite)", 500, 201),
        ("m2=8.7", 8.7, 201),
    ]:
        r = requests.post(f"{BASE}/leads", headers=AUTH, json=cuerpo_lead(valor, etiqueta), timeout=20)
        detalle = ""
        if r.status_code == 422:
            # El mensaje de Pydantic debe nombrar el campo, para que quien
            # integre sepa qué corregir sin adivinar.
            detalle = f"({r.json()['detail'][0]['loc']} {r.json()['detail'][0]['msg']})"
        comprobar(f"E  POST /leads con {etiqueta} -> {esperado}",
                  r.status_code == esperado, detalle or f"({r.status_code})")
    cur.execute("SELECT count(*) FROM leads;")
    leads_despues = cur.fetchone()[0]
    cn.commit()
    comprobar("E  los rechazados no escribieron nada (solo entraron los 2 válidos)",
              leads_despues == leads_antes + 2, f"({leads_antes} -> {leads_despues})")

    print("\n" + "=" * 78)
    print("C (HTTP) - Un m2 corrupto en la base de datos acaba en el manejador global")
    print("=" * 78)
    # HALLAZGO de esta verificación: con el CHECK puesto, m2 ya NO se
    # puede corromper por SQL. El CHECK se evalúa también en los UPDATE,
    # y poner "abc" hace fallar el cast antes de escribir nada. Se
    # comprueba aquí, porque es una protección extra que no estaba
    # prevista en el plan.
    error, _ = ejecutar_en_savepoint(
        cur,
        """UPDATE leads SET datos_estructurados = jsonb_set(datos_estructurados, '{m2}', '"abc"')
           WHERE cliente_id = (SELECT id FROM clientes WHERE email=%s);""",
        (EMAIL,),
    )
    comprobar("C  con el CHECK, ni siquiera se puede corromper m2 por SQL",
              isinstance(error, psycopg2.DataError),
              f"({type(error).__name__ if error else 'sin error: se pudo corromper'})")

    # Entonces, ¿cómo se comprueba que un cast fallido dentro de
    # estimate_service acaba en el manejador global? Con OTRO campo del
    # mismo JSON que el servicio también convierte y que NO tiene CHECK:
    # incluye_cambios_estructurales, que se lee con ::boolean. Es
    # exactamente el mismo camino de código (un cast que falla dentro de
    # services/, sin que nadie lo capture), solo que por un campo que sí
    # se puede corromper. Lo que se verifica es el MANEJADOR, no el campo.
    cur.execute(
        """UPDATE leads
           SET datos_estructurados = jsonb_set(datos_estructurados,
                                               '{incluye_cambios_estructurales}', to_jsonb(%s::text))
           WHERE cliente_id = (SELECT id FROM clientes WHERE email=%s) RETURNING id;""",
        # to_jsonb(texto) convierte el texto en un valor JSON de tipo
        # cadena ("m2check-..."), igual que antes lo era '"abc"'.
        (VALOR_CORRUPTO, EMAIL),
    )
    print(f"  leads corrompidos a mano (incluye_cambios_estructurales): {cur.rowcount}")
    cur.execute(
        """SELECT o.id FROM oportunidades o JOIN leads l ON l.id=o.lead_id
           JOIN clientes c ON c.id=l.cliente_id WHERE c.email=%s LIMIT 1;""",
        (EMAIL,),
    )
    oportunidad_id = cur.fetchone()[0]
    # Id más alto de logs antes de la petición: el log de la prueba C
    # tendrá un id mayor. Se combina con VALOR_CORRUPTO al leer, para que
    # un error real que llegue a la vez no cuente como el de la prueba.
    cur.execute("SELECT COALESCE(max(id), 0) FROM logs;")
    id_base = cur.fetchone()[0]
    cn.commit()

    r = requests.post(f"{BASE}/calculate-estimate", headers=AUTH,
                      json={"oportunidad_id": oportunidad_id}, timeout=30)
    print(f"  POST /calculate-estimate -> {r.status_code} {r.text}")
    comprobar("C  el cliente recibe 500 genérico", r.status_code == 500)
    comprobar("C  el cuerpo no filtra traceback ni el error interno",
              r.json() == {"status": "error", "detail": "Error interno"}
              and VALOR_CORRUPTO not in r.text and "Traceback" not in r.text)
    time.sleep(0.5)  # margen para el INSERT del manejador
    cur.execute(
        """SELECT count(*), max(detalle->>'tipo'), max(detalle->>'ruta')
           FROM logs WHERE id > %s AND entity_type='sistema' AND accion='error_no_controlado'
             AND detalle->>'mensaje' LIKE '%%' || %s || '%%';""",
        (id_base, VALOR_CORRUPTO),
    )
    n_logs, tipo, ruta = cur.fetchone()
    cn.commit()
    comprobar("C  el manejador global dejó la fila en logs",
              n_logs == 1, f"(filas propias con {VALOR_CORRUPTO}: {n_logs})")
    comprobar("C  el log dice qué error fue y en qué ruta",
              tipo is not None and "DataError" in tipo or tipo == "InvalidTextRepresentation",
              f"(tipo={tipo}, ruta={ruta})")

finally:
    limpiar()
    print("\n  Datos de prueba eliminados.")
    servidor.should_exit = True
    hilo.join(timeout=15)
    cur.close()
    cn.close()

print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{ok + len(fallos)} correctas")
for f in fallos:
    print(f"  FALLO: {f}")
print("=" * 78)
sys.exit(0 if not fallos else 1)
