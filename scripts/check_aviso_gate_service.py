"""
Verificación SIN servidor HTTP de app/services/aviso_gate_service.py (plan:
docs/Plan_Endpoint_Aviso_Gate.txt, sección 4.3).

Qué prueba:
  1. deducir_sin_iva (función pura): el ejemplo de la Adenda, IVA 0, None
     y un barrido de TODOS los importes sin IVA de 0,01 a 3000,00 con IVA
     21, 10, 4 y 0: sin_iva -> con_iva (calculado como estimate_service)
     -> sin_iva debe dar siempre el original.
  2. a_madrid (función pura): fechas FIJAS a los dos lados del cambio de
     hora de 2026 (23/10 -> +02:00, 26/10 -> +01:00). Aquí no caducan: la
     función no exige que la fecha sea futura.
  3. obtener_aviso, llamada directamente (con el pool del backend), sobre
     datos PROPIOS creados por SQL: la regla del contacto (P1) con y sin
     la clave 'contacto', y los tres rechazos (404 y los dos 409).

Marca propia: emails svc-aviso-<8 caracteres>@example.com. Al terminar se
borra SOLO lo propio y se comprueba que no queda nada.

Una comprobación que ESPERA una excepción y no la recibe es FALLO
explícito ("la excepción se ha tragado"), en la rama else del try (regla
de CLAUDE.md, caso D16).
"""

# sys: para tocar sys.path, la salida estándar y el código de salida.
import sys
# uuid: para que cada ejecución tenga sus propios emails de prueba.
import uuid
# datetime y timezone: para construir instantes fijos en UTC.
from datetime import datetime, timezone
# Decimal: los importes, siempre exactos.
from decimal import Decimal
# Path: para calcular la raíz del repositorio.
from pathlib import Path

# La raíz del repositorio es la carpeta padre de scripts/; se añade a
# sys.path para que "import app..." funcione.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
# Tildes bien en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: para crear y borrar los datos propios.
import psycopg2
# Json: diccionario de Python -> valor JSONB.
from psycopg2.extras import Json

# La cadena de conexión (nunca se imprime).
from app.config import DATABASE_URL
# El pool del backend: obtener_aviso usa get_db_connection(), que lo necesita.
from app.db import connection as db
# Lo que se prueba.
from app.services.aviso_gate_service import (
    EstadoNoPermiteAviso,
    OportunidadNoEncontrada,
    a_madrid,
    deducir_sin_iva,
    obtener_aviso,
)
# El MISMO redondeo del cálculo, para construir el importe con IVA de cada
# caso del barrido exactamente como lo haría estimate_service.
from app.services.estimate_service import CIEN, redondear

# Patrón SQL (LIKE) de los clientes de ESTE script.
PATRON_EMAIL = "svc-aviso-%@example.com"

# Contador de correctas y lista de fallos.
ok, fallos = 0, []


def comprobar(titulo, condicion, detalle=""):
    """Anota y enseña una comprobación: [OK] o [FALLO]."""
    global ok
    if condicion:
        ok += 1
        print(f"  [OK]    {titulo}  {detalle}")
    else:
        fallos.append(titulo)
        print(f"  [FALLO] {titulo}  {detalle}")


# ======================================================================
print("1. deducir_sin_iva")
# ======================================================================
# El ejemplo de la Adenda (1.1): 7500,00 sin IVA, al 21 %, son 9075,00.
comprobar("9075.00 al 21 % -> 7500.00", deducir_sin_iva(Decimal("9075.00"), Decimal("21.00")) == Decimal("7500.00"),
          f"({deducir_sin_iva(Decimal('9075.00'), Decimal('21.00'))})")
# Con IVA 0, el importe sin IVA es el mismo.
comprobar("IVA 0: 1234.56 -> 1234.56", deducir_sin_iva(Decimal("1234.56"), Decimal("0")) == Decimal("1234.56"))
# Sin importe con IVA no se inventa nada.
comprobar("None -> None", deducir_sin_iva(None, Decimal("21.00")) is None)
# El barrido: para cada IVA, todos los céntimos de 0,01 a 3000,00.
for iva in (Decimal("21.00"), Decimal("10.00"), Decimal("4.00"), Decimal("0.00")):
    # Contador de casos en los que la vuelta no da el original, y de casos
    # "en la frontera" (sin_iva × k termina exactamente en 5 milésimas,
    # donde un redondeo mal hecho fallaría).
    distintos, frontera = 0, 0
    for centimos in range(1, 300_001):
        # El importe sin IVA de este caso: centimos / 100, como Decimal exacto.
        sin_iva = Decimal(centimos) / CIEN
        # El producto exacto, antes de redondear.
        exacto = sin_iva * (1 + iva / CIEN)
        # ¿Está en la frontera? (la tercera y cuarta cifras decimales son 50)
        if exacto * 1000 % 10 == 5 and exacto * 10000 % 10 == 0:
            frontera += 1
        # El importe con IVA, redondeado como estimate_service (aplicar_iva).
        con_iva = redondear(exacto)
        # La vuelta tiene que dar exactamente el original.
        if deducir_sin_iva(con_iva, iva) != sin_iva:
            distintos += 1
    comprobar(f"IVA {iva}: 300000 importes, ida y vuelta exacta", distintos == 0,
              f"({distintos} distintos; {frontera} casos en la frontera de redondeo)")

# ======================================================================
print("\n2. a_madrid (fechas fijas a los dos lados del cambio de hora)")
# ======================================================================
# 23/10/2026 06:30 UTC = 08:30 en Madrid, todavía en horario de verano (+02:00).
verano = a_madrid(datetime(2026, 10, 23, 6, 30, tzinfo=timezone.utc))
comprobar("23/10/2026 06:30 UTC -> 2026-10-23T08:30:00+02:00",
          verano.isoformat() == "2026-10-23T08:30:00+02:00", f"({verano.isoformat()})")
# 26/10/2026 07:30 UTC = 08:30 en Madrid, ya en horario de invierno (+01:00).
invierno = a_madrid(datetime(2026, 10, 26, 7, 30, tzinfo=timezone.utc))
comprobar("26/10/2026 07:30 UTC -> 2026-10-26T08:30:00+01:00",
          invierno.isoformat() == "2026-10-26T08:30:00+01:00", f"({invierno.isoformat()})")
# Sin fecha, nada.
comprobar("None -> None", a_madrid(None) is None)

# ======================================================================
print("\n3. obtener_aviso con datos propios (sin servidor)")
# ======================================================================
# Conexión propia para crear y borrar los datos de prueba.
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()

# Qué es propio: clientes con la marca -> sus leads -> sus oportunidades.
SQL_CLIENTES = "SELECT id FROM clientes WHERE email LIKE %(patron)s"
SQL_LEADS = f"SELECT id FROM leads WHERE cliente_id IN ({SQL_CLIENTES})"
SQL_OPS = f"SELECT id FROM oportunidades WHERE lead_id IN ({SQL_LEADS})"
# Tablas y condición de lo propio, en el orden de borrado (claves foráneas).
BORRADO = [
    ("logs", f"entity_type = 'oportunidad' AND entity_id IN ({SQL_OPS})"),
    ("presupuestos", f"oportunidad_id IN ({SQL_OPS})"),
    ("oportunidades", f"id IN ({SQL_OPS})"),
    ("leads", f"id IN ({SQL_LEADS})"),
    ("clientes", f"id IN ({SQL_CLIENTES})"),
]


def limpiar():
    """Borra SOLO lo propio, en el orden de las claves foráneas."""
    for tabla, condicion in BORRADO:
        # SQL: borra las filas propias de esa tabla.
        cur.execute(f"DELETE FROM {tabla} WHERE {condicion};", {"patron": PATRON_EMAIL})
    cn.commit()


def caso(contacto, con_presupuesto=True, gate=True):
    """Crea cliente + lead + oportunidad (y presupuesto) propios por SQL.
    contacto: el diccionario de la clave 'contacto', o None para no ponerla.
    Devuelve el id de la oportunidad."""
    email = f"svc-aviso-{uuid.uuid4().hex[:8]}@example.com"
    # SQL: el cliente, con una ficha que NUNCA debe salir en el aviso.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES ('Ficha Servicio', %s, '699999999') "
                "RETURNING id;", (email,))
    cliente = cur.fetchone()[0]
    # Los datos de la reforma; 'contacto' solo si se pidió.
    datos = {"tipo_reforma": "bano", "nivel_acabados": "medio", "m2": 6, "incluye_cambios_estructurales": True}
    if contacto is not None:
        datos["contacto"] = contacto
    # SQL: el lead, sin fotos.
    cur.execute("INSERT INTO leads (cliente_id, canal, fotos_urls, datos_estructurados) "
                "VALUES (%s, 'check_aviso_svc', '[]'::jsonb, %s) RETURNING id;", (cliente, Json(datos)))
    lead = cur.fetchone()[0]
    # SQL: la oportunidad, en el estado de un caso con Gate.
    cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma, estado) VALUES (%s, 'bano', %s) RETURNING id;",
                (lead, "pendiente_aprobacion" if gate else "presupuesto_enviado"))
    op = cur.fetchone()[0]
    # SQL: el presupuesto, con o sin Gate, si se pidió.
    if con_presupuesto:
        cur.execute("INSERT INTO presupuestos (oportunidad_id, importe_min_con_iva, importe_max_con_iva, "
                    "requiere_aprobacion, motivo_gate, iva_pct_aplicado) VALUES (%s, 9075.00, 10436.25, %s, %s, 21);",
                    (op, gate, "cambios_estructurales" if gate else None))
    cn.commit()
    return op


def debe_rechazar(titulo, op, clase, motivo):
    """obtener_aviso(op) debe lanzar esa clase con ese motivo. Si no lanza
    nada: FALLO explícito, la excepción se ha tragado."""
    try:
        obtener_aviso(op)
    except clase as error:
        comprobar(titulo, error.motivo == motivo, f"({type(error).__name__}, motivo={error.motivo})")
    # Cualquier otra excepción también es un fallo (no la esperada).
    except Exception as error:
        comprobar(titulo, False, f"(otra excepción: {type(error).__name__}: {error})")
    # else del try: solo se ejecuta si NO hubo ninguna excepción.
    else:
        comprobar(titulo, False, "(sin excepción: la excepción se ha tragado)")


# El pool del backend (lo usa obtener_aviso). try/finally: se cierra y se
# limpia pase lo que pase.
db.init_pool()
try:
    # Restos de una ejecución anterior cortada.
    limpiar()
    contacto = {"nombre": "Solicitud Servicio", "email": "svc@example.com", "telefono": "611000000"}
    op_con = caso(contacto)
    op_sin = caso(None)
    op_sin_gate = caso(contacto, gate=False)
    op_sin_presupuesto = caso(contacto, con_presupuesto=False)

    aviso_con = obtener_aviso(op_con)
    comprobar("lead con 'contacto' -> sus datos y origen 'solicitud'",
              aviso_con.contacto.model_dump(mode="json") == {**contacto, "origen": "solicitud"},
              f"({aviso_con.contacto.model_dump(mode='json')})")
    aviso_sin = obtener_aviso(op_sin)
    comprobar("lead sin 'contacto' -> tres None y origen 'no_disponible'",
              aviso_sin.contacto.model_dump(mode="json")
              == {"nombre": None, "email": None, "telefono": None, "origen": "no_disponible"},
              f"({aviso_sin.contacto.model_dump(mode='json')})")
    comprobar("la ficha ('Ficha Servicio', '699999999') no aparece en ninguno de los dos avisos",
              all("Ficha Servicio" not in a.model_dump_json() and "699999999" not in a.model_dump_json()
                  for a in (aviso_con, aviso_sin)))
    comprobar("importes sin IVA deducidos: 7500.00 y 8625.00",
              (aviso_con.presupuesto.importe_min_sin_iva, aviso_con.presupuesto.importe_max_sin_iva)
              == (Decimal("7500.00"), Decimal("8625.00")))

    # SQL: un id que no existe (el máximo más un millón).
    cur.execute("SELECT max(id) FROM oportunidades;")
    inexistente = cur.fetchone()[0] + 1_000_000
    cn.commit()
    debe_rechazar("id inexistente -> OportunidadNoEncontrada (oportunidad_no_encontrada)", inexistente,
                  OportunidadNoEncontrada, "oportunidad_no_encontrada")
    debe_rechazar("sin presupuesto -> EstadoNoPermiteAviso (sin_presupuesto)", op_sin_presupuesto,
                  EstadoNoPermiteAviso, "sin_presupuesto")
    debe_rechazar("presupuesto sin Gate -> EstadoNoPermiteAviso (sin_gate)", op_sin_gate,
                  EstadoNoPermiteAviso, "sin_gate")
finally:
    # Se cierra el pool, se limpia lo propio y se comprueba que no queda nada.
    db.close_pool()
    limpiar()
    # SQL: clientes con la marca que queden (deben ser 0).
    cur.execute(f"SELECT count(*) FROM clientes WHERE email LIKE %(patron)s;", {"patron": PATRON_EMAIL})
    restos = cur.fetchone()[0]
    cn.commit()
    cn.close()
    comprobar("no queda ningún dato de prueba", restos == 0, f"({restos})")

# Resumen y código de salida (1 si algo falló).
print(f"\nRESULTADO: {ok}/{ok + len(fallos)} correctas")
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
