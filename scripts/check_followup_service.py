"""
Verificación SIN servidor de POST /create-followup-task (plan:
docs/Plan_Endpoint_Create_Followup_Task.txt, sección 6.1).

  S0. La conexión de prueba rechaza commit() (CommitProhibido): S1, S3 y S6
      usan ConexionSinCommit (incidente de la N12, 2026-10-08).
  S1. LÍMITE EXACTO del plazo, al microsegundo, contra la base de datos real
      pero dentro de UNA transacción que termina en ROLLBACK: no deja nada.
  S2. problema_horas (la validación de la regla de 48 h, compartida con el
      listado) con valores sueltos.
  S3. Los rechazos del servicio por el camino real (en la misma transacción
      con ROLLBACK): 404, y 503 con la clave cambiada EN MEMORIA y con
      valores fuera de rango inyectados. Con la rama else que marca FALLO
      si la excepción esperada NO sale (regla de CLAUDE.md, caso D16).
  S4. Fuente única, con ast.parse: ni followup_service.py ni
      listado_llamadas_service.py contienen fragmentos de la condición, y
      los dos la importan de condicion_seguimiento.py.
  S5. Los seis criterios, en el orden de 2.9 del plan.
  S6. Regla fuera de rango con el VALOR REAL en la tabla (UPDATE dentro de una
      transacción con ROLLBACK): 8761 y 99999999 -> 503, y la regla intacta.
  S7. primer_criterio_incumplido con listas de valores sueltas.

Marca propia: emails check-followup-svc-<8>@example.com, siempre dentro de
la transacción que se deshace. Los secretos no se usan.
"""

# ----------------------------------------------------------------------
# Imports
# ----------------------------------------------------------------------
# ast: para leer los servicios como árbol y buscar textos dentro (S4).
import ast
# sys: sys.path, la salida y el código de salida.
import sys
# uuid: marca aleatoria de esta ejecución.
import uuid
# timedelta: duraciones (48 h, 1 microsegundo).
from datetime import timedelta
# Decimal: los valores de reglas_negocio llegan como Decimal.
from decimal import Decimal
# Path: rutas de archivos.
from pathlib import Path

# La raíz del repositorio es la carpeta padre de scripts/. Se pone la
# primera en sys.path para que "import app..." encuentre el paquete.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
# La consola de Windows no usa UTF-8 por defecto; así las tildes salen bien.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: conexión directa propia; Json convierte un dict en JSONB.
import psycopg2
from psycopg2.extras import Json

# La URL de la base de datos (nunca se imprime).
from app.config import DATABASE_URL
# El esquema de entrada del endpoint.
from app.schemas.followup_tasks import FollowupTaskCreate
# El módulo compartido y los dos servicios.
from app.services import condicion_seguimiento as condicion
from app.services import followup_service as servicio
# El rechazo de configuración (503), compartido.
from app.services.reglas_visita import ConfiguracionIncompleta

# ----------------------------------------------------------------------
# Contadores
# ----------------------------------------------------------------------
# Comprobaciones correctas y títulos de las que fallan.
ok = 0
fallos = []


def comprobar(titulo, condicion_ok, detalle=""):
    """Imprime [OK] o [FALLO] y lleva la cuenta."""
    # global: el contador es el de fuera de la función.
    global ok
    # Correcta: suma; fallida: se apunta su título.
    if condicion_ok:
        ok += 1
    else:
        fallos.append(titulo)
    # La línea del resultado, con el detalle si lo hay.
    print(f"  [{'OK' if condicion_ok else 'FALLO'}] {titulo} {detalle}")


# Los seis motivos, EN ORDEN, copiados del PLAN (2.9), no del código.
ORDEN_PLAN = ["sin_presupuesto", "con_gate", "estado_no_permitido",
              "contacto_registrado", "visita_existente", "plazo_no_cumplido"]


# ----------------------------------------------------------------------
# Conexión de prueba SIN commit (incidente de la N12, 2026-10-08)
# ----------------------------------------------------------------------
# S1, S3 y S6 escriben en tablas reales dentro de una transacción que se
# deshace al final, y pasan ESA conexión al código bajo prueba. Si el código
# (o una versión rota de él) llamara a commit(), confirmaría también los
# cambios de la prueba: el 2026-10-08, la versión rota N12 dejó así
# horas_seguimiento_presupuesto en 8761 en la tabla real. Con esta clase, un
# commit() no confirma nada: lanza CommitProhibido, que la prueba cuenta
# como FALLO, y el ROLLBACK del final lo deshace todo.
class CommitProhibido(Exception):
    """Alguien ha llamado a commit() en una conexión de prueba."""


class ConexionSinCommit(psycopg2.extensions.connection):
    """Una conexión de psycopg2 normal, salvo commit(), que lanza un error.
    rollback() funciona como siempre (es lo que usa la prueba)."""

    def commit(self):
        # No se confirma NADA: se lanza el error y la transacción sigue
        # abierta, para que el ROLLBACK de la prueba la deshaga.
        raise CommitProhibido("commit() en una conexión de prueba: la prueba termina siempre en ROLLBACK")


# Autoprueba de la defensa: commit() tiene que lanzar CommitProhibido.
# connection_factory: psycopg2 crea la conexión con esta clase.
conexion_prueba = psycopg2.connect(DATABASE_URL, connection_factory=ConexionSinCommit)
# try/except/else: sin la excepción, FALLO explícito (regla de CLAUDE.md).
try:
    conexion_prueba.commit()
# La esperada.
except CommitProhibido:
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", True)
# else: commit() no lanzó nada -> la defensa no existe.
else:
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", False,
              "(sin error: la excepción se ha tragado)")
# Se cierra (no había nada que deshacer).
finally:
    conexion_prueba.rollback()
    conexion_prueba.close()

# ======================================================================
print("S1. Límite EXACTO del plazo, en una transacción que termina en ROLLBACK")
# ======================================================================
# Conexión directa propia y su cursor: todo lo de S1 y S3 es UNA
# transacción, que el finally deshace. ConexionSinCommit: ni el código bajo
# prueba ni una versión rota de él pueden confirmar nada (ver arriba).
cn = psycopg2.connect(DATABASE_URL, connection_factory=ConexionSinCommit)
cur = cn.cursor()
# 8 caracteres aleatorios para el email propio de esta ejecución.
marca = uuid.uuid4().hex[:8]
# try/finally: pase lo que pase dentro, el finally hace el ROLLBACK.
try:
    # SQL: el reloj de ESTA transacción. Dentro de ella now() no cambia,
    # así que el límite es exacto al microsegundo.
    cur.execute("SELECT now();")
    ahora = cur.fetchone()[0]
    # SQL: las horas de la regla, tal como están en la tabla (48).
    cur.execute("SELECT valor FROM reglas_negocio WHERE clave = %s;", (condicion.CLAVE_SEGUIMIENTO,))
    horas = int(cur.fetchone()[0])
    print(f"  regla {condicion.CLAVE_SEGUIMIENTO} = {horas} h")

    # SQL: el cliente propio (email con la marca); RETURNING id da su id.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s) RETURNING id;",
                ("Servicio Seguimiento", f"check-followup-svc-{marca}@example.com", "600000010"))
    cliente = cur.fetchone()[0]
    # SQL: un lead propio (sus oportunidades cuelgan de él). Json(...) da el
    # JSONB de datos_estructurados (con m2 dentro del rango del CHECK).
    cur.execute("INSERT INTO leads (cliente_id, canal, fotos_urls, datos_estructurados) "
                "VALUES (%s, 'check_followup', '[]'::jsonb, %s) RETURNING id;",
                (cliente, Json({"tipo_reforma": "bano", "nivel_acabados": "medio", "m2": 6,
                                "incluye_cambios_estructurales": False})))
    lead = cur.fetchone()[0]

    # Ayuda: crea una oportunidad propia lista para seguimiento (salvo el plazo).
    def oportunidad(presupuesto_creado):
        """Oportunidad propia en 'presupuesto_enviado' con su presupuesto
        SIN Gate y created_at FIJO. Devuelve su id."""
        # SQL: la oportunidad; RETURNING id da su id.
        cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma, estado) "
                    "VALUES (%s, 'bano', 'presupuesto_enviado') RETURNING id;", (lead,))
        op = cur.fetchone()[0]
        # SQL: su presupuesto sin importes (no hacen falta), sin Gate, con la fecha fijada.
        cur.execute("INSERT INTO presupuestos (oportunidad_id, requiere_aprobacion, created_at) "
                    "VALUES (%s, false, %s);", (op, presupuesto_creado))
        return op

    # Ayuda: una llamada al servicio dentro de su propio SAVEPOINT.
    def intentar(op):
        """Llama al servicio con el cursor de ESTA transacción, dentro de su
        propio SAVEPOINT: si lanza una excepción, se deshace solo lo de esta
        llamada (ROLLBACK TO SAVEPOINT) y la transacción sigue viva (gotcha
        de CLAUDE.md). Devuelve (respuesta, None) o (None, excepción)."""
        # SQL: marca un punto de vuelta dentro de la transacción.
        cur.execute("SAVEPOINT intento;")
        # La llamada real al servicio.
        try:
            respuesta = servicio.abrir_seguimiento_con_cursor(
                cur, FollowupTaskCreate(oportunidad_id=op, motivo="sin_respuesta_visita"))
        # Cualquier excepción: se vuelve al punto marcado y se devuelve.
        except Exception as error:
            # try/except: si el punto ya no existe (una versión rota que
            # hizo commit, N12), se deshace todo con cn.rollback().
            try:
                # SQL: deshace solo lo de esta llamada (vuelve al punto marcado).
                cur.execute("ROLLBACK TO SAVEPOINT intento;")
            except psycopg2.Error:
                cn.rollback()
            return None, error
        # SQL: sin excepción, el punto ya no hace falta (los cambios siguen).
        cur.execute("RELEASE SAVEPOINT intento;")
        return respuesta, None

    # Ayuda: exige una excepción concreta (con la rama de "se ha tragado").
    def esperar_rechazo(titulo, op, clase, comprobacion, detalle):
        """Llama al servicio y exige la excepción de esa clase. Sin
        excepción: FALLO explícito (regla de CLAUDE.md, caso D16). Con otra
        excepción: FALLO con su nombre (una versión rota da un recuento, no
        un script parado)."""
        # La llamada, en su SAVEPOINT.
        _, error = intentar(op)
        # La esperada: se comprueba lo que pida el caso.
        if isinstance(error, clase):
            comprobar(titulo, comprobacion(error), detalle(error))
        # Ninguna: la excepción se ha tragado.
        elif error is None:
            comprobar(titulo, False, "(sin error: la excepción se ha tragado)")
        # Otra distinta: se enseña su clase y su texto.
        else:
            comprobar(titulo, False, f"(salió {type(error).__name__}: {error})")

    # Un microsegundo: la unidad más pequeña de timestamptz.
    us = timedelta(microseconds=1)
    # Exactamente las horas de la regla (NO cumple: límite estricto) y un
    # microsegundo más (SÍ cumple).
    op_exacto = oportunidad(ahora - timedelta(hours=horas))
    op_dentro = oportunidad(ahora - timedelta(hours=horas) - us)

    # La exacta: tiene que salir SeguimientoNoPermitido plazo_no_cumplido.
    esperar_rechazo(f"presupuesto de {horas} h EXACTAS -> 409 plazo_no_cumplido", op_exacto,
                    servicio.SeguimientoNoPermitido, lambda e: e.motivo == "plazo_no_cumplido",
                    lambda e: f"({e.motivo})")

    # La de un microsegundo más: se abre el seguimiento (dentro de la
    # transacción que luego se deshace).
    respuesta, error = intentar(op_dentro)
    comprobar(f"presupuesto de {horas} h + 1 µs -> se abre (creado=True)",
              respuesta is not None and respuesta.creado is True, f"({error!r})" if error else "")
    # SQL: el estado de esa oportunidad DENTRO de la transacción.
    cur.execute("SELECT estado FROM oportunidades WHERE id = %s;", (op_dentro,))
    comprobar("  ... y queda en 'seguimiento_pendiente'", cur.fetchone()[0] == "seguimiento_pendiente")

    # ==================================================================
    # S3 va aquí, antes que S2, porque usa la MISMA transacción con
    # ROLLBACK que S1 (los números son los del plan, 6.1).
    print("\nS3. Rechazos del servicio por el camino real (misma transacción)")
    # ==================================================================
    # SQL: un id que no existe (el máximo + 1000).
    cur.execute("SELECT COALESCE(max(id), 0) + 1000 FROM oportunidades;")
    inexistente = cur.fetchone()[0]
    # 404: tiene que salir OportunidadNoEncontrada.
    esperar_rechazo("id inexistente -> 404 oportunidad_no_encontrada", inexistente,
                    servicio.OportunidadNoEncontrada, lambda e: e.motivo == "oportunidad_no_encontrada",
                    lambda e: "")

    # 503 con la clave cambiada EN MEMORIA, sobre una oportunidad propia que
    # cumple todo (la de 48 h + 1 µs ya está abierta: se crea otra).
    op_503 = oportunidad(ahora - timedelta(hours=horas + 1))
    # El nombre real se guarda para restaurarlo; el falso es único.
    real = servicio.CLAVE_SEGUIMIENTO
    falsa = f"clave_falsa_{marca}"
    servicio.CLAVE_SEGUIMIENTO = falsa
    # try/finally: el nombre real vuelve aunque algo falle.
    try:
        # "faltan" tiene que nombrar la clave falsa.
        esperar_rechazo("clave cambiada en memoria -> 503 configuracion_incompleta", op_503,
                        ConfiguracionIncompleta,
                        lambda e: e.motivo == "configuracion_incompleta" and any(falsa in p for p in e.faltan),
                        lambda e: f"({e.faltan})")
    finally:
        servicio.CLAVE_SEGUIMIENTO = real

    # 503 con un valor fuera de rango INYECTADO: se envuelve problema_horas
    # en memoria para que reciba ese valor en lugar del leído, y llame a la
    # función real (la fila de la tabla no se toca).
    original = servicio.problema_horas
    # Un caso por valor: el primero por encima del máximo y el mayor entero
    # de NUMERIC(10,2) (el que daba "timestamp out of range" sin máximo).
    for valor in ("8761", "99999999"):
        # La envoltura: misma clave, valor inyectado.
        servicio.problema_horas = lambda clave, _leido, v=valor: original(clave, Decimal(v))
        # try/finally: la función real vuelve aunque algo falle.
        try:
            esperar_rechazo(f"regla = {valor} -> 503 configuracion_incompleta", op_503,
                            ConfiguracionIncompleta, lambda e: "8760" in e.faltan[0], lambda e: f"({e.faltan})")
        finally:
            servicio.problema_horas = original

    # SQL: el 503 no ha escrito nada en esa oportunidad (dentro de la
    # transacción, antes del ROLLBACK): estado y logs.
    cur.execute("SELECT o.estado, (SELECT count(*) FROM logs l WHERE l.entity_type = 'oportunidad' "
                "AND l.entity_id = o.id) FROM oportunidades o WHERE o.id = %s;", (op_503,))
    comprobar("los 503 no han escrito nada (estado y 0 logs)", cur.fetchone() == ("presupuesto_enviado", 0))
finally:
    # ROLLBACK: se deshace TODO lo de S1 y S3.
    cn.rollback()

# SQL: tras el ROLLBACK no queda ningún cliente con la marca.
cur.execute("SELECT count(*) FROM clientes WHERE email LIKE %s;", ("check-followup-svc-%@example.com",))
restos = cur.fetchone()[0]
# Se cierra la transacción de la consulta y la conexión de S1 y S3.
cn.rollback()
cn.close()
# Tiene que ser 0: el ROLLBACK lo ha deshecho todo.
comprobar("tras el ROLLBACK no queda ninguna fila propia", restos == 0, f"(clientes = {restos})")

# ======================================================================
print("\nS2. problema_horas (validación de UNA regla, compartida con el listado)")
# ======================================================================
# Clave de ejemplo para los textos de los problemas.
CLAVE = condicion.CLAVE_SEGUIMIENTO
# Válidos: 48 y el máximo, 8760.
for valor in ("48.00", "8760.00"):
    comprobar(f"{valor} -> válido", condicion.problema_horas(CLAVE, Decimal(valor)) is None)
# La fila no existe (None).
problema = condicion.problema_horas(CLAVE, None)
comprobar("sin fila -> 'no existe'", problema == f"{CLAVE}: no existe", f"({problema})")
# No válidos: 0, negativo, con decimales, el máximo + 1 y el mayor entero.
for valor in ("0", "-5", "24.50", "8761", "99999999"):
    problema = condicion.problema_horas(CLAVE, Decimal(valor))
    comprobar(f"{valor} -> problema que nombra la clave", problema is not None and problema.startswith(CLAVE),
              f"({problema})")

# ======================================================================
print("\nS4. Fuente única (ast.parse)")
# ======================================================================
# Fragmentos que SOLO pueden estar en condicion_seguimiento.py. No se
# busca make_interval: el apartado a) del listado lo usa con su propia
# regla (corrección del plan, 3.3).
FRAGMENTOS = ("fecha_ultimo_contacto IS NULL", "requiere_aprobacion", "NOT EXISTS")
# Los tres archivos: los dos que usan la condición y el compartido.
for nombre in ("followup_service.py", "listado_llamadas_service.py", "condicion_seguimiento.py"):
    # El árbol del archivo; los comentarios no forman parte de él.
    arbol = ast.parse((RAIZ_REPO / "app" / "services" / nombre).read_text(encoding="utf-8"))
    # Todos los textos del código (ast.Constant de tipo str).
    textos = [n.value for n in ast.walk(arbol) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    # Qué fragmentos aparecen en algún texto.
    encontrados = sorted({f for f in FRAGMENTOS for t in textos if f in t})
    # Los módulos de sus "from X import ...".
    origenes = [n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom)]
    print(f"  {nombre}: fragmentos {encontrados}")
    # El compartido los tiene todos; los otros dos, ninguno, y la importan.
    if nombre == "condicion_seguimiento.py":
        comprobar("el módulo compartido contiene los tres fragmentos", encontrados == sorted(FRAGMENTOS))
    else:
        comprobar(f"{nombre}: ningún fragmento de la condición", encontrados == [])
        comprobar(f"{nombre}: importa de app.services.condicion_seguimiento",
                  "app.services.condicion_seguimiento" in origenes)

# ======================================================================
print("\nS5. Los seis criterios, en el orden del plan (2.9)")
# ======================================================================
# Los motivos de la tupla, en su orden.
motivos = [m for m, _ in condicion.CRITERIOS_SEGUIMIENTO]
comprobar("seis criterios, en el orden de 2.9", motivos == ORDEN_PLAN, f"({motivos})")
# Cada 409 tiene su mensaje en el servicio (si no, el 409 daría un 500).
comprobar("cada motivo tiene su mensaje en MENSAJES_409", set(servicio.MENSAJES_409) == set(ORDEN_PLAN))

# ======================================================================
print("\nS6. Regla fuera de rango con el VALOR REAL de la tabla (UPDATE en una transacción con ROLLBACK)")
# ======================================================================
# Por qué hace falta (revisión del tutor, 2026-10-08): las pruebas que
# envuelven problema_horas solo cambian lo que se VALIDA; el plazo se
# calcula con el valor leído de la tabla. Aquí el valor fuera de rango está
# EN LA TABLA (dentro de una transacción que se deshace), así que es el
# mismo valor el que se valida y el que llegaría a la consulta.
# Conexión propia (autocommit apagado, el de por defecto): todo lo de S6 es
# UNA transacción, que el finally deshace SIEMPRE. ConexionSinCommit: el
# UPDATE de la regla real NUNCA se puede confirmar, ni siquiera si el código
# bajo prueba llama a commit() (incidente de la N12, ver arriba).
cn6 = psycopg2.connect(DATABASE_URL, connection_factory=ConexionSinCommit)
cur6 = cn6.cursor()
# SQL: el valor real de la regla ANTES de nada, para comprobar al final
# que sigue igual.
cur6.execute("SELECT valor FROM reglas_negocio WHERE clave = %s;", (condicion.CLAVE_SEGUIMIENTO,))
valor_real = cur6.fetchone()[0]
cn6.rollback()
print(f"  {condicion.CLAVE_SEGUIMIENTO} antes de la prueba: {valor_real}")
# Marca propia de esta parte.
marca6 = uuid.uuid4().hex[:8]
# try/finally: pase lo que pase dentro, el finally hace el ROLLBACK.
try:
    # SQL: si otra sesión tuviera bloqueada la fila de la regla, esperar
    # como mucho 5 s y fallar, en vez de quedarse colgado. LOCAL: solo en
    # esta transacción.
    cur6.execute("SET LOCAL lock_timeout = '5s';")
    # SQL: cliente propio (email con la marca); RETURNING id da su id.
    cur6.execute("INSERT INTO clientes (nombre, email, telefono) VALUES (%s, %s, %s) RETURNING id;",
                 ("Servicio Seguimiento S6", f"check-followup-svc-{marca6}@example.com", "600000012"))
    cliente6 = cur6.fetchone()[0]
    # SQL: su lead (con m2 dentro del rango del CHECK); RETURNING id da su id.
    cur6.execute("INSERT INTO leads (cliente_id, canal, fotos_urls, datos_estructurados) "
                 "VALUES (%s, 'check_followup', '[]'::jsonb, %s) RETURNING id;",
                 (cliente6, Json({"tipo_reforma": "bano", "nivel_acabados": "medio", "m2": 6,
                                  "incluye_cambios_estructurales": False})))
    lead6 = cur6.fetchone()[0]
    # Un valor por vuelta: el primero por encima del máximo y el mayor
    # entero de NUMERIC(10,2).
    for valor in ("8761", "99999999"):
        # SQL: una oportunidad propia NUEVA por vuelta (si la de una vuelta
        # se abriera, la siguiente daría 200 y no probaría nada).
        cur6.execute("INSERT INTO oportunidades (lead_id, tipo_reforma, estado) "
                     "VALUES (%s, 'bano', 'presupuesto_enviado') RETURNING id;", (lead6,))
        op6 = cur6.fetchone()[0]
        # SQL: su presupuesto sin Gate, de hace 49 h: con la regla real (48)
        # cumple el plazo.
        cur6.execute("INSERT INTO presupuestos (oportunidad_id, requiere_aprobacion, created_at) "
                     "VALUES (%s, false, now() - interval '49 hours');", (op6,))
        # SQL: la regla REAL pasa a valer ese valor, solo dentro de esta
        # transacción (el ROLLBACK del final lo deshace).
        cur6.execute("UPDATE reglas_negocio SET valor = %s WHERE clave = %s;",
                     (Decimal(valor), condicion.CLAVE_SEGUIMIENTO))
        # SQL: punto de vuelta: si la llamada da un error de Postgres, se
        # deshace solo lo de la llamada y la transacción sigue viva.
        cur6.execute("SAVEPOINT s6;")
        # La llamada real, con ESTE cursor (lee la regla cambiada).
        try:
            servicio.abrir_seguimiento_con_cursor(cur6, FollowupTaskCreate(oportunidad_id=op6,
                                                                           motivo="sin_respuesta_visita"))
        # La esperada: 503 por la regla fuera de rango.
        except ConfiguracionIncompleta as error:
            # SQL: vuelve al punto marcado (deshace lo de la llamada).
            cur6.execute("ROLLBACK TO SAVEPOINT s6;")
            comprobar(f"regla REAL = {valor} -> 503 configuracion_incompleta", "8760" in error.faltan[0],
                      f"({error.faltan})")
        # Otra excepción (por ejemplo, el "timestamp out of range" de
        # Postgres en una versión sin máximo): FALLO con su clase y texto.
        except Exception as error:
            # SQL: vuelve al punto marcado (la transacción quedó abortada).
            cur6.execute("ROLLBACK TO SAVEPOINT s6;")
            comprobar(f"regla REAL = {valor} -> 503 configuracion_incompleta", False,
                      f"(salió {type(error).__name__}: {str(error).strip()})")
        # Ninguna excepción: el seguimiento se ha abierto (dentro de la
        # transacción) -> FALLO explícito, regla de CLAUDE.md.
        else:
            comprobar(f"regla REAL = {valor} -> 503 configuracion_incompleta", False,
                      "(sin error: la excepción se ha tragado)")
finally:
    # ROLLBACK: se deshace TODO lo de S6, también el UPDATE de la regla.
    cn6.rollback()

# Comprobación, en una transacción NUEVA, de que no queda nada.
# SQL: el valor de la regla ahora.
cur6.execute("SELECT valor FROM reglas_negocio WHERE clave = %s;", (condicion.CLAVE_SEGUIMIENTO,))
valor_despues = cur6.fetchone()[0]
# SQL: clientes con la marca de S6 que queden.
cur6.execute("SELECT count(*) FROM clientes WHERE email = %s;", (f"check-followup-svc-{marca6}@example.com",))
restos6 = cur6.fetchone()[0]
# Se cierra la transacción de las consultas y la conexión de S6.
cn6.rollback()
cn6.close()
# La regla tiene que seguir en su valor y no puede quedar nada de S6.
comprobar(f"tras el ROLLBACK la regla sigue en {valor_real}", valor_despues == valor_real, f"({valor_despues})")
comprobar("tras el ROLLBACK no queda ninguna fila con la marca de S6", restos6 == 0, f"(clientes = {restos6})")

# ======================================================================
print("\nS7. primer_criterio_incumplido con valores sueltos")
# ======================================================================


def primero(valores):
    """primer_criterio_incumplido sin parar el script: devuelve su
    resultado o, si lanza una excepción (por ejemplo, una versión rota con
    otro número de criterios), el texto "excepción <clase>"."""
    # La llamada real.
    try:
        return condicion.primer_criterio_incumplido(valores)
    # Cualquier excepción se convierte en un resultado que no coincide con
    # ninguno esperado: la comprobación falla y el script sigue contando.
    except Exception as error:
        return f"excepción {type(error).__name__}"


# Todos True -> None (se cumple la condición).
comprobar("todos True -> None", primero([True] * 6) is None)
# El primero que no es True gana, aunque haya otros después.
comprobar("[True, False, ..., False] -> con_gate", primero([True, False, True, False, True, False]) == "con_gate")
# NULL (None) cuenta como incumplido, igual que en un WHERE.
comprobar("None cuenta como incumplido -> sin_presupuesto", primero([None] * 6) == "sin_presupuesto")
# Solo el último falla -> plazo_no_cumplido.
resultado = primero([True] * 5 + [False])
comprobar("solo el último False -> plazo_no_cumplido", resultado == "plazo_no_cumplido", f"({resultado})")
# Longitud distinta: strict=True lo convierte en un error en vez de
# emparejar mal en silencio.
try:
    condicion.primer_criterio_incumplido([True] * 5)
# La esperada: ValueError de zip(strict=True).
except ValueError:
    comprobar("5 valores en vez de 6 -> ValueError (zip strict)", True)
# else: no salió -> FALLO explícito.
else:
    comprobar("5 valores en vez de 6 -> ValueError (zip strict)", False, "(sin error: la excepción se ha tragado)")

# ----------------------------------------------------------------------
# Resumen y código de salida (1 si algo falló, para la suite).
# ----------------------------------------------------------------------
total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
# Con algún fallo: se listan y se sale con código 1.
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
