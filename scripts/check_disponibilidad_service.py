"""
Verificación SIN servidor de la disponibilidad de visitas, en
app/services/reglas_visita.py.

Historia: se escribió en el Bloque B5 del plan del Agente 2 (D31: solapa,
huecos_libres con paso_huecos_min). Reescrita el 2026-10-13 para D32 (plan
docs/Plan_Horario_Visitas_D32.txt, Bloque B2): rejilla de D23.5, choque con
desplazamiento, UNA función de disponibilidad, el 422 fuera_de_rejilla y
paso_huecos_min retirada.

  S0  autoprueba de ConexionSinCommit (rama else: "se ha tragado").
  S1  rejilla(): con las reglas de D23.5, exactamente 08:30, 09:45, 11:00,
      12:15, 17:00 y 18:15; con valores de mentira (90 + 30 y otras
      franjas), y con desplazamiento 0.
  S2  choca(): los bordes con desplazamiento (una de 09:45 deja libres las
      08:30 y las 11:00 y choca con 08:31 y 10:59); simétrica; con
      desplazamiento 0 es el solape de D31.
  S3  disponibilidad() con un cursor de mentira: la rejilla entera por
      franja; una visita antigua a las 10:00 quita las 09:45 y las 11:00;
      sábado y domingo vacíos; nunca una hora pasada (ni la de "ahora"
      justo); UNA consulta para 1 día y para 10; la ventana de la consulta
      y los excluidos.
  S4  días del cambio de hora (26/10/2026 y 29/03/2027): la rejilla en hora
      de Madrid de ese día y el choque medido en tiempo real.
  S5  validar_fecha y los mensajes: 10:00 -> 422 fuera_de_rejilla con
      horas_validas por franja (rama else: si no sale, FALLO); 12:45 sigue
      siendo fuera_de_franja (el orden); 09:45 pasa; el texto del 409.
  S6  leer_reglas_visita: las siete reglas de D32; falta desplazamiento_min
      o dias_oferta_visita -> problema; dias_oferta_visita 0 o 61 ->
      problema; desplazamiento 0 vale; paso_huecos_min ya NO se exige.
  S7  contra la base de datos, en UNA transacción con ROLLBACK
      (ConexionSinCommit): la rejilla con las reglas REALES es la de D23.5;
      comprobar_hueco_libre frente a una visita antigua a las 10:00 (409 a
      las 11:00 con huecos_libres plana; 08:30 libre; la propia excluida,
      C8; 'cancelada' y 'completada' no ocupan; el bloqueo del día en
      pg_locks); disponibilidad de 10 días con UNA consulta; y la fila
      paso_huecos_min borrada DENTRO de la transacción: leer_reglas_visita
      sigue sin problemas (N12 del plan: ya nadie la lee).
  S8  estructura (ast): en los dos servicios, una llamada a
      comprobar_hueco_libre, después de validar_fecha; comprobar_hueco_libre
      usa disponibilidad(); fuera de reglas_visita.py, nadie llama a choca()
      ni lee CLAVE_DESPLAZAMIENTO (solo reglas_visita.py calcula horas).

Las 4 preguntas: (1) escribe en tablas reales SOLO dentro de una
transacción que se deshace siempre; (2) pasa su cursor al código, por eso
ConexionSinCommit; (3) si se corta, Postgres deshace la transacción
abierta; al final se comprueba que no queda el cliente de prueba; (4) no
toca el 8000 ni ninguna fila real.
"""

# ast: comprobaciones de estructura del código (S8).
import ast
# re: buscar las horas "HH:MM" en los mensajes (S5).
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

# psycopg2: hablar con PostgreSQL.
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
# Las 6 horas de D23.5, escritas a mano (son lo que se quiere comprobar).
D23_5 = {"manana": ["08:30", "09:45", "11:00", "12:15"], "tarde": ["17:00", "18:15"]}
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


class CursorDeMentira:
    """
    Imita lo mínimo de un cursor: execute() apunta la consulta y sus
    parámetros (para contarlas, S3) y fetchall() devuelve las filas dadas.
    """

    def __init__(self, filas):
        # Las filas que devolverá.
        self.filas = filas
        # Las consultas recibidas: (sql, parámetros).
        self.consultas = []

    def execute(self, sql, params=None):
        # Se apunta (no se ejecuta nada).
        self.consultas.append((sql, params))

    def fetchall(self):
        # Las filas dadas.
        return self.filas


class CursorContador:
    """Envuelve un cursor real y cuenta sus execute (S7: UNA consulta)."""

    def __init__(self, cursor):
        # El cursor de verdad.
        self.cursor = cursor
        # Cuántas veces se ha llamado a execute.
        self.ejecuciones = 0

    def execute(self, sql, params=None):
        # Una más.
        self.ejecuciones += 1
        # Y se ejecuta de verdad.
        return self.cursor.execute(sql, params)

    def fetchall(self):
        # Del cursor de verdad.
        return self.cursor.fetchall()


def en_madrid(dia: date, hh: int, mm: int) -> datetime:
    """Ese día a esa hora, en Madrid (con el desfase de ESE día)."""
    # combine con la zona de Madrid.
    return datetime.combine(dia, time(hh, mm), tzinfo=MADRID)


# Las reglas fijas de las pruebas puras (las de hoy en reglas_negocio, pero
# escritas aquí: S1-S5 no dependen de la base de datos).
REGLAS = {
    # Mañana 08:30-13:30.
    rv.CLAVE_MANANA_INICIO: 510, rv.CLAVE_MANANA_FIN: 810,
    # Tarde 17:00-20:00.
    rv.CLAVE_TARDE_INICIO: 1020, rv.CLAVE_TARDE_FIN: 1200,
    # 60 de visita, 15 de desplazamiento y 10 días de oferta.
    rv.CLAVE_DURACION: 60, rv.CLAVE_DESPLAZAMIENTO: 15, rv.CLAVE_DIAS_OFERTA: 10,
}
# Un lunes laborable de referencia, lejos (para que "ahora" quede antes).
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
print("\nS1. rejilla(): las horas de inicio válidas")
# ======================================================================
# Con las reglas de hoy: las 6 de D23.5.
obtenida = rv.rejilla_texto(REGLAS)
# Se enseña.
print(f"    60 + 15: {obtenida}")
# Exacta, por franja.
comprobar("60 + 15 en 08:30-13:30 y 17:00-20:00 -> exactamente las 6 horas de D23.5", obtenida == D23_5, f"({obtenida})")
# Los minutos también (la forma interna).
comprobar("en minutos: [510, 585, 660, 735] y [1020, 1095]",
          rv.rejilla(REGLAS) == {"manana": [510, 585, 660, 735], "tarde": [1020, 1095]}, f"({rv.rejilla(REGLAS)})")
# Valores de mentira: 90 de visita, 30 de desplazamiento, 08:00-14:00 y 16:00-20:00.
MENTIRA = {**REGLAS, rv.CLAVE_DURACION: 90, rv.CLAVE_DESPLAZAMIENTO: 30, rv.CLAVE_MANANA_INICIO: 480,
           rv.CLAVE_MANANA_FIN: 840, rv.CLAVE_TARDE_INICIO: 960, rv.CLAVE_TARDE_FIN: 1200}
# Paso 120: mañana 08:00, 10:00, 12:00 (12:00 + 90 = 13:30 <= 14:00; 14:00 no cabe); tarde 16:00, 18:00.
comprobar("90 + 30: mañana 08:00, 10:00, 12:00; tarde 16:00, 18:00",
          rv.rejilla_texto(MENTIRA) == {"manana": ["08:00", "10:00", "12:00"], "tarde": ["16:00", "18:00"]},
          f"({rv.rejilla_texto(MENTIRA)})")
# Desplazamiento 0: cada hora en punto (y media) desde el inicio, como una rejilla de 60.
SIN_DESPL = {**REGLAS, rv.CLAVE_DESPLAZAMIENTO: 0}
# 08:30, 09:30, 10:30, 11:30, 12:30 (termina 13:30 justas) y 17:00, 18:00, 19:00.
comprobar("60 + 0: 08:30 ... 12:30 y 17:00, 18:00, 19:00",
          rv.rejilla_texto(SIN_DESPL) == {"manana": ["08:30", "09:30", "10:30", "11:30", "12:30"],
                                          "tarde": ["17:00", "18:00", "19:00"]}, f"({rv.rejilla_texto(SIN_DESPL)})")

# ======================================================================
print("\nS2. choca(): los bordes con desplazamiento")
# ======================================================================
# La visita ocupada: 09:45 de DIA_REF (termina 10:45; libre desde 11:00).
OCUPADA = en_madrid(DIA_REF, 9, 45)
# (hora pedida, ¿choca?): los dos bordes libres y tres puntos que chocan.
for hh, mm, esperado in ((8, 30, False), (8, 31, True), (9, 45, True), (10, 59, True), (11, 0, False)):
    # El resultado de la función, con 60 + 15.
    obtenido = rv.choca(en_madrid(DIA_REF, hh, mm), OCUPADA, 60, 15)
    # Igual que lo esperado.
    comprobar(f"{hh:02d}:{mm:02d} frente a una de 09:45 (60 + 15) -> {'choca' if esperado else 'libre'}",
              obtenido is esperado, f"(obtenido {obtenido})")
# Es simétrica.
comprobar("simétrica: choca(09:45, 10:30) == choca(10:30, 09:45) == True",
          rv.choca(OCUPADA, en_madrid(DIA_REF, 10, 30), 60, 15) is True
          and rv.choca(en_madrid(DIA_REF, 10, 30), OCUPADA, 60, 15) is True)
# Con desplazamiento 0 es el solape de D31: 10:45 ya no choca.
comprobar("desplazamiento 0: 10:45 tras una de 09:45 no choca (el solape de D31)",
          rv.choca(en_madrid(DIA_REF, 10, 45), OCUPADA, 60, 0) is False
          and rv.choca(en_madrid(DIA_REF, 10, 44), OCUPADA, 60, 0) is True)

# ======================================================================
print("\nS3. disponibilidad() con un cursor de mentira")
# ======================================================================
# Sin ocupadas: la rejilla entera, por franja.
falso = CursorDeMentira([])
# Un solo día.
libres = rv.disponibilidad(falso, [DIA_REF], AHORA_ANTES, REGLAS)
# Exacta.
comprobar("sin ocupadas: la rejilla entera por franja", libres == {DIA_REF: D23_5}, f"({libres})")
# Una visita ANTIGUA a las 10:00 (fuera de la rejilla, puesta a mano): quita
# 09:45 (terminaría 10:45) y 11:00 (10:00 + 60 + 15 = 11:15).
falso = CursorDeMentira([(en_madrid(DIA_REF, 10, 0).astimezone(timezone.utc),)])
# El día.
libres = rv.disponibilidad(falso, [DIA_REF], AHORA_ANTES, REGLAS)[DIA_REF]
# Se enseña.
print(f"    antigua a las 10:00: {libres}")
# Fuera 09:45 y 11:00.
comprobar("antigua a las 10:00: fuera 09:45 y 11:00 (el desplazamiento); 08:30 y 12:15 libres",
          libres == {"manana": ["08:30", "12:15"], "tarde": ["17:00", "18:15"]}, f"({libres})")
# Sábado y domingo: vacíos (DIA_REF + 5 y + 6).
falso = CursorDeMentira([])
# Los dos días del fin de semana.
fin = rv.disponibilidad(falso, [DIA_REF + timedelta(days=5), DIA_REF + timedelta(days=6)], AHORA_ANTES, REGLAS)
# Las dos franjas vacías en los dos.
comprobar("sábado y domingo: las dos franjas vacías",
          all(v == {"manana": [], "tarde": []} for v in fin.values()) and len(fin) == 2, f"({fin})")
# "Ahora" a las 10:00 de ese día: solo las posteriores (11:00, 12:15 y la tarde).
libres = rv.disponibilidad(CursorDeMentira([]), [DIA_REF], en_madrid(DIA_REF, 10, 0), REGLAS)[DIA_REF]
# Exacta.
comprobar("ahora 10:00: la primera ofrecida es 11:00", libres["manana"] == ["11:00", "12:15"], f"({libres})")
# "Ahora" justo a las 11:00: esa tampoco (tiene que ser POSTERIOR).
libres = rv.disponibilidad(CursorDeMentira([]), [DIA_REF], en_madrid(DIA_REF, 11, 0), REGLAS)[DIA_REF]
# Solo 12:15 por la mañana.
comprobar("ahora 11:00 en punto: 11:00 no se ofrece", libres["manana"] == ["12:15"], f"({libres})")
# "Ahora" después de la última: nada.
libres = rv.disponibilidad(CursorDeMentira([]), [DIA_REF], en_madrid(DIA_REF, 18, 16), REGLAS)[DIA_REF]
# Vacías.
comprobar("ahora 18:16: ninguna", libres == {"manana": [], "tarde": []}, f"({libres})")
# UNA consulta, sea cual sea el número de días: 1 día y 10 días.
uno = CursorDeMentira([])
# Un día.
rv.disponibilidad(uno, [DIA_REF], AHORA_ANTES, REGLAS)
# Diez días.
diez = CursorDeMentira([])
# Del lunes al viernes de dos semanas.
rv.disponibilidad(diez, [DIA_REF + timedelta(days=d) for d in (0, 1, 2, 3, 4, 7, 8, 9, 10, 11)], AHORA_ANTES, REGLAS)
# Una cada vez.
comprobar("una sola consulta con 1 día y con 10 días", len(uno.consultas) == 1 and len(diez.consultas) == 1,
          f"({len(uno.consultas)}, {len(diez.consultas)})")
# Los parámetros de la de 10 días: estados, excluidos, desde y hasta.
_, (estados, excluidos, desde, hasta) = diez.consultas[0]
# La ventana: medianoche del primer día - 75 min y medianoche siguiente al último + 75 min.
comprobar("la ventana: del primer día - 75 min al día siguiente del último + 75 min",
          desde == en_madrid(DIA_REF, 0, 0) - timedelta(minutes=75)
          and hasta == en_madrid(DIA_REF + timedelta(days=12), 0, 0) + timedelta(minutes=75), f"({desde}, {hasta})")
# Los estados que ocupan y la lista de excluidos (vacía).
comprobar("solo 'solicitada' y 'confirmada'; sin excluidos", estados == ["solicitada", "confirmada"] and excluidos == [],
          f"({estados}, {excluidos})")
# Con excluidos: llegan a la consulta.
excl = CursorDeMentira([])
# Dos ids.
rv.disponibilidad(excl, [DIA_REF], AHORA_ANTES, REGLAS, [41, 42])
# En el segundo parámetro.
comprobar("los excluidos llegan a la consulta", excl.consultas[0][1][1] == [41, 42], f"({excl.consultas[0][1]})")
# Sin días: ninguna consulta.
nada = CursorDeMentira([])
# Lista vacía.
comprobar("sin días: {} y ninguna consulta", rv.disponibilidad(nada, [], AHORA_ANTES, REGLAS) == {}
          and nada.consultas == [])

# ======================================================================
print("\nS4. Días del cambio de hora")
# ======================================================================
# (lunes después del cambio, desfase de ese día): el cambio es a las
# 02:00-03:00 del domingo, así que el lunes entero va con el desfase nuevo.
for dia, desfase in ((date(2026, 10, 26), timedelta(hours=1)), (date(2027, 3, 29), timedelta(hours=2))):
    # Una antigua a las 10:00 de Madrid, dada en UTC (como llega de Postgres).
    ocupada_utc = en_madrid(dia, 10, 0).astimezone(timezone.utc)
    # Se enseña.
    print(f"    {dia}: 10:00 de Madrid = {ocupada_utc:%H:%M} UTC (desfase {desfase})")
    # El desfase de ese día es el esperado.
    comprobar(f"{dia}: las 10:00 de Madrid van a {desfase}", en_madrid(dia, 10, 0).utcoffset() == desfase)
    # Las libres de ese día con la ocupada: fuera 09:45 y 11:00, como en S3.
    libres = rv.disponibilidad(CursorDeMentira([(ocupada_utc,)]), [dia],
                               datetime(2026, 1, 1, tzinfo=timezone.utc), REGLAS)[dia]
    # Con un desfase fijo mal puesto, la ocupada caería una hora antes o después.
    comprobar(f"{dia}: con la de las 10:00 (en UTC) ocupada, fuera 09:45 y 11:00",
              libres == {"manana": ["08:30", "12:15"], "tarde": ["17:00", "18:15"]}, f"({libres})")

# ======================================================================
print("\nS5. validar_fecha (fuera_de_rejilla) y los mensajes")
# ======================================================================
# 10:00: dentro de la franja pero fuera de la rejilla -> 422 fuera_de_rejilla.
try:
    # Debe lanzar FechaNoValida.
    rv.validar_fecha(en_madrid(DIA_REF, 10, 0), AHORA_ANTES, REGLAS)
# La esperada: se mira.
except rv.FechaNoValida as error:
    # Se enseña.
    print(f"    10:00 -> {error.motivo}: {error.mensaje}")
    # Motivo y horas válidas por franja.
    comprobar("10:00 -> fuera_de_rejilla con horas_validas = la rejilla por franja",
              error.motivo == "fuera_de_rejilla" and error.horas_validas == D23_5, f"({error.motivo}, {error.horas_validas})")
    # El mensaje nombra exactamente las 6 horas y la pedida, y ninguna otra.
    comprobar("el mensaje nombra las 6 horas válidas y la pedida (10:00), ninguna más",
              re.findall(r"\b\d{2}:\d{2}\b", error.mensaje) == D23_5["manana"] + D23_5["tarde"] + ["10:00"],
              f"({error.mensaje})")
# No salió: FALLO explícito.
else:
    # La excepción no ha salido.
    comprobar("10:00 -> fuera_de_rejilla", False, "(la excepción se ha tragado: 10:00 se acepta)")
# 12:45 (termina 13:45, fuera de la franja): sigue siendo fuera_de_franja (el orden).
try:
    # Debe lanzar FechaNoValida.
    rv.validar_fecha(en_madrid(DIA_REF, 12, 45), AHORA_ANTES, REGLAS)
# La esperada.
except rv.FechaNoValida as error:
    # fuera_de_franja, sin horas_validas.
    comprobar("12:45 -> fuera_de_franja (va antes que la rejilla), sin horas_validas",
              error.motivo == "fuera_de_franja" and error.horas_validas is None, f"({error.motivo})")
# No salió: FALLO explícito.
else:
    # La excepción no ha salido.
    comprobar("12:45 -> fuera_de_franja", False, "(la excepción se ha tragado)")
# Cada hora de la rejilla pasa validar_fecha (ninguna excepción).
for hora in D23_5["manana"] + D23_5["tarde"]:
    # try: no debe lanzar nada.
    try:
        # La hora "HH:MM" partida en dos números.
        rv.validar_fecha(en_madrid(DIA_REF, int(hora[:2]), int(hora[3:])), AHORA_ANTES, REGLAS)
    # Si lanza, FALLO.
    except rv.FechaNoValida as error:
        # Con el motivo.
        comprobar(f"{hora}: pasa validar_fecha", False, f"({error.motivo})")
    # Sin excepción: correcto.
    else:
        # Una más.
        comprobar(f"{hora}: pasa validar_fecha", True)
# El texto del 409 (no cambia con D32): solo las horas libres.
mensaje = rv.mensaje_hueco_ocupado(["08:30", "12:15"])
# Se enseña.
print(f"    {mensaje}")
# Exactamente las libres y ninguna otra cifra.
comprobar("409: nombra exactamente las horas libres y ninguna otra cifra",
          re.findall(r"\b\d{2}:\d{2}\b", mensaje) == ["08:30", "12:15"]
          and not re.search(r"\d", re.sub(r"\b\d{2}:\d{2}\b", "", mensaje)), f"({mensaje})")
# Con la lista vacía: pide otro día y no tiene ninguna cifra.
vacio = rv.mensaje_hueco_ocupado([])
# Ninguna cifra y pide otro día.
comprobar("409 con la lista vacía: sin cifras y pide otro día", not re.search(r"\d", vacio) and "otro día" in vacio)

# ======================================================================
print("\nS6. leer_reglas_visita: las siete reglas de D32")
# ======================================================================
# Las siete correctas, como filas de reglas_negocio (Decimal).
SIETE = [(c, Decimal(v)) for c, v in REGLAS.items()]


def problemas_con(filas):
    """Los problemas de leer_reglas_visita con esas filas."""
    # Solo la lista de problemas.
    return rv.leer_reglas_visita(CursorDeMentira(filas))[1]


# Las siete: ningún problema.
reglas7, problemas = rv.leer_reglas_visita(CursorDeMentira(SIETE))
# Ninguno, y los valores como enteros.
comprobar("las siete bien: 0 problemas, desplazamiento 15 y días 10",
          problemas == [] and reglas7[rv.CLAVE_DESPLAZAMIENTO] == 15 and reglas7[rv.CLAVE_DIAS_OFERTA] == 10,
          f"({problemas})")
# Falta el desplazamiento.
comprobar("sin desplazamiento_min: 'desplazamiento_min: no existe'",
          problemas_con([f for f in SIETE if f[0] != rv.CLAVE_DESPLAZAMIENTO]) == ["desplazamiento_min: no existe"])
# Faltan los días de oferta.
comprobar("sin dias_oferta_visita: 'dias_oferta_visita: no existe'",
          problemas_con([f for f in SIETE if f[0] != rv.CLAVE_DIAS_OFERTA]) == ["dias_oferta_visita: no existe"])
# Días 0 y 61: fuera de 1..60.
for valor in (0, 61):
    # Las siete con ese valor de días.
    filas = [(c, Decimal(valor) if c == rv.CLAVE_DIAS_OFERTA else v) for c, v in SIETE]
    # Un problema que lo nombra.
    p = problemas_con(filas)
    # Lo nombra.
    comprobar(f"dias_oferta_visita = {valor}: un problema 'entre 1 y 60'",
              len(p) == 1 and p[0].startswith("dias_oferta_visita: debe estar entre 1 y 60"), f"({p})")
# Desplazamiento 0: válido (sin desplazamiento).
comprobar("desplazamiento_min = 0: válido",
          problemas_con([(c, Decimal(0) if c == rv.CLAVE_DESPLAZAMIENTO else v) for c, v in SIETE]) == [])
# paso_huecos_min ya no está entre las claves que se leen (expand/contract, 2.6).
comprobar("paso_huecos_min ya NO es una de las claves que se leen", "paso_huecos_min" not in rv.CLAVES_REGLAS,
          f"({rv.CLAVES_REGLAS})")

# ======================================================================
print("\nS7. Contra la base de datos (ROLLBACK)")
# ======================================================================
# El bloque de fechas del script (B5-0), lejos de cualquier visita real.
HOY = datetime.now(MADRID).date()
# Un jueves del bloque (lunes + 3).
DIA = fechas_prueba.lunes_de_prueba("check_disponibilidad_service", HOY) + timedelta(days=3)
# Antes de crear nada: ninguna visita activa ajena en el bloque.
fechas_prueba.exigir_libres("check_disponibilidad_service", EMAIL,
                            [fechas_prueba.tramo_del_bloque("check_disponibilidad_service", HOY)])
# try/finally: pase lo que pase, ROLLBACK.
try:
    # Las reglas REALES, leídas en esta transacción.
    reglas, problemas = rv.leer_reglas_visita(cur)
    # Sin problemas (con paso14 aplicada): si los hay, lo demás no tiene sentido.
    comprobar("las reglas reales se leen sin problemas (las siete de D32)", problemas == [], f"({problemas})")
    # La rejilla con las reglas REALES: la de D23.5 (plan D32, 2.2).
    reales = rv.rejilla_texto(reglas) if not problemas else {}
    # Se enseña.
    print(f"    rejilla con las reglas reales: {reales}")
    # Exacta.
    comprobar("la rejilla con las reglas REALES es exactamente la de D23.5", reales == D23_5, f"({reales})")
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
        # SQL: la visita con ese estado y esa fecha (por SQL: sin la rejilla).
        cur.execute("INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
                    "VALUES (%s, %s, %s, 'prueba disponibilidad') RETURNING id;", (op, cuando, estado))
        # Su id.
        return cur.fetchone()[0]

    # SQL: el reloj de la base de datos.
    cur.execute("SELECT now();")
    # El "ahora" de la transacción.
    AHORA = cur.fetchone()[0]
    # Una visita ANTIGUA 'solicitada' a las 10:00 del jueves (fuera de la
    # rejilla: lo que puede existir de antes de D32, R3).
    v_a = visita(oportunidad(), "solicitada", en_madrid(DIA, 10, 0))
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

    # 08:30: libre (termina 09:30; 30 min antes de la de las 10:00).
    comprobar("08:30: libre", intento(8, 30) is None)
    # 11:00: choca (10:00 + 60 + 15 = 11:15), con la lista de libres.
    error = intento(11, 0)
    # Se enseña.
    print(f"    11:00 -> {error and (error.motivo, error.mensaje, error.huecos_libres)}")
    # Rama else explícita: si no sale, FALLO.
    if error is None:
        # FALLO explícito: la excepción no ha salido.
        comprobar("11:00: HuecoOcupado", False, "(la excepción se ha tragado: el hueco sale libre)")
    # Salió: se comprueba.
    else:
        # Motivo y lista PLANA (P3): la rejilla sin 09:45 ni 11:00.
        comprobar("11:00: HuecoOcupado 'hueco_ocupado' con huecos_libres plana sin 09:45 ni 11:00",
                  error.motivo == "hueco_ocupado" and error.huecos_libres == ["08:30", "12:15", "17:00", "18:15"],
                  f"({error.huecos_libres})")
    # 11:00 excluyendo la propia: libre (C8: sustitución o confirmación).
    comprobar("11:00 excluyendo la visita que la ocupa: libre (C8)", intento(11, 0, [v_a]) is None)
    # 17:00: libre (la cancelada y la completada no ocupan).
    comprobar("17:00 con una 'cancelada' y una 'completada': libre", intento(17, 0) is None)
    # SQL: el bloqueo del día, tomado por ESTA sesión (classid: primer entero;
    # objid: segundo; objsubid 2 = forma de dos enteros).
    cur.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND classid = %s AND objid = %s "
                "AND objsubid = 2 AND pid = pg_backend_pid() AND granted;",
                (rv.CLAVE_BLOQUEO_VISITAS, DIA.year * 10000 + DIA.month * 100 + DIA.day))
    # Uno.
    comprobar("el bloqueo del día está tomado por esta transacción (pg_locks)", cur.fetchone()[0] == 1)
    # disponibilidad de 10 días laborables del bloque con UNA consulta real.
    contador = CursorContador(cur)
    # Los 10 primeros laborables del bloque (dos semanas).
    lunes = fechas_prueba.lunes_de_prueba("check_disponibilidad_service", HOY)
    # Las fechas.
    diez_dias = [lunes + timedelta(days=d) for d in (0, 1, 2, 3, 4, 7, 8, 9, 10, 11)]
    # El cálculo.
    disp = rv.disponibilidad(contador, diez_dias, AHORA, reglas)
    # Una sola consulta y el jueves con la antigua sin 09:45 ni 11:00.
    comprobar("10 días con UNA consulta real; el jueves sin 09:45 ni 11:00",
              contador.ejecuciones == 1 and disp[DIA]["manana"] == ["08:30", "12:15"],
              f"({contador.ejecuciones}, {disp.get(DIA)})")
    # N12: la fila paso_huecos_min borrada DENTRO de esta transacción (se
    # deshace con el ROLLBACK): leer_reglas_visita no la echa de menos.
    cur.execute("DELETE FROM reglas_negocio WHERE clave = 'paso_huecos_min';")
    # Cuántas filas se borraron (1 si existe; da igual para la prueba).
    print(f"    paso_huecos_min borrada dentro de la transacción: {cur.rowcount} fila(s)")
    # Se vuelven a leer las reglas.
    _, problemas_sin_paso = rv.leer_reglas_visita(cur)
    # Ningún problema: ya nadie la lee.
    comprobar("sin la fila paso_huecos_min: leer_reglas_visita sin problemas (N12)", problemas_sin_paso == [],
              f"({problemas_sin_paso})")
# Siempre: ROLLBACK de todo (también del DELETE de paso_huecos_min).
finally:
    # Se deshace todo (y se suelta el bloqueo).
    cn.rollback()

# Fuera de la transacción: no queda el cliente de prueba.
cur.execute("SELECT count(*) FROM clientes WHERE email = %s;", (EMAIL,))
# Cero.
comprobar("no queda ningún dato de prueba (ROLLBACK)", cur.fetchone()[0] == 0)
# SQL: la fila paso_huecos_min sigue en la tabla (el DELETE se deshizo).
cur.execute("SELECT count(*) FROM reglas_negocio WHERE clave = 'paso_huecos_min';")
# [INFO]: no se exige (se borrará con paso14b después del merge).
print(f"  [INFO]  paso_huecos_min tras el ROLLBACK: {cur.fetchone()[0]} fila(s)")
# Se cierra la lectura y la conexión.
cn.rollback()
# Cerrada.
cn.close()

# ======================================================================
print("\nS8. Estructura del código (ast)")
# ======================================================================


def lineas_de_llamadas(arbol, nombre):
    """Las líneas en las que se llama a una función con ese nombre."""
    # Cada nodo Call cuyo nombre es ese (llamada directa).
    return sorted(n.lineno for n in ast.walk(arbol)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == nombre)


# Los dos servicios que escriben visitas.
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
# La línea del motivo visita_confirmada.
linea_conf = next((i for i, l in enumerate(texto.splitlines(), 1) if '"visita_confirmada"' in l), 0)
# La línea de la llamada.
linea_hueco = next((i for i, l in enumerate(texto.splitlines(), 1) if "comprobar_hueco_libre(cursor" in l), 0)
# Después.
comprobar("visits_service: hueco_ocupado después de visita_confirmada", 0 < linea_conf < linea_hueco,
          f"(visita_confirmada {linea_conf}, hueco {linea_hueco})")
# comprobar_hueco_libre usa disponibilidad() (la función ÚNICA, 2.5).
arbol_rv = ast.parse((RAIZ_REPO / "app/services/reglas_visita.py").read_text(encoding="utf-8"))
# La definición de comprobar_hueco_libre.
funcion = next(n for n in ast.walk(arbol_rv) if isinstance(n, ast.FunctionDef) and n.name == "comprobar_hueco_libre")
# Llama a disponibilidad dentro.
comprobar("comprobar_hueco_libre llama a disponibilidad()", bool(lineas_de_llamadas(funcion, "disponibilidad")))
# Fuera de reglas_visita.py, nadie calcula horas: ni llama a choca() ni lee
# CLAVE_DESPLAZAMIENTO (ni por nombre ni como rv.CLAVE_DESPLAZAMIENTO).
ajenos = []
# Todos los .py de app/.
for ruta in sorted((RAIZ_REPO / "app").rglob("*.py")):
    # reglas_visita.py es el único que puede.
    if ruta.name == "reglas_visita.py":
        # Se salta.
        continue
    # El árbol del archivo.
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    # Nombres y atributos usados.
    usados = {n.id for n in ast.walk(arbol) if isinstance(n, ast.Name)} | \
             {n.attr for n in ast.walk(arbol) if isinstance(n, ast.Attribute)}
    # choca o CLAVE_DESPLAZAMIENTO fuera de su sitio.
    if usados & {"choca", "CLAVE_DESPLAZAMIENTO", "rejilla"}:
        # Se apunta con lo que usa.
        ajenos.append(f"{ruta.relative_to(RAIZ_REPO)}: {sorted(usados & {'choca', 'CLAVE_DESPLAZAMIENTO', 'rejilla'})}")
# Ninguno.
comprobar("fuera de reglas_visita.py nadie usa choca, rejilla ni CLAVE_DESPLAZAMIENTO", ajenos == [], f"({ajenos})")

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
