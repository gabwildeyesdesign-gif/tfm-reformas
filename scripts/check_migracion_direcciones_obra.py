"""
Verificación de la migración paso16 (D42.1 y D42.3): la tabla
direcciones_obra, con su clave única por oportunidad, su clave foránea, sus
tres CHECK y la RLS activada sin políticas.

Qué comprueba:
  S0. La conexión de prueba rechaza commit() (CommitProhibido), con la rama
      else que marca FALLO si no sale la excepción (regla del 2026-10-08).
  1.  El catálogo: la tabla existe; sus 7 columnas con su tipo y NOT NULL;
      las 6 restricciones con nombre (clave primaria, única por
      oportunidad_id, foránea hacia oportunidades(id) y los 3 CHECK con sus
      piezas); RLS activada y 0 políticas. El número de filas sale como
      [INFO] (no se exige 0: después de migrar, las reales son de Gabi).
  2.  Su comportamiento, dentro de UNA transacción que termina SIEMPRE en
      ROLLBACK, con datos propios (email check-paso16-<marca>@example.com,
      marca aleatoria de la ejecución), cada intento en su SAVEPOINT:
      aceptados los límites buenos (150 y 100 "á", códigos 01001, 28013,
      50000 y 52999); rechazados por SU CHECK (23514 y el nombre de la
      restricción) los textos vacíos, sin recortar o largos y los códigos
      postales fuera del patrón (00123, 53000, 99999, cuatro y seis cifras,
      con letra, con espacio, con salto de línea final y con cifras árabes
      orientales); rechazados la segunda fila de la misma oportunidad
      (23505), una oportunidad inexistente (23503) y un NULL (23502); y el
      ON CONFLICT (oportunidad_id) DO UPDATE sustituye la fila (D42.4). Si
      un rechazo esperado NO sale, FALLO explícito ("la excepción se ha
      tragado").
  3.  No queda el cliente de prueba (y, en ensayo, que la tabla sigue sin
      existir).

H4 del veredicto del tutor: NUNCA imprime una fila de direcciones_obra, ni
el texto de un error de Postgres (copia la fila): solo pgcode, nombre de la
restricción, recuentos y verdadero/falso.

Modo ensayo: --ensayo <ruta de un archivo de migración>. Lee la constante
SENTENCIAS de ese archivo (sin ejecutar su main) y la ejecuta DENTRO de la
misma transacción, antes de las comprobaciones; al final, ROLLBACK. Sirve
para probar paso16 sin aplicarla (control) y las copias rotas (pruebas en
negativo, N3 y N13). Solo antes de migrar: si la tabla ya existe, se para
(código 2). Se niega a ejecutar una orden con COMMIT, END o ROLLBACK
escritos en el SQL, que se saltarían ConexionSinCommit.

Recuentos: se calculan en la fase 2 y se escriben en el commit (D37.5:
nunca en la misma orden que los calcula). Antes de migrar y sin ensayo,
FALLA (es su primera prueba en negativo).

Las 4 preguntas (CLAUDE.md):
  (1) Escribe en clientes, leads, oportunidades y direcciones_obra (y, en
      ensayo, CREATE TABLE) dentro de una transacción que termina en ROLLBACK.
  (2) No pasa la conexión a ningún código de app/. El SQL del ensayo corre en
      la misma conexión ConexionSinCommit, y las órdenes de fin de
      transacción escritas en SQL se rechazan antes de ejecutarlas.
  (3) Si se corta, Postgres deshace la transacción abierta: no se confirma
      nada. La siguiente ejecución lo comprueba (punto 3).
  (4) En ensayo, la clave foránea bloquea oportunidades frente a escrituras
      mientras dura la transacción (alrededor de un segundo): se ejecuta en
      la fase 2, con Docker/n8n cerrados y con lock_timeout de 5 s. No toca
      datos reales.

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

# Lo esperado, escrito aquí (copiado de D42.1 y D42.3, no del código).
TABLA = "direcciones_obra"
# Los límites de D42.3.
MAX_CALLE, MAX_MUNICIPIO = 150, 100
# Las 7 columnas: (nombre, tipo de information_schema), todas NOT NULL.
COLUMNAS = [("id", "integer"), ("oportunidad_id", "integer"), ("calle_numero", "text"),
            ("codigo_postal", "text"), ("municipio", "text"),
            ("created_at", "timestamp with time zone"), ("updated_at", "timestamp with time zone")]
# Los nombres de las restricciones.
PK, UNICA, FK = f"{TABLA}_pkey", f"{TABLA}_oportunidad_id_key", f"{TABLA}_oportunidad_id_fkey"
CHK_CALLE, CHK_CP, CHK_MUNICIPIO = (f"chk_{TABLA}_calle_numero", f"chk_{TABLA}_codigo_postal",
                                    f"chk_{TABLA}_municipio")
# La marca propia de esta ejecución: 8 caracteres aleatorios.
MARCA = uuid.uuid4().hex[:8]
# El email propio (dominio reservado @example.com, con la marca).
EMAIL = f"check-paso16-{MARCA}@example.com"
# Una dirección válida de partida (inventada; nunca se imprime).
VALIDA = {"calle_numero": "Calle de Prueba 1", "codigo_postal": "28013", "municipio": "Municipio de Prueba"}

# Contadores.
ok, fallos = 0, []


def comprobar(titulo, condicion, detalle=""):
    """[OK] o [FALLO], y se cuenta."""
    # global: se cambia el contador de fuera.
    global ok
    # Correcta.
    if condicion:
        # Una más, y se enseña.
        ok += 1
        print(f"  [OK]    {titulo}  {detalle}")
    # Incorrecta.
    else:
        # Para el resumen, y en su sitio.
        fallos.append(f"{titulo} {detalle}")
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


def tabla_existe(cur):
    """True si la tabla existe (to_regclass da NULL si no)."""
    # SQL: el nombre resuelto, o NULL.
    cur.execute("SELECT to_regclass(%s) IS NOT NULL;", (f"public.{TABLA}",))
    # True o False.
    return cur.fetchone()[0]


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
        # Se explica, y código 2.
        print("Falta la ruta del archivo de migración detrás de --ensayo.")
        sys.exit(2)
    # La ruta del archivo de migración.
    ruta_ensayo = Path(sys.argv[posicion + 1]).resolve()
    # Se prepara la carga del archivo como un módulo de Python.
    spec = importlib.util.spec_from_file_location("migracion_ensayo", ruta_ensayo)
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
            # Se explica y no se ejecuta nada (código 2).
            print(f"ENSAYO RECHAZADO: una orden termina la transacción desde el SQL: {sentencia.splitlines()[0]}")
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
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", True)
# else: commit() no lanzó nada -> la defensa no existe.
else:
    comprobar("la conexión de prueba rechaza commit() (CommitProhibido)", False,
              "(sin error: la excepción se ha tragado)")
# Se cierra (no había nada que deshacer, pero se deja limpio).
finally:
    conexion_prueba.rollback()
    conexion_prueba.close()

# Conexión propia para todo lo demás: UNA transacción que se deshace.
cn = psycopg2.connect(DATABASE_URL, connection_factory=ConexionSinCommit)
cur = cn.cursor()
# El estado de la tabla ANTES de nada (para el punto 3 del ensayo).
existia_antes = tabla_existe(cur)
print(f"\n  tabla {TABLA} al empezar: {'EXISTE' if existia_antes else 'NO EXISTE'}")
# Un ensayo solo tiene sentido antes de migrar.
if SENTENCIAS_ENSAYO is not None and existia_antes:
    # Se explica, se cierra y código 2.
    print("ENSAYO IMPOSIBLE: la tabla ya existe (el ensayo es solo antes de migrar)")
    cn.rollback()
    cn.close()
    sys.exit(2)


def intento(oportunidad_id, campos, sql_extra=""):
    """
    Inserta una fila con esos campos (los de VALIDA, cambiados por
    "campos") en un SAVEPOINT, y la deshace. Devuelve None si se aceptó, o
    el error de Postgres si se rechazó. sql_extra: un ON CONFLICT (texto
    fijo de este archivo) para probar el upsert.
    """
    # Los tres textos: los de partida, con los cambiados encima.
    fila = {**VALIDA, **campos}
    # SQL: un punto al que volver.
    cur.execute("SAVEPOINT paso16;")
    # try: el INSERT puede fallar (por una restricción, o porque no hay tabla).
    try:
        # SQL: la fila, con los valores aparte.
        cur.execute(
            f"INSERT INTO {TABLA} (oportunidad_id, calle_numero, codigo_postal, municipio) "
            f"VALUES (%s, %s, %s, %s) {sql_extra};",
            (oportunidad_id, fila["calle_numero"], fila["codigo_postal"], fila["municipio"]),
        )
    # Rechazado: se vuelve al punto de guardado y se devuelve el error.
    except psycopg2.Error as error:
        # SQL: la transacción sigue viva.
        cur.execute("ROLLBACK TO SAVEPOINT paso16;")
        return error
    # SQL: aceptado; también se deshace (solo se quería saber si pasaba).
    cur.execute("ROLLBACK TO SAVEPOINT paso16;")
    return None


def resumen_error(error):
    """pgcode y nombre de la restricción, NUNCA el texto (lleva la fila, H4)."""
    # Sin error: aceptado.
    if error is None:
        return "aceptado"
    # Con error: solo los dos códigos.
    return f"{error.pgcode} {error.diag.constraint_name}"


def debe_aceptarse(titulo, campos):
    """El intento no tiene que dar ningún error."""
    # Se intenta sobre la oportunidad propia.
    error = intento(OPORTUNIDAD, campos)
    # Sin error: correcto; con error, sus códigos.
    comprobar(titulo, error is None, f"({resumen_error(error)})")


def debe_rechazarse(titulo, campos, pgcode, restriccion, oportunidad_id=None):
    """El intento tiene que fallar con ESE código y ESA restricción."""
    # Se intenta (sobre la propia, salvo que se diga otra).
    error = intento(OPORTUNIDAD if oportunidad_id is None else oportunidad_id, campos)
    # Rechazado: se comprueba que es por lo esperado y no por otra cosa.
    if error is not None:
        # restriccion None: solo se mira el código (NOT NULL no siempre la nombra).
        comprobar(titulo, error.pgcode == pgcode
                  and (restriccion is None or error.diag.constraint_name == restriccion),
                  f"({resumen_error(error)})")
    # No se rechazó: FALLO explícito (regla de CLAUDE.md, caso D16).
    else:
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
            # Constancia en la salida (la primera línea).
            print(f"  ensayado: {sentencia.splitlines()[0]}")

    # ------------------------------------------------------------------
    print("\n1. El catálogo")
    # ¿Existe? Sin tabla, el resto del catálogo se cuenta como FALLO sin
    # consultarlo (::regclass daría un error).
    existe = tabla_existe(cur)
    comprobar(f"la tabla {TABLA} existe", existe)
    # Valores vacíos por defecto (sin tabla).
    columnas, restricciones, rls, politicas, filas = [], {}, None, None, None
    if existe:
        # SQL: nombre, tipo y si admite NULL, en el orden de la tabla.
        cur.execute("SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position;", (TABLA,))
        columnas = cur.fetchall()
        # SQL: cada restricción con su tipo y su definición, como la escribe Postgres.
        cur.execute("SELECT conname, contype, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = %s::regclass;", (f"public.{TABLA}",))
        restricciones = {nombre: (tipo, definicion) for nombre, tipo, definicion in cur.fetchall()}
        # SQL: RLS activada (relrowsecurity) y número de políticas.
        cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass;", (f"public.{TABLA}",))
        rls = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND tablename = %s;", (TABLA,))
        politicas = cur.fetchone()[0]
        # SQL: cuántas filas tiene (solo el número).
        cur.execute(f"SELECT count(*) FROM {TABLA};")
        filas = cur.fetchone()[0]
    # Las 7 columnas, con su tipo y NOT NULL.
    comprobar("las 7 columnas, en orden, con su tipo",
              [(c[0], c[1]) for c in columnas] == COLUMNAS, f"({[(c[0], c[1]) for c in columnas]})")
    comprobar("todas NOT NULL", bool(columnas) and all(c[2] == "NO" for c in columnas))

    def definicion(nombre):
        """La definición de una restricción, o "" si no existe."""
        # (tipo, definición), o vacío.
        return restricciones.get(nombre, ("", ""))[1]

    # La clave primaria, sobre id.
    comprobar(f"{PK}: PRIMARY KEY (id)", definicion(PK) == "PRIMARY KEY (id)", f"({definicion(PK) or 'NO EXISTE'})")
    # Una fila como mucho por oportunidad.
    comprobar(f"{UNICA}: UNIQUE (oportunidad_id)", definicion(UNICA) == "UNIQUE (oportunidad_id)",
              f"({definicion(UNICA) or 'NO EXISTE'})")
    # La foránea, sin ON DELETE (como las demás de la casa).
    comprobar(f"{FK}: FOREIGN KEY (oportunidad_id) REFERENCES oportunidades(id)",
              definicion(FK) == "FOREIGN KEY (oportunidad_id) REFERENCES oportunidades(id)",
              f"({definicion(FK) or 'NO EXISTE'})")
    # Los tres CHECK: sus piezas, como las escribe Postgres (reescribe BETWEEN).
    # La calle: su definición (es esquema, no datos), y las tres piezas.
    d = definicion(CHK_CALLE)
    print(f"    {CHK_CALLE}: {d or 'NO EXISTE'}")
    comprobar(f"{CHK_CALLE}: recortada y de 1 a {MAX_CALLE}",
              "btrim(calle_numero)" in d and ">= 1" in d and f"<= {MAX_CALLE}" in d)
    # El municipio, igual.
    d = definicion(CHK_MUNICIPIO)
    print(f"    {CHK_MUNICIPIO}: {d or 'NO EXISTE'}")
    comprobar(f"{CHK_MUNICIPIO}: recortado y de 1 a {MAX_MUNICIPIO}",
              "btrim(municipio)" in d and ">= 1" in d and f"<= {MAX_MUNICIPIO}" in d)
    # El código postal: el patrón entero, tal cual.
    d = definicion(CHK_CP)
    print(f"    {CHK_CP}: {d or 'NO EXISTE'}")
    comprobar(f"{CHK_CP}: el patrón de 5 cifras con provincia 01-52",
              "^(0[1-9]|[1-4][0-9]|5[0-2])[0-9]{3}$" in d)
    # RLS activada y sin políticas (Decision_RLS_N0.txt).
    comprobar("RLS activada", rls is True, f"({rls})")
    comprobar("0 políticas (cierra la Data API a anon/authenticated)", politicas == 0, f"({politicas})")
    # El número de filas: solo información (H4: nunca su contenido).
    print(f"  [INFO] filas en {TABLA}: {filas}")

    # ------------------------------------------------------------------
    print("\n2. Su comportamiento (todo se deshace al final)")
    # SQL: el cliente propio, su lead y su oportunidad (fuera de la tabla
    # nueva: siempre se pueden crear).
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES ('Prueba paso16', %s, '600000000') "
                "RETURNING id;", (EMAIL,))
    cliente = cur.fetchone()[0]
    # SQL: su lead, con un m2 válido (chk_leads_m2_rango).
    cur.execute("INSERT INTO leads (cliente_id, canal, datos_estructurados) VALUES (%s, 'check_paso16', %s) "
                "RETURNING id;", (cliente, Json({"m2": 10})))
    lead = cur.fetchone()[0]
    # SQL: su oportunidad, en 'nueva'.
    cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma, estado) VALUES (%s, 'bano', 'nueva') "
                "RETURNING id;", (lead,))
    OPORTUNIDAD = cur.fetchone()[0]

    # Aceptados: la de partida y los límites buenos.
    debe_aceptarse("dirección válida -> aceptada", {})
    debe_aceptarse(f"calle de {MAX_CALLE} 'á' ({MAX_CALLE * 2} bytes) -> aceptada", {"calle_numero": "á" * MAX_CALLE})
    debe_aceptarse(f"municipio de {MAX_MUNICIPIO} 'á' -> aceptado", {"municipio": "á" * MAX_MUNICIPIO})
    # Los extremos del patrón: provincias 01, 50 y 52.
    for cp in ("01001", "28013", "50000", "52999"):
        debe_aceptarse(f"código postal {cp} -> aceptado", {"codigo_postal": cp})

    # Rechazados por el CHECK de la calle.
    debe_rechazarse("calle vacía -> CHECK", {"calle_numero": ""}, "23514", CHK_CALLE)
    debe_rechazarse("calle con espacio delante -> CHECK", {"calle_numero": " Calle 1"}, "23514", CHK_CALLE)
    debe_rechazarse("calle con espacio detrás -> CHECK", {"calle_numero": "Calle 1 "}, "23514", CHK_CALLE)
    debe_rechazarse(f"calle de {MAX_CALLE + 1} -> CHECK", {"calle_numero": "x" * (MAX_CALLE + 1)}, "23514", CHK_CALLE)
    # Rechazados por el CHECK del municipio.
    debe_rechazarse("municipio vacío -> CHECK", {"municipio": ""}, "23514", CHK_MUNICIPIO)
    debe_rechazarse("municipio con espacio detrás -> CHECK", {"municipio": "Madrid "}, "23514", CHK_MUNICIPIO)
    debe_rechazarse(f"municipio de {MAX_MUNICIPIO + 1} -> CHECK", {"municipio": "x" * (MAX_MUNICIPIO + 1)},
                    "23514", CHK_MUNICIPIO)
    # Rechazados por el CHECK del código postal (provincia fuera, longitud,
    # caracteres que no son cifras ASCII).
    for cp, que in (("00123", "provincia 00"), ("53000", "provincia 53"), ("99999", "provincia 99"),
                    ("2801", "4 cifras"), ("280130", "6 cifras"), ("2801a", "con letra"),
                    ("28 13", "con espacio"), ("28013\n", "con salto de línea final"),
                    ("٢٨٠١٣", "cifras árabes orientales"), ("", "vacío")):
        debe_rechazarse(f"código postal {que} -> CHECK", {"codigo_postal": cp}, "23514", CHK_CP)

    # Las otras restricciones.
    debe_rechazarse("oportunidad inexistente -> clave foránea", {}, "23503", FK, oportunidad_id=-1)
    debe_rechazarse("calle NULL -> NOT NULL", {"calle_numero": None}, "23502", None)
    # SQL: una fila de verdad (dentro de la transacción) para la clave única.
    cur.execute(f"INSERT INTO {TABLA} (oportunidad_id, calle_numero, codigo_postal, municipio) "
                "VALUES (%s, %s, %s, %s);",
                (OPORTUNIDAD, VALIDA["calle_numero"], VALIDA["codigo_postal"], VALIDA["municipio"]))
    debe_rechazarse("segunda fila de la misma oportunidad -> clave única", {}, "23505", UNICA)
    # El upsert de D42.4: ON CONFLICT sobre la única sustituye los textos.
    error = intento(OPORTUNIDAD, {"calle_numero": "Otra Calle 2", "codigo_postal": "08001", "municipio": "Otro"},
                    "ON CONFLICT (oportunidad_id) DO UPDATE SET calle_numero = EXCLUDED.calle_numero, "
                    "codigo_postal = EXCLUDED.codigo_postal, municipio = EXCLUDED.municipio, updated_at = now()")
    comprobar("ON CONFLICT (oportunidad_id) DO UPDATE se acepta", error is None, f"({resumen_error(error)})")
    # SQL: el upsert de verdad (sin SAVEPOINT) y lo que queda, comparado en
    # Python (H4: no se imprime).
    cur.execute(f"INSERT INTO {TABLA} (oportunidad_id, calle_numero, codigo_postal, municipio) "
                "VALUES (%s, 'Otra Calle 2', '08001', 'Otro') ON CONFLICT (oportunidad_id) DO UPDATE "
                "SET calle_numero = EXCLUDED.calle_numero, codigo_postal = EXCLUDED.codigo_postal, "
                "municipio = EXCLUDED.municipio, updated_at = now();", (OPORTUNIDAD,))
    # SQL: cuántas filas tiene la oportunidad y si todas llevan los textos nuevos.
    cur.execute(f"SELECT count(*), bool_and(calle_numero = 'Otra Calle 2' AND codigo_postal = '08001' "
                f"AND municipio = 'Otro') FROM {TABLA} WHERE oportunidad_id = %s;", (OPORTUNIDAD,))
    cuantas, sustituida = cur.fetchone()
    comprobar("el upsert deja UNA fila, con los textos nuevos", cuantas == 1 and sustituida is True,
              f"(filas {cuantas}, sustituida {sustituida})")
# Siempre: ROLLBACK de todo (también del ensayo).
finally:
    cn.rollback()

# ----------------------------------------------------------------------
print("\n3. Nada queda")
# SQL: el cliente de prueba de esta ejecución no queda.
cur.execute("SELECT count(*) FROM clientes WHERE email = %s;", (EMAIL,))
comprobar("no queda ningún dato de prueba (ROLLBACK)", cur.fetchone()[0] == 0)
# En ensayo: la tabla vuelve a no existir, como al empezar.
if SENTENCIAS_ENSAYO is not None:
    comprobar("el ensayo no ha dejado nada en el esquema", tabla_existe(cur) == existia_antes)
# Se cierra la lectura y la conexión.
cn.rollback()
cn.close()

# Resumen y código de salida.
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{ok + len(fallos)} correctas")
print("=" * 78)
# Con fallos, se listan y código 1.
if fallos:
    print("FALLOS:")
    # Uno por línea, con su detalle.
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
