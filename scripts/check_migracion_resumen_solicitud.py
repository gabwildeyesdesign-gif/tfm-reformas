"""
Verificación de la migración paso15 (plan: docs/Plan_Resumen_Solicitud_D28.txt,
sección 4.8): la columna leads.resumen_solicitud (TEXT, admite NULL, sin
DEFAULT) y su CHECK chk_leads_resumen_solicitud_longitud, de 1 a 300
caracteres (D28.1, defensa doble con MAX_RESUMEN_SOLICITUD = 300 de Python).

Qué comprueba:
  S0. La conexión de prueba rechaza commit() (CommitProhibido), con la rama
      else que marca FALLO si no sale la excepción (regla del 2026-10-08).
  1.  El catálogo: la columna existe, es text, admite NULL y no tiene
      DEFAULT; el CHECK existe y es de 1 a 300 sobre char_length.
  2.  Su comportamiento, dentro de UNA transacción que termina SIEMPRE en
      ROLLBACK, con datos propios (email check-paso15-<marca>@example.com,
      marca aleatoria de la ejecución), cada intento en su SAVEPOINT: NULL,
      "x" y 300 "á" (600 bytes: cuenta caracteres, no bytes) se aceptan; ""
      y 301 caracteres los rechaza el CHECK. Si un rechazo esperado NO sale,
      FALLO explícito ("la excepción se ha tragado").
  3.  No queda el cliente de prueba (y, en ensayo, que la columna sigue como
      estaba antes del ensayo).

Modo ensayo: --ensayo <ruta de un archivo de migración>. Lee la constante
SENTENCIAS de ese archivo (sin ejecutar su main) y la ejecuta DENTRO de la
misma transacción, antes de las comprobaciones; al final, ROLLBACK. Sirve para
probar paso15 sin aplicarla (control) y la copia rota sin CHECK (prueba en
negativo N5). Solo antes de migrar: si la columna ya existe, se para (código
2). Se niega a ejecutar una orden con COMMIT, END o ROLLBACK escritos en el
SQL, que se saltarían ConexionSinCommit.

Recuentos previstos (plan, 4.8 y 7.4): sin migrar y sin ensayo, 2/12 (es la
primera prueba en negativo de este script); ensayo con paso15, 13/13; ensayo
con la copia sin CHECK (N5), 10/13; después de migrar, 12/12.

Las 4 preguntas (CLAUDE.md):
  (1) Escribe en clientes y leads (y, en ensayo, ALTER TABLE leads) dentro de
      una transacción que termina en ROLLBACK.
  (2) No pasa la conexión a ningún código de app/. El SQL del ensayo corre en
      la misma conexión ConexionSinCommit, y las órdenes de fin de
      transacción escritas en SQL se rechazan antes de ejecutarlas.
  (3) Si se corta, Postgres deshace la transacción abierta: no se confirma
      nada. La siguiente ejecución lo comprueba (catálogo y punto 3).
  (4) En ensayo, el ALTER TABLE bloquea leads mientras dura la transacción
      (alrededor de un segundo): se ejecuta en la fase 2, con Docker/n8n
      cerrados (nadie llama al uvicorn del 8000) y con lock_timeout de 5 s.
      No toca datos reales.

Código de salida: 0 si todo está bien, 1 si hay algún fallo, 2 si no se ha
hecho nada a propósito (ensayo imposible).
"""

# importlib.util: cargar el archivo de migración del ensayo por su ruta.
import importlib.util
# re: buscar COMMIT, END o ROLLBACK en el SQL del ensayo.
import re
# sys: sys.path, argumentos, la salida estándar y el código de salida.
import sys
# uuid: la marca aleatoria de esta ejecución.
import uuid
# Path: rutas.
from pathlib import Path

# La raíz del repositorio en sys.path (para "import app...").
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Primera en la lista de búsqueda.
sys.path.insert(0, str(RAIZ_REPO))
# Tildes correctas en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: hablar con PostgreSQL; extensions: la clase de conexión base.
import psycopg2
import psycopg2.extensions
# Json: un diccionario de Python a una columna JSONB.
from psycopg2.extras import Json

# La cadena de conexión del .env (nunca se imprime).
from app.config import DATABASE_URL

# La columna, el CHECK y el máximo esperados (los mismos que la migración).
COLUMNA = "resumen_solicitud"
# El CHECK.
RESTRICCION = "chk_leads_resumen_solicitud_longitud"
# El máximo de caracteres.
MAXIMO = 300
# La marca propia de esta ejecución: 8 caracteres aleatorios.
MARCA = uuid.uuid4().hex[:8]
# El email propio (dominio reservado @example.com, con la marca).
EMAIL = f"check-paso15-{MARCA}@example.com"

# Contadores.
ok, fallos = 0, []


def comprobar(titulo, condicion, detalle=""):
    """[OK] o [FALLO], y se cuenta."""
    # global: se cambia el contador de fuera.
    global ok
    # Correcta.
    if condicion:
        # Una más.
        ok += 1
        # Se enseña.
        print(f"  [OK]    {titulo}  {detalle}")
    # Incorrecta.
    else:
        # Para el resumen.
        fallos.append(f"{titulo} {detalle}")
        # En su sitio.
        print(f"  [FALLO] {titulo}  {detalle}")


# Una conexión cuyo commit() lanza un error en vez de confirmar (regla de
# CLAUDE.md del 2026-10-08, "Conexiones de prueba sin commit").
class CommitProhibido(Exception):
    """Alguien ha llamado a commit() en una conexión de prueba."""


class ConexionSinCommit(psycopg2.extensions.connection):
    """Una conexión de psycopg2 normal, salvo commit(), que lanza un error.
    rollback() funciona como siempre (es lo que usa la prueba)."""

    def commit(self):
        # No se confirma NADA: se lanza el error y la transacción sigue
        # abierta, para que el ROLLBACK de la prueba la deshaga.
        raise CommitProhibido("commit() en una conexión de prueba: la prueba termina siempre en ROLLBACK")


def estado_columna(cur):
    """El tipo de la columna (por ejemplo 'text'), o None si no existe."""
    # SQL: el tipo de la columna en el catálogo.
    cur.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'leads' AND column_name = %s;",
        (COLUMNA,),
    )
    # La fila, o None.
    fila = cur.fetchone()
    # El tipo, o None.
    return fila[0] if fila else None


# ----------------------------------------------------------------------
# Modo ensayo: se lee (sin ejecutar nada todavía) el archivo de migración.
# ----------------------------------------------------------------------
# Sin --ensayo: modo normal.
SENTENCIAS_ENSAYO = None
# Con --ensayo, la ruta va justo detrás.
if "--ensayo" in sys.argv:
    # La posición de la bandera en la lista de argumentos.
    posicion = sys.argv.index("--ensayo")
    # Falta la ruta: no se hace nada.
    if posicion + 1 >= len(sys.argv):
        # Se explica.
        print("Falta la ruta del archivo de migración detrás de --ensayo.")
        # Código 2.
        sys.exit(2)
    # La ruta del archivo de migración.
    ruta_ensayo = Path(sys.argv[posicion + 1]).resolve()
    # Se prepara la carga del archivo como un módulo de Python.
    spec = importlib.util.spec_from_file_location("migracion_ensayo", ruta_ensayo)
    # El módulo, todavía vacío.
    modulo = importlib.util.module_from_spec(spec)
    # Se ejecuta su parte de arriba (constantes y funciones); su main() NO,
    # porque solo corre con __name__ == "__main__".
    spec.loader.exec_module(modulo)
    # Las órdenes que se van a ensayar.
    SENTENCIAS_ENSAYO = tuple(modulo.SENTENCIAS)
    # Ninguna orden puede terminar la transacción desde el SQL: COMMIT o END
    # confirmarían sin pasar por ConexionSinCommit.commit().
    for sentencia in SENTENCIAS_ENSAYO:
        # \b: palabra completa, sin distinguir mayúsculas.
        if re.search(r"\b(COMMIT|END|ROLLBACK)\b", sentencia, re.IGNORECASE):
            # Se explica y no se ejecuta nada.
            print(f"ENSAYO RECHAZADO: la orden termina la transacción desde el SQL: {sentencia}")
            # Código 2.
            sys.exit(2)
    # Se enseña qué se va a ensayar.
    print(f"MODO ENSAYO: {ruta_ensayo.name} ({len(SENTENCIAS_ENSAYO)} órdenes), todo se deshace al final")

# ======================================================================
print("S0. La conexión de prueba rechaza commit()")
# ======================================================================
# connection_factory: psycopg2 crea la conexión con esta clase.
conexion_prueba = psycopg2.connect(DATABASE_URL, connection_factory=ConexionSinCommit)
# try/except/else: sin la excepción, FALLO explícito (regla de CLAUDE.md).
try:
    # Tiene que lanzar CommitProhibido.
    conexion_prueba.commit()
# La esperada.
except CommitProhibido:
    # Correcto.
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", True)
# else: commit() no lanzó nada -> la defensa no existe.
else:
    # FALLO explícito.
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", False,
              "(sin error: la excepción se ha tragado)")
# Se cierra (no había nada que deshacer).
finally:
    # Nada que deshacer, pero se deja limpio.
    conexion_prueba.rollback()
    # Cerrada.
    conexion_prueba.close()

# Conexión propia para todo lo demás: UNA transacción que se deshace.
cn = psycopg2.connect(DATABASE_URL, connection_factory=ConexionSinCommit)
# El cursor.
cur = cn.cursor()
# El estado de la columna ANTES de nada (para el punto 3 del ensayo).
columna_antes = estado_columna(cur)
# Se enseña.
print(f"\n  columna leads.{COLUMNA} al empezar: {columna_antes or 'NO EXISTE'}")
# Un ensayo solo tiene sentido antes de migrar.
if SENTENCIAS_ENSAYO is not None and columna_antes is not None:
    # Se explica.
    print("ENSAYO IMPOSIBLE: la columna ya existe (el ensayo es solo antes de migrar)")
    # Nada que deshacer, pero se deja limpio y se cierra.
    cn.rollback()
    # Cerrada.
    cn.close()
    # Código 2.
    sys.exit(2)


def intento(texto):
    """Inserta un lead propio con ese resumen en un SAVEPOINT. Devuelve
    None si se aceptó, o el error de Postgres si se rechazó."""
    # SQL: un punto al que volver.
    cur.execute("SAVEPOINT paso15;")
    # try: el INSERT puede fallar (por el CHECK, o porque no hay columna).
    try:
        # SQL: el lead propio, con un m2 válido (chk_leads_m2_rango).
        cur.execute(
            f"INSERT INTO leads (cliente_id, canal, datos_estructurados, {COLUMNA}) "
            "VALUES (%s, 'check_paso15', %s, %s);",
            (CLIENTE, Json({"m2": 10}), texto),
        )
    # Rechazado: se devuelve el error.
    except psycopg2.Error as error:
        # SQL: se vuelve al punto de guardado (la transacción sigue viva).
        cur.execute("ROLLBACK TO SAVEPOINT paso15;")
        # El error.
        return error
    # SQL: aceptado; también se deshace (solo se quería saber si pasaba).
    cur.execute("ROLLBACK TO SAVEPOINT paso15;")
    # Ningún error.
    return None


def debe_aceptarse(titulo, texto):
    """El intento no tiene que dar ningún error."""
    # Se intenta.
    error = intento(texto)
    # Sin error: correcto; con error, se enseña su código.
    comprobar(titulo, error is None, f"({error.pgcode if error is not None else 'aceptado'})")


def debe_rechazarse(titulo, texto):
    """El intento tiene que fallar por ESTE CHECK (código 23514)."""
    # Se intenta.
    error = intento(texto)
    # Rechazado: se comprueba que es el CHECK y no otra cosa.
    if error is not None:
        # 23514 es check_violation; el nombre del CHECK sale en el mensaje.
        comprobar(titulo, error.pgcode == "23514" and RESTRICCION in str(error), f"({error.pgcode})")
    # No se rechazó: FALLO explícito.
    else:
        # La regla de CLAUDE.md (caso D16).
        comprobar(titulo, False, "(la excepción se ha tragado: se acepta)")


# try / finally: pase lo que pase, ROLLBACK de todo.
try:
    # SQL: esperar un bloqueo como mucho 5 s, solo en esta transacción.
    cur.execute("SET LOCAL lock_timeout = '5s';")
    # En ensayo: las órdenes de la migración, dentro de esta transacción.
    if SENTENCIAS_ENSAYO is not None:
        # Una a una.
        for sentencia in SENTENCIAS_ENSAYO:
            # SQL: cada orden del archivo ensayado (ya revisada arriba).
            cur.execute(sentencia)
            # Constancia en la salida.
            print(f"  ensayado: {sentencia}")

    # ------------------------------------------------------------------
    print("\n1. El catálogo")
    # SQL: tipo, si admite NULL y su DEFAULT.
    cur.execute(
        "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'leads' AND column_name = %s;",
        (COLUMNA,),
    )
    # La fila, o None si la columna no existe.
    fila = cur.fetchone()
    # Existe.
    comprobar(f"la columna leads.{COLUMNA} existe", fila is not None)
    # Los tres datos, o None si no existe.
    tipo, admite_null, por_defecto = fila if fila else (None, None, None)
    # Texto.
    comprobar("es de tipo text", tipo == "text", f"({tipo})")
    # Admite NULL ('YES' en information_schema).
    comprobar("admite NULL", admite_null == "YES", f"({admite_null})")
    # Sin DEFAULT: con la columna, column_default es None.
    comprobar("no tiene DEFAULT", fila is not None and por_defecto is None, f"({por_defecto})")
    # SQL: la definición del CHECK, como la escribe Postgres.
    cur.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'public.leads'::regclass AND conname = %s;",
        (RESTRICCION,),
    )
    # La fila, o None.
    fila = cur.fetchone()
    # El texto, o vacío.
    definicion = fila[0] if fila else ""
    # Se enseña.
    print(f"    {RESTRICCION}: {definicion or 'NO EXISTE'}")
    # 1..300 sobre char_length de la columna.
    comprobar(f"el CHECK existe y es de 1 a {MAXIMO} caracteres",
              f"char_length({COLUMNA})" in definicion and ">= 1" in definicion and f"<= {MAXIMO}" in definicion)

    # ------------------------------------------------------------------
    print("\n2. Su comportamiento (todo se deshace al final)")
    # SQL: el cliente propio (sin la columna nueva: siempre se puede crear).
    cur.execute(
        "INSERT INTO clientes (nombre, email, telefono) VALUES ('Prueba paso15', %s, '600000000') RETURNING id;",
        (EMAIL,),
    )
    # Su id, que usa intento().
    CLIENTE = cur.fetchone()[0]
    # NULL: un lead sin resumen (los antiguos y el chat de hoy).
    debe_aceptarse("resumen NULL -> aceptado", None)
    # Un carácter: el mínimo.
    debe_aceptarse("resumen de 1 carácter -> aceptado", "x")
    # 300 "á": aceptado (caracteres, no bytes).
    debe_aceptarse(f"{MAXIMO} 'á' ({MAXIMO * 2} bytes) -> aceptado", "á" * MAXIMO)
    # Vacío: lo rechaza el CHECK (char_length 0).
    debe_rechazarse("resumen vacío -> rechazado por el CHECK", "")
    # 301: lo rechaza el CHECK.
    debe_rechazarse(f"{MAXIMO + 1} caracteres -> rechazado por el CHECK", "x" * (MAXIMO + 1))
# Siempre: ROLLBACK de todo (también del ensayo).
finally:
    # Se deshace todo.
    cn.rollback()

# ----------------------------------------------------------------------
print("\n3. Nada queda")
# SQL: el cliente de prueba de esta ejecución no queda.
cur.execute("SELECT count(*) FROM clientes WHERE email = %s;", (EMAIL,))
# Cero.
comprobar("no queda ningún dato de prueba (ROLLBACK)", cur.fetchone()[0] == 0)
# En ensayo: la columna vuelve a estar como al empezar (no existía).
if SENTENCIAS_ENSAYO is not None:
    # Se compara con el estado del principio.
    comprobar("el ensayo no ha dejado nada en el esquema", estado_columna(cur) == columna_antes,
              f"({estado_columna(cur) or 'NO EXISTE'})")
# Se cierra la lectura.
cn.rollback()
# Y la conexión.
cn.close()

# Resumen y código de salida.
print("\n" + "=" * 78)
# El recuento.
print(f"RESULTADO: {ok}/{ok + len(fallos)} correctas")
# Separador.
print("=" * 78)
# Con fallos, se listan y código 1.
if fallos:
    # Cabecera.
    print("FALLOS:")
    # Uno por línea.
    for f in fallos:
        # Con su detalle.
        print(f"  - {f}")
    # Código 1.
    sys.exit(1)
