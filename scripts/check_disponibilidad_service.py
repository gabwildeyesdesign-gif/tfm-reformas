"""
Verificación SIN servidor de la comprobación de disponibilidad de D31
(Bloque B5 del plan del Agente 2, docs/Plan_Agente2_Calendar.txt, sección
10.3 y sección 7): solapa, huecos_libres, mensaje_hueco_ocupado y
comprobar_hueco_libre, en app/services/reglas_visita.py.

  S0  autoprueba de ConexionSinCommit (rama else: "se ha tragado").
  S1  solapa con los bordes: una visita de 10:00 a 11:00 deja libres las
      09:00 y las 11:00, y ocupa las 09:01, las 10:00 y las 10:59.
  S2  huecos_libres con reglas fijas (08:30-13:30 y 17:00-20:00, 60 min,
      paso 30): la lista exacta, la visita entera dentro de su franja.
  S3  inicios en el pasado nunca ofrecidos ("ahora" a media mañana).
  S4  días del cambio de hora (25/10/2026 y 28/03/2027): la rejilla sale en
      hora de Madrid de ESE día, y el solape se mide en tiempo real.
  S5  el mensaje: solo las horas libres, ningún otro número; y el de la
      lista vacía.
  S6  leer_reglas_visita exige paso_huecos_min (falta -> problema; 0 ->
      problema).
  S7  comprobar_hueco_libre contra la base de datos, con datos propios
      dentro de UNA transacción que termina en ROLLBACK: libre, ocupado
      (HuecoOcupado con la lista), la visita propia excluida (C8), las
      'cancelada' y 'completada' no ocupan, y el bloqueo del día se ha
      tomado (pg_locks).
  S8  el orden en el código (ast): en los dos servicios, una sola llamada
      a comprobar_hueco_libre, DESPUÉS de validar_fecha (y en /visits,
      después del 409 visita_confirmada).

Las 4 preguntas: (1) escribe en tablas reales SOLO dentro de una
transacción que se deshace siempre; (2) pasa su cursor al código, por eso
ConexionSinCommit; (3) si se corta, Postgres deshace la transacción
abierta; al final se comprueba que no queda el cliente de prueba; (4) no
toca el 8000 ni ninguna fila real.
"""

# ast: comprobaciones de estructura del código (S8).
import ast
# re: buscar las horas "HH:MM" en el mensaje (S5).
import re
# sys: consola en UTF-8 y código de salida.
import sys
# Fechas: día, instante, hora, duraciones y UTC.
from datetime import date, datetime, time, timedelta, timezone
# Decimal: los valores de reglas_negocio llegan así (NUMERIC), S6.
from decimal import Decimal
# Path: rutas.
from pathlib import Path

# La raíz del repositorio (carpeta padre de scripts/) en sys.path. En una
# copia de prueba en negativo, es la de la copia, y se importa SU app.
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Primera en la lista de búsqueda de módulos.
sys.path.insert(0, str(RAIZ_REPO))
# Tildes correctas en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2 y su clase de conexión; Json para los JSONB.
import psycopg2
# La clase base de las conexiones (para ConexionSinCommit).
import psycopg2.extensions
# Json: un diccionario de Python a una columna JSONB.
from psycopg2.extras import Json

# El módulo que se prueba, entero (sus constantes y funciones).
import app.services.reglas_visita as rv
# La cadena de conexión del .env (nunca se imprime).
from app.config import DATABASE_URL

# Las fechas propias de este script (B5-0): su bloque de semanas lejanas.
import fechas_prueba

# La marca propia (dominio reservado).
EMAIL = "check-disponibilidad-svc@example.com"
# La zona de Madrid, la misma que usa el código.
MADRID = rv.ZONA_MADRID
# Contadores.
ok, fallos = 0, []


def comprobar(titulo, condicion, detalle=""):
    """[OK] si la condición es verdadera; [FALLO] (y se apunta) si no."""
    # global: se cambia el contador de fuera.
    global ok
    # Correcta: se suma y se imprime.
    if condicion:
        # Una más.
        ok += 1
        # Se enseña.
        print(f"  [OK]    {titulo}")
    # Incorrecta: se apunta y se imprime con el detalle.
    else:
        # Para el resumen.
        fallos.append(f"{titulo} {detalle}")
        # En su sitio.
        print(f"  [FALLO] {titulo} {detalle}")


# La excepción propia de la defensa.
class CommitProhibido(Exception):
    """Alguien ha llamado a commit() en la conexión de prueba."""


# La conexión con commit() prohibido (regla de CLAUDE.md del 2026-10-08).
class ConexionSinCommit(psycopg2.extensions.connection):
    """Una conexión normal salvo commit(), que lanza CommitProhibido."""

    def commit(self):
        # No se confirma nada: error, y el ROLLBACK final lo deshace todo.
        raise CommitProhibido("commit() en una conexión de prueba: la prueba termina siempre en ROLLBACK")


def en_madrid(dia: date, hh: int, mm: int) -> datetime:
    """Ese día a esa hora, en Madrid (con el desfase de ESE día)."""
    # combine con la zona de Madrid.
    return datetime.combine(dia, time(hh, mm), tzinfo=MADRID)


# Las reglas fijas de las pruebas puras (las de hoy en reglas_negocio, pero
# escritas aquí: S1-S5 no dependen de la base de datos).
REGLAS = {
    rv.CLAVE_MANANA_INICIO: 510,
    rv.CLAVE_MANANA_FIN: 810,
    rv.CLAVE_TARDE_INICIO: 1020,
    rv.CLAVE_TARDE_FIN: 1200,
    rv.CLAVE_DURACION: 60,
    rv.CLAVE_PASO_HUECOS: 30,
}
# Todos los inicios de la rejilla con esas reglas, calculados a mano:
# mañana de 08:30 a 12:30 (termina 13:30) y tarde de 17:00 a 19:00
# (termina 20:00), cada 30 minutos.
REJILLA = ["08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
           "17:00", "17:30", "18:00", "18:30", "19:00"]
# Un día laborable de referencia, lejos (para que "ahora" quede antes).
DIA_REF = date(2030, 1, 7)
# Un "ahora" anterior a cualquier hora de ese día.
AHORA_ANTES = datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc)

# ======================================================================
print("S0. Autoprueba de ConexionSinCommit")
# ======================================================================
# La conexión de toda la prueba, con commit() prohibido.
cn = psycopg2.connect(DATABASE_URL, connection_factory=ConexionSinCommit)
# Su cursor.
cur = cn.cursor()
# try/except/else: sin la excepción, FALLO explícito.
try:
    # Debe lanzar CommitProhibido.
    cn.commit()
# La esperada.
except CommitProhibido:
    # Correcto.
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", True)
# commit() no lanzó nada: la defensa no existe.
else:
    # FALLO explícito.
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", False, "(la excepción se ha tragado)")

# ======================================================================
print("\nS1. solapa: los bordes (visita de 10:00 a 11:00, 60 min)")
# ======================================================================
# La visita ocupada: 10:00 de DIA_REF.
OCUPADA = en_madrid(DIA_REF, 10, 0)
# (hora pedida, ¿se solapa?): los dos bordes libres y tres puntos dentro.
for hh, mm, esperado in ((9, 0, False), (9, 1, True), (10, 0, True), (10, 59, True), (11, 0, False)):
    # El resultado de la función.
    obtenido = rv.solapa(en_madrid(DIA_REF, hh, mm), OCUPADA, 60)
    # Igual que lo esperado.
    comprobar(f"{hh:02d}:{mm:02d} frente a 10:00-11:00 -> {'ocupado' if esperado else 'libre'}",
              obtenido is esperado, f"(obtenido {obtenido})")
# Es simétrica: da lo mismo el orden de los argumentos.
comprobar("simétrica: solapa(10:00, 10:30) == solapa(10:30, 10:00) == True",
          rv.solapa(OCUPADA, en_madrid(DIA_REF, 10, 30), 60) is True
          and rv.solapa(en_madrid(DIA_REF, 10, 30), OCUPADA, 60) is True)

# ======================================================================
print("\nS2. huecos_libres: la lista exacta")
# ======================================================================
# Sin ninguna ocupada: la rejilla entera.
libres = rv.huecos_libres(DIA_REF, AHORA_ANTES, REGLAS, [])
# Se enseña.
print(f"    sin ocupadas: {libres}")
# Igual a la calculada a mano.
comprobar(f"sin ocupadas: las {len(REJILLA)} horas de la rejilla", libres == REJILLA, f"({libres})")
# Con la de las 10:00 ocupada: fuera 09:30, 10:00 y 10:30 (se solapan).
libres = rv.huecos_libres(DIA_REF, AHORA_ANTES, REGLAS, [OCUPADA])
# Lo esperado: la rejilla sin esas tres.
esperado = [h for h in REJILLA if h not in ("09:30", "10:00", "10:30")]
# Se enseña.
print(f"    10:00 ocupada: {libres}")
# Exacta.
comprobar("10:00 ocupada: fuera 09:30, 10:00 y 10:30; 09:00 y 11:00 siguen libres", libres == esperado, f"({libres})")
# Con una ocupada a las 12:45 (hecha a mano: la API no la dejaría): fuera
# 12:00 y 12:30 de la mañana (12:30 terminaría 13:30 y se solapa).
libres = rv.huecos_libres(DIA_REF, AHORA_ANTES, REGLAS, [en_madrid(DIA_REF, 12, 45)])
# Lo esperado.
esperado = [h for h in REJILLA if h not in ("12:00", "12:30")]
# Exacta.
comprobar("12:45 ocupada: fuera 12:00 y 12:30; 11:30 (termina 12:30) sigue libre", libres == esperado, f"({libres})")

# ======================================================================
print("\nS3. Inicios en el pasado: nunca se ofrecen")
# ======================================================================
# "Ahora" = ese día a las 10:15 de Madrid.
ahora = en_madrid(DIA_REF, 10, 15)
# Libres sin ocupadas.
libres = rv.huecos_libres(DIA_REF, ahora, REGLAS, [])
# Lo esperado: solo las posteriores a las 10:15.
esperado = ["10:30", "11:00", "11:30", "12:00", "12:30", "17:00", "17:30", "18:00", "18:30", "19:00"]
# Exacta.
comprobar("ahora 10:15: la primera ofrecida es 10:30", libres == esperado, f"({libres})")
# "Ahora" justo a las 10:30: esa tampoco (tiene que ser POSTERIOR).
libres = rv.huecos_libres(DIA_REF, en_madrid(DIA_REF, 10, 30), REGLAS, [])
# La primera, 11:00.
comprobar("ahora 10:30 en punto: 10:30 no se ofrece", libres[:1] == ["11:00"], f"({libres[:3]})")
# "Ahora" después de la última: lista vacía.
comprobar("ahora 19:01: ninguna", rv.huecos_libres(DIA_REF, en_madrid(DIA_REF, 19, 1), REGLAS, []) == [])

# ======================================================================
print("\nS4. Días del cambio de hora")
# ======================================================================
# (día del cambio, desfase después del cambio): el cambio es a las 02:00-03:00,
# antes de cualquier franja, así que todo el horario de visitas va con el nuevo.
for dia, desfase in ((date(2026, 10, 25), timedelta(hours=1)), (date(2027, 3, 28), timedelta(hours=2))):
    # Una ocupada a las 10:00 de Madrid, dada en UTC (como llega de Postgres).
    ocupada_utc = en_madrid(dia, 10, 0).astimezone(timezone.utc)
    # Se enseña.
    print(f"    {dia}: 10:00 de Madrid = {ocupada_utc:%H:%M} UTC (desfase {desfase})")
    # El desfase de las 10:00 de ese día es el esperado.
    comprobar(f"{dia}: las 10:00 de Madrid van a {desfase}", en_madrid(dia, 10, 0).utcoffset() == desfase)
    # La rejilla de ese día con la ocupada: fuera 09:30, 10:00 y 10:30.
    libres = rv.huecos_libres(dia, AHORA_ANTES.replace(year=2026, month=1), REGLAS, [ocupada_utc])
    # Exacta: con un desfase fijo mal puesto, la ocupada caería una hora antes o después.
    comprobar(f"{dia}: con la de las 10:00 (en UTC) ocupada, fuera 09:30, 10:00 y 10:30",
              libres == [h for h in REJILLA if h not in ("09:30", "10:00", "10:30")], f"({libres})")
    # Los bordes del solape, en tiempo real: 09:00 y 11:00 libres.
    comprobar(f"{dia}: 09:00 y 11:00 de Madrid no se solapan con ella",
              not rv.solapa(en_madrid(dia, 9, 0), ocupada_utc, 60) and not rv.solapa(en_madrid(dia, 11, 0), ocupada_utc, 60))

# ======================================================================
print("\nS5. El mensaje del 409")
# ======================================================================
# Con dos horas libres.
mensaje = rv.mensaje_hueco_ocupado(["09:00", "11:00"])
# Se enseña.
print(f"    {mensaje}")
# Las horas que aparecen en el mensaje.
horas = re.findall(r"\b\d{2}:\d{2}\b", mensaje)
# Exactamente las libres, en ese orden.
comprobar("nombra exactamente las horas libres", horas == ["09:00", "11:00"], f"({horas})")
# Sin las horas, ninguna otra cifra (ni ids ni horas de fin).
comprobar("ninguna otra cifra (ni ids ni horas de otra visita)", not re.search(r"\d", re.sub(r"\b\d{2}:\d{2}\b", "", mensaje)))
# Con la lista vacía: pide otro día y no tiene ninguna cifra.
vacio = rv.mensaje_hueco_ocupado([])
# Se enseña.
print(f"    {vacio}")
# Ninguna cifra y pide otro día.
comprobar("lista vacía: sin cifras y pide otro día", not re.search(r"\d", vacio) and "otro día" in vacio)

# ======================================================================
print("\nS6. leer_reglas_visita exige paso_huecos_min")
# ======================================================================


class CursorDeMentira:
    """execute() no hace nada; fetchall() devuelve las filas dadas."""

    def __init__(self, filas):
        # Las filas que devolverá.
        self.filas = filas

    def execute(self, sql, params=None):
        # No hace nada.
        pass

    def fetchall(self):
        # Las filas dadas.
        return self.filas


# Las cinco de antes (sin el paso), con valores correctos.
CINCO = [(rv.CLAVE_MANANA_INICIO, 510), (rv.CLAVE_MANANA_FIN, 810), (rv.CLAVE_TARDE_INICIO, 1020),
         (rv.CLAVE_TARDE_FIN, 1200), (rv.CLAVE_DURACION, 60)]
# Sin el paso: un problema que lo nombra.
_, problemas = rv.leer_reglas_visita(CursorDeMentira([(c, Decimal(v)) for c, v in CINCO]))
# Lo nombra.
comprobar("sin paso_huecos_min: 'paso_huecos_min: no existe'", problemas == ["paso_huecos_min: no existe"], f"({problemas})")
# Con paso 0: otro problema.
_, problemas = rv.leer_reglas_visita(CursorDeMentira([(c, Decimal(v)) for c, v in CINCO + [(rv.CLAVE_PASO_HUECOS, 0)]]))
# Lo nombra.
comprobar("paso 0: 'debe ser mayor que 0'", problemas == ["paso_huecos_min: debe ser mayor que 0"], f"({problemas})")
# Con las seis bien: ningún problema y el paso como entero.
reglas6, problemas = rv.leer_reglas_visita(CursorDeMentira([(c, Decimal(v)) for c, v in CINCO + [(rv.CLAVE_PASO_HUECOS, 30)]]))
# Sin problemas.
comprobar("las seis bien: 0 problemas y paso 30", problemas == [] and reglas6.get(rv.CLAVE_PASO_HUECOS) == 30, f"({problemas})")

# ======================================================================
print("\nS7. comprobar_hueco_libre contra la base de datos (ROLLBACK)")
# ======================================================================
# El primer día del bloque del script (B5-0), lejos de cualquier visita real.
HOY = datetime.now(MADRID).date()
# Un jueves del bloque (lunes + 3).
DIA = fechas_prueba.lunes_de_prueba("check_disponibilidad_service", HOY) + timedelta(days=3)
# Antes de crear nada: ninguna visita activa ajena en el bloque.
fechas_prueba.exigir_libres("check_disponibilidad_service", EMAIL,
                            [fechas_prueba.tramo_del_bloque("check_disponibilidad_service", HOY)])
# try/finally: pase lo que pase, ROLLBACK.
try:
    # SQL: el cliente, con la marca.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES ('Prueba disp', %s, '600000000') RETURNING id;",
                (EMAIL,))
    # Su id.
    CLIENTE = cur.fetchone()[0]
    # SQL: su lead (m2 dentro del CHECK de leads).
    cur.execute("INSERT INTO leads (cliente_id, canal, datos_estructurados) VALUES (%s, 'check_disp', %s) RETURNING id;",
                (CLIENTE, Json({"m2": 10})))
    # Su id.
    LEAD = cur.fetchone()[0]

    def oportunidad():
        """Una oportunidad propia en 'visita_agendada'."""
        # SQL: la oportunidad del lead de prueba.
        cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma, estado) VALUES (%s, 'bano', 'visita_agendada') "
                    "RETURNING id;", (LEAD,))
        # Su id.
        return cur.fetchone()[0]

    def visita(op, estado, cuando):
        """Una visita propia. Devuelve su id."""
        # SQL: la visita con ese estado y esa fecha.
        cur.execute("INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
                    "VALUES (%s, %s, %s, 'prueba disponibilidad') RETURNING id;", (op, cuando, estado))
        # Su id.
        return cur.fetchone()[0]

    # Las reglas REALES (con el paso), leídas en esta transacción.
    reglas, problemas = rv.leer_reglas_visita(cur)
    # Sin problemas: si los hay, lo demás no tiene sentido.
    comprobar("las reglas reales se leen sin problemas (incluido el paso)", problemas == [], f"({problemas})")
    # SQL: el reloj de la base de datos.
    cur.execute("SELECT now();")
    # El "ahora" de la transacción.
    AHORA = cur.fetchone()[0]
    # Una visita 'solicitada' a las 10:00 del jueves, de una oportunidad propia.
    op_a = oportunidad()
    # Su id.
    v_a = visita(op_a, "solicitada", en_madrid(DIA, 10, 0))
    # Y una 'cancelada' y una 'completada' a las 17:00 (no deben ocupar).
    visita(oportunidad(), "cancelada", en_madrid(DIA, 17, 0))
    # La completada.
    visita(oportunidad(), "completada", en_madrid(DIA, 17, 0))

    def intento(hh, mm, excluir=()):
        """Llama a comprobar_hueco_libre; devuelve None (libre) o la HuecoOcupado."""
        # try: si está ocupado, lanza HuecoOcupado.
        try:
            # Con el cursor de la prueba.
            rv.comprobar_hueco_libre(cur, en_madrid(DIA, hh, mm), AHORA, reglas, list(excluir))
        # Ocupado: se devuelve la excepción.
        except rv.HuecoOcupado as error:
            # Para mirarla.
            return error
        # Un commit() dentro sería un FALLO contado.
        except CommitProhibido:
            # Se cuenta.
            comprobar("comprobar_hueco_libre sin commit() dentro", False, "(CommitProhibido)")
            # Y se trata como libre.
            return None
        # Libre.
        return None

    # 11:00: libre (empieza justo cuando termina la de las 10:00).
    comprobar("11:00: libre", intento(11, 0) is None)
    # 10:30: ocupado, con la lista de libres.
    error = intento(10, 30)
    # Se enseña.
    print(f"    10:30 -> {error and (error.motivo, error.mensaje, error.huecos_libres)}")
    # Ocupado (rama else explícita: si no sale, FALLO).
    if error is None:
        # FALLO explícito: la excepción no ha salido.
        comprobar("10:30: HuecoOcupado", False, "(la excepción se ha tragado: el hueco sale libre)")
    # Salió: se comprueba.
    else:
        # Motivo y lista: la rejilla real sin 09:30, 10:00 ni 10:30 (con las
        # reglas reales; las 17:00 cancelada y completada no ocupan).
        esperado = [rv._hhmm(m) for ini, fin in ((reglas[rv.CLAVE_MANANA_INICIO], reglas[rv.CLAVE_MANANA_FIN]),
                                                 (reglas[rv.CLAVE_TARDE_INICIO], reglas[rv.CLAVE_TARDE_FIN]))
                    for m in range(ini, fin - reglas[rv.CLAVE_DURACION] + 1, reglas[rv.CLAVE_PASO_HUECOS])
                    if not (9 * 60 + 30 <= m <= 10 * 60 + 30)]
        # Igual.
        comprobar("10:30: HuecoOcupado 'hueco_ocupado' con la lista de libres del día (17:00 incluida)",
                  error.motivo == "hueco_ocupado" and error.huecos_libres == esperado, f"({error.huecos_libres})")
    # 10:30 excluyendo la propia: libre (C8: sustitución o confirmación).
    comprobar("10:30 excluyendo la visita que la ocupa: libre (C8)", intento(10, 30, [v_a]) is None)
    # 17:00: libre (la cancelada y la completada no ocupan).
    comprobar("17:00 con una 'cancelada' y una 'completada': libre", intento(17, 0) is None)
    # SQL: el bloqueo del día, tomado por ESTA sesión (pg_locks: classid es
    # el primer entero y objid el segundo; objsubid 2 = forma de dos enteros).
    cur.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND classid = %s AND objid = %s "
                "AND objsubid = 2 AND pid = pg_backend_pid() AND granted;",
                (rv.CLAVE_BLOQUEO_VISITAS, DIA.year * 10000 + DIA.month * 100 + DIA.day))
    # Uno.
    comprobar("el bloqueo del día está tomado por esta transacción (pg_locks)", cur.fetchone()[0] == 1)
# Siempre: ROLLBACK de todo.
finally:
    # Se deshace todo (y se suelta el bloqueo).
    cn.rollback()

# Fuera de la transacción: no queda el cliente de prueba.
cur.execute("SELECT count(*) FROM clientes WHERE email = %s;", (EMAIL,))
# Cero.
comprobar("no queda ningún dato de prueba (ROLLBACK)", cur.fetchone()[0] == 0)
# Se cierra la lectura y la conexión.
cn.rollback()
# Cerrada.
cn.close()

# ======================================================================
print("\nS8. El orden en el código (ast)")
# ======================================================================


def lineas_de_llamadas(arbol, nombre):
    """Las líneas en las que se llama a una función con ese nombre."""
    # Cada nodo Call cuyo nombre es ese (llamada directa).
    return sorted(n.lineno for n in ast.walk(arbol)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == nombre)


# Los dos servicios.
for archivo in ("app/services/visits_service.py", "app/services/resultados_llamada_service.py"):
    # El árbol del archivo.
    arbol = ast.parse((RAIZ_REPO / archivo).read_text(encoding="utf-8"))
    # Dónde se llama a cada cosa.
    hueco = lineas_de_llamadas(arbol, "comprobar_hueco_libre")
    # Dónde se valida la fecha.
    fecha = lineas_de_llamadas(arbol, "validar_fecha")
    # Una sola llamada, después de validar_fecha.
    comprobar(f"{archivo}: una llamada a comprobar_hueco_libre, después de validar_fecha",
              len(hueco) == 1 and fecha and hueco[0] > max(fecha), f"(hueco {hueco}, fecha {fecha})")
# En /visits, además, después del 409 visita_confirmada.
texto = (RAIZ_REPO / "app/services/visits_service.py").read_text(encoding="utf-8")
# La línea del motivo visita_confirmada y la de la llamada.
linea_conf = next((i for i, l in enumerate(texto.splitlines(), 1) if '"visita_confirmada"' in l), 0)
# La línea de la llamada.
linea_hueco = next((i for i, l in enumerate(texto.splitlines(), 1) if "comprobar_hueco_libre(cursor" in l), 0)
# Después.
comprobar("visits_service: hueco_ocupado después de visita_confirmada", 0 < linea_conf < linea_hueco,
          f"(visita_confirmada {linea_conf}, hueco {linea_hueco})")

# Resumen y código de salida (1 si hubo fallos).
total = ok + len(fallos)
# Separador.
print("\n" + "=" * 78)
# El recuento.
print(f"RESULTADO: {ok}/{total} correctas")
# Separador.
print("=" * 78)
# Con fallos, se listan y el código de salida es 1.
if fallos:
    # Cabecera.
    print("FALLOS:")
    # Uno por línea.
    for f in fallos:
        # Con su detalle.
        print(f"  - {f}")
    # Código 1.
    sys.exit(1)
