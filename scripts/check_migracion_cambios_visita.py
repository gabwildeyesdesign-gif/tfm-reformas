"""
Verificación de la migración paso18 (D46; plan docs/Plan_Cambio_Visita_D46.txt,
secciones 2 y 7): la tabla cambios_visita, con sus dos claves foráneas dobles
hacia visitas, su clave foránea hacia oportunidades, sus dos claves únicas,
sus cuatro CHECK, su índice por oportunidad y la RLS activada sin políticas.

Qué comprueba:
  S0. La conexión de prueba rechaza commit() (CommitProhibido), con la rama
      else que marca FALLO si no sale la excepción (regla del 2026-10-08).
  1.  El catálogo: la tabla existe; sus 8 columnas en orden, con su tipo y si
      admiten NULL; las 10 restricciones con nombre y sus piezas; el índice
      sobre oportunidad_id; RLS activada y 0 políticas. El número de filas
      sale como [INFO] (después de migrar, las reales son de administración).
  2.  Su comportamiento, dentro de UNA transacción que termina SIEMPRE en
      ROLLBACK, con datos propios (email check-paso18-<marca>@example.com,
      marca aleatoria de la ejecución), cada intento en su SAVEPOINT:
      aceptados un cambio, una cancelación con cada motivo del catálogo y un
      informe de 2000 "á"; rechazados por SU restricción (pgcode y nombre)
      una acción desconocida, no_contesta y sin_decision, los informes vacío,
      sin recortar y de 2001, cada combinación incoherente, una visita de
      OTRA oportunidad (las dos claves dobles), una oportunidad inexistente y
      un NULL; con filas de verdad (dentro de la transacción), la segunda
      fila de la misma visita anterior y de la misma visita nueva (23505), y
      dos cancelaciones con visita nueva NULL aceptadas. Si un rechazo
      esperado NO sale, FALLO explícito ("la excepción se ha tragado").
  3.  No queda el cliente de prueba (y, en ensayo, que la tabla sigue sin
      existir).

Nunca imprime un informe ni el texto de un error de Postgres (copia la fila):
solo pgcode, nombre de la restricción, recuentos y verdadero/falso.

Modo ensayo: --ensayo <ruta de un archivo de migración>. Lee la constante
SENTENCIAS de ese archivo (sin ejecutar su main) y la ejecuta DENTRO de la
misma transacción, antes de las comprobaciones; al final, ROLLBACK. Sirve
para probar paso18 sin aplicarla y las copias rotas (pruebas en negativo).
Solo antes de migrar: si la tabla ya existe, se para (código 2). Se niega a
ejecutar una orden con COMMIT, END o ROLLBACK escritos en el SQL, que se
saltarían ConexionSinCommit.

Recuentos: se escriben en el plan ANTES de la primera prueba de la fase 2
(precisión 5 del tutor) y en el commit (D37.5: nunca en la misma orden que
los calcula). Antes de migrar y sin ensayo, FALLA (N16: es su primera prueba
en negativo).

Las 4 preguntas (CLAUDE.md):
  (1) Escribe en clientes, leads, oportunidades, visitas y cambios_visita (y,
      en ensayo, CREATE TABLE e INDEX) dentro de una transacción que termina
      en ROLLBACK.
  (2) No pasa la conexión a ningún código de app/. El SQL del ensayo corre en
      la misma conexión ConexionSinCommit, y las órdenes de fin de
      transacción escritas en SQL se rechazan antes de ejecutarlas.
  (3) Si se corta, Postgres deshace la transacción abierta: no se confirma
      nada. La siguiente ejecución lo comprueba (punto 3).
  (4) En ensayo, las claves foráneas bloquean oportunidades y visitas frente
      a escrituras mientras dura la transacción (alrededor de un segundo): se
      ejecuta en la fase 2, con Docker/n8n cerrados y con lock_timeout de
      5 s. Sus visitas son 'cancelada' o una sola 'confirmada' por
      oportunidad propia, en 2099 (no ocupan ningún hueco real). No toca
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

# Lo esperado, escrito aquí (copiado del plan, sección 2, no del código).
TABLA = "cambios_visita"
# El máximo del informe (el de decisiones_gate).
MAX_INFORME = 2000
# Las 8 columnas: (nombre, tipo de information_schema, admite NULL).
COLUMNAS = [("id", "integer", "NO"), ("oportunidad_id", "integer", "NO"),
            ("accion", "character varying", "NO"), ("visita_anterior_id", "integer", "NO"),
            ("visita_nueva_id", "integer", "YES"), ("motivo", "character varying", "YES"),
            ("informe", "text", "NO"), ("created_at", "timestamp with time zone", "NO")]
# Los nombres de las restricciones: clave primaria y foránea simple.
PK, FK_OP = f"{TABLA}_pkey", f"{TABLA}_oportunidad_id_fkey"
# Las dos foráneas dobles hacia visitas(id, oportunidad_id).
FK_ANT, FK_NUEVA = f"{TABLA}_visita_anterior_oportunidad_fkey", f"{TABLA}_visita_nueva_oportunidad_fkey"
# Las dos únicas.
UQ_ANT, UQ_NUEVA = f"{TABLA}_visita_anterior_id_key", f"{TABLA}_visita_nueva_id_key"
# Los cuatro CHECK.
CHK_ACCION, CHK_MOTIVO = f"chk_{TABLA}_accion", f"chk_{TABLA}_motivo"
CHK_INFORME, CHK_COHERENCIA = f"chk_{TABLA}_informe", f"chk_{TABLA}_coherencia"
# El índice por oportunidad (P10).
INDICE = f"{TABLA}_oportunidad_id_idx"
# La marca propia de esta ejecución: 8 caracteres aleatorios.
MARCA = uuid.uuid4().hex[:8]
# El email propio (dominio reservado @example.com, con la marca).
EMAIL = f"check-paso18-{MARCA}@example.com"

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


class SinTabla(Exception):
    """Antes de migrar: corta el punto 2 cuando la tabla no existe."""


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

# Una fila válida de partida: un cambio (se completa con los ids propios).
VALIDA = {"accion": "cambiar", "motivo": None, "informe": "Informe de prueba paso18."}


def intento(campos):
    """
    Inserta una fila con esos campos (los de VALIDA y los ids de partida,
    cambiados por "campos") en un SAVEPOINT, y la deshace. Devuelve None si
    se aceptó, o el error de Postgres si se rechazó.
    """
    # Los valores: los de partida, con los cambiados encima.
    fila = {"oportunidad_id": OP_A, "visita_anterior_id": VA_1, "visita_nueva_id": VA_2, **VALIDA, **campos}
    # SQL: un punto al que volver.
    cur.execute("SAVEPOINT paso18;")
    # try: el INSERT puede fallar (por una restricción, o porque no hay tabla).
    try:
        # SQL: la fila, con los valores aparte.
        cur.execute(
            f"INSERT INTO {TABLA} (oportunidad_id, accion, visita_anterior_id, visita_nueva_id, motivo, informe) "
            "VALUES (%(oportunidad_id)s, %(accion)s, %(visita_anterior_id)s, %(visita_nueva_id)s, %(motivo)s, "
            "%(informe)s);",
            fila,
        )
    # Rechazado: se vuelve al punto de guardado y se devuelve el error.
    except psycopg2.Error as error:
        # SQL: la transacción sigue viva.
        cur.execute("ROLLBACK TO SAVEPOINT paso18;")
        return error
    # SQL: aceptado; también se deshace (solo se quería saber si pasaba).
    cur.execute("ROLLBACK TO SAVEPOINT paso18;")
    return None


def resumen_error(error):
    """pgcode y nombre de la restricción, NUNCA el texto (copia la fila)."""
    # Sin error: aceptado.
    if error is None:
        return "aceptado"
    # Con error: solo los dos códigos.
    return f"{error.pgcode} {error.diag.constraint_name}"


def debe_aceptarse(titulo, campos):
    """El intento no tiene que dar ningún error."""
    # Se intenta.
    error = intento(campos)
    # Sin error: correcto; con error, sus códigos.
    comprobar(titulo, error is None, f"({resumen_error(error)})")


def debe_rechazarse(titulo, campos, pgcode, restricciones):
    """
    El intento tiene que fallar con ESE código y una de ESAS restricciones
    (restricciones None: solo se mira el código; NOT NULL no siempre la nombra).
    """
    # Se intenta.
    error = intento(campos)
    # Rechazado: se comprueba que es por lo esperado y no por otra cosa.
    if error is not None:
        # El código y, si se dice, el nombre de la restricción.
        comprobar(titulo, error.pgcode == pgcode
                  and (restricciones is None or error.diag.constraint_name in restricciones),
                  f"({resumen_error(error)})")
    # No se rechazó: FALLO explícito (regla de CLAUDE.md, caso D16).
    else:
        comprobar(titulo, False, "(la excepción se ha tragado: se acepta)")


def visita(op, estado, dia):
    """Una visita propia, en 2099 (lejos de cualquier hueco real). Devuelve su id."""
    # SQL: la visita, con un texto válido (chk de texto_cliente, 1..300).
    cur.execute("INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
                "VALUES (%s, %s::timestamptz, %s, 'visita de prueba paso18') RETURNING id;",
                (op, f"2099-01-{dia:02d} 10:00+01", estado))
    return cur.fetchone()[0]


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
    columnas, restricciones, indice, rls, politicas, filas = [], {}, None, None, None, None
    if existe:
        # SQL: nombre, tipo y si admite NULL, en el orden de la tabla.
        cur.execute("SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position;", (TABLA,))
        columnas = [tuple(c) for c in cur.fetchall()]
        # SQL: cada restricción con su definición, como la escribe Postgres.
        cur.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = %s::regclass;", (f"public.{TABLA}",))
        restricciones = dict(cur.fetchall())
        # SQL: la definición del índice (o ninguna fila si no existe).
        cur.execute("SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = %s;", (INDICE,))
        fila_indice = cur.fetchone()
        indice = fila_indice[0] if fila_indice else None
        # SQL: RLS activada (relrowsecurity) y número de políticas.
        cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass;", (f"public.{TABLA}",))
        rls = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND tablename = %s;", (TABLA,))
        politicas = cur.fetchone()[0]
        # SQL: cuántas filas tiene (solo el número).
        cur.execute(f"SELECT count(*) FROM {TABLA};")
        filas = cur.fetchone()[0]
    # Las 8 columnas, en orden, con su tipo y si admiten NULL.
    comprobar("las 8 columnas, en orden, con su tipo y NULL solo en visita_nueva_id y motivo",
              columnas == COLUMNAS, f"({columnas})")

    def definicion(nombre):
        """La definición de una restricción, o "" si no existe."""
        # El texto, o vacío.
        return restricciones.get(nombre, "")

    # La clave primaria, sobre id.
    comprobar(f"{PK}: PRIMARY KEY (id)", definicion(PK) == "PRIMARY KEY (id)", f"({definicion(PK) or 'NO EXISTE'})")
    # La foránea simple, sin ON DELETE (como las demás de la casa).
    comprobar(f"{FK_OP}: FOREIGN KEY (oportunidad_id) REFERENCES oportunidades(id)",
              definicion(FK_OP) == "FOREIGN KEY (oportunidad_id) REFERENCES oportunidades(id)",
              f"({definicion(FK_OP) or 'NO EXISTE'})")
    # Las dos dobles: (visita, oportunidad) -> visitas(id, oportunidad_id).
    for nombre, columna in ((FK_ANT, "visita_anterior_id"), (FK_NUEVA, "visita_nueva_id")):
        # El texto exacto que escribe Postgres.
        esperado = f"FOREIGN KEY ({columna}, oportunidad_id) REFERENCES visitas(id, oportunidad_id)"
        comprobar(f"{nombre}: {esperado}", definicion(nombre) == esperado, f"({definicion(nombre) or 'NO EXISTE'})")
    # Las dos únicas.
    for nombre, columna in ((UQ_ANT, "visita_anterior_id"), (UQ_NUEVA, "visita_nueva_id")):
        # El texto exacto que escribe Postgres.
        comprobar(f"{nombre}: UNIQUE ({columna})", definicion(nombre) == f"UNIQUE ({columna})",
                  f"({definicion(nombre) or 'NO EXISTE'})")
    # Los CHECK: sus piezas, como las escribe Postgres (reescribe IN y BETWEEN).
    # La acción: los dos textos.
    d = definicion(CHK_ACCION)
    print(f"    {CHK_ACCION}: {d or 'NO EXISTE'}")
    comprobar(f"{CHK_ACCION}: solo 'cambiar' y 'cancelar'", "'cambiar'" in d and "'cancelar'" in d)
    # El motivo: los cuatro del catálogo, y ni no_contesta ni sin_decision.
    d = definicion(CHK_MOTIVO)
    print(f"    {CHK_MOTIVO}: {d or 'NO EXISTE'}")
    comprobar(f"{CHK_MOTIVO}: NULL o precio, plazo, proyecto_no_viable, otro (sin no_contesta ni sin_decision)",
              all(f"'{m}'" in d for m in ("precio", "plazo", "proyecto_no_viable", "otro"))
              and "no_contesta" not in d and "sin_decision" not in d and "IS NULL" in d)
    # El informe: recortado y de 1 a 2000.
    d = definicion(CHK_INFORME)
    print(f"    {CHK_INFORME}: {d or 'NO EXISTE'}")
    comprobar(f"{CHK_INFORME}: recortado y de 1 a {MAX_INFORME}",
              "btrim(informe)" in d and ">= 1" in d and f"<= {MAX_INFORME}" in d)
    # La coherencia: lo justo para ver que nombra las tres columnas y la desigualdad.
    d = definicion(CHK_COHERENCIA)
    print(f"    {CHK_COHERENCIA}: {d or 'NO EXISTE'}")
    comprobar(f"{CHK_COHERENCIA}: nombra visita_nueva_id, motivo y visita_nueva_id <> visita_anterior_id",
              "visita_nueva_id IS NOT NULL" in d and "motivo IS NULL" in d and "motivo IS NOT NULL" in d
              and "visita_nueva_id <> visita_anterior_id" in d)
    # El índice por oportunidad (P10).
    print(f"    {INDICE}: {indice or 'NO EXISTE'}")
    comprobar(f"{INDICE}: índice sobre (oportunidad_id)", indice is not None and "(oportunidad_id)" in indice
              and "UNIQUE" not in indice)
    # RLS activada y sin políticas (Decision_RLS_N0.txt).
    comprobar("RLS activada", rls is True, f"({rls})")
    comprobar("0 políticas (cierra la Data API a anon/authenticated)", politicas == 0, f"({politicas})")
    # El número de filas: solo información.
    print(f"  [INFO] filas en {TABLA}: {filas}")

    # ------------------------------------------------------------------
    print("\n2. Su comportamiento (todo se deshace al final)")
    # SQL: el cliente propio, su lead y dos oportunidades (fuera de la tabla
    # nueva: siempre se pueden crear).
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES ('Prueba paso18', %s, '600000000') "
                "RETURNING id;", (EMAIL,))
    cliente = cur.fetchone()[0]
    # SQL: su lead, con un m2 válido (chk_leads_m2_rango).
    cur.execute("INSERT INTO leads (cliente_id, canal, datos_estructurados) VALUES (%s, 'check_paso18', %s) "
                "RETURNING id;", (cliente, Json({"m2": 10})))
    lead = cur.fetchone()[0]
    # SQL: dos oportunidades, en 'visita_agendada'.
    cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma, estado) VALUES (%s, 'bano', 'visita_agendada'), "
                "(%s, 'bano', 'visita_agendada') RETURNING id;", (lead, lead))
    OP_A, OP_B = (f[0] for f in cur.fetchall())
    # Las visitas: en A, una confirmada y cuatro canceladas (el índice parcial
    # admite una sola activa); en B, una cancelada (para las claves dobles).
    VA_1, VA_2 = visita(OP_A, "cancelada", 5), visita(OP_A, "confirmada", 6)
    VA_3, VA_4, VA_5 = visita(OP_A, "cancelada", 7), visita(OP_A, "cancelada", 8), visita(OP_A, "cancelada", 9)
    VB_1 = visita(OP_B, "cancelada", 12)

    # Aceptados: un cambio, una cancelación con cada motivo y el informe más largo.
    debe_aceptarse("cambiar con visita nueva y sin motivo -> aceptado", {})
    for motivo_ok in ("precio", "plazo", "proyecto_no_viable", "otro"):
        # Cancelar: sin visita nueva y con motivo.
        debe_aceptarse(f"cancelar con motivo {motivo_ok} -> aceptado",
                       {"accion": "cancelar", "visita_nueva_id": None, "motivo": motivo_ok})
    debe_aceptarse(f"informe de {MAX_INFORME} 'á' ({MAX_INFORME * 2} bytes) -> aceptado",
                   {"informe": "á" * MAX_INFORME})

    # Rechazados por el CHECK de la acción y por el del motivo.
    debe_rechazarse("acción 'mover' -> CHECK", {"accion": "mover"}, "23514", {CHK_ACCION})
    for motivo_no in ("no_contesta", "sin_decision"):
        # Cancelar con un motivo fuera del catálogo de D46.3.
        debe_rechazarse(f"cancelar con motivo {motivo_no} -> CHECK",
                        {"accion": "cancelar", "visita_nueva_id": None, "motivo": motivo_no}, "23514", {CHK_MOTIVO})
    # Rechazados por el CHECK del informe.
    debe_rechazarse("informe vacío -> CHECK", {"informe": ""}, "23514", {CHK_INFORME})
    debe_rechazarse("informe con espacio delante -> CHECK", {"informe": " Informe"}, "23514", {CHK_INFORME})
    debe_rechazarse(f"informe de {MAX_INFORME + 1} -> CHECK", {"informe": "x" * (MAX_INFORME + 1)}, "23514",
                    {CHK_INFORME})
    # Rechazados por la coherencia, una combinación cada vez.
    for titulo, campos in (("cambiar sin visita nueva", {"visita_nueva_id": None}),
                           ("cambiar con motivo", {"motivo": "precio"}),
                           ("cambiar a la misma visita", {"visita_nueva_id": VA_1}),
                           ("cancelar sin motivo", {"accion": "cancelar", "visita_nueva_id": None}),
                           ("cancelar con visita nueva", {"accion": "cancelar", "motivo": "precio"})):
        # Siempre el mismo CHECK.
        debe_rechazarse(f"{titulo} -> CHECK de coherencia", campos, "23514", {CHK_COHERENCIA})
    # Las claves dobles: una visita de OTRA oportunidad (la de B con la oportunidad A).
    debe_rechazarse("visita anterior de otra oportunidad -> clave doble", {"visita_anterior_id": VB_1}, "23503",
                    {FK_ANT})
    debe_rechazarse("visita nueva de otra oportunidad -> clave doble", {"visita_nueva_id": VB_1}, "23503", {FK_NUEVA})
    # Una oportunidad inexistente: la rechaza la simple o la doble (las dos fallan).
    debe_rechazarse("oportunidad inexistente -> clave foránea", {"oportunidad_id": -1}, "23503", {FK_OP, FK_ANT})
    # Un NULL en una columna obligatoria.
    debe_rechazarse("visita anterior NULL -> NOT NULL", {"visita_anterior_id": None}, "23502", None)
    # Sin tabla (antes de migrar), lo que sigue no va en SAVEPOINT y daría
    # una traza: sus comprobaciones cuentan como FALLO, sin consultar.
    if not existe:
        # Una por cada comprobación que se salta.
        for titulo in ("segunda fila de la misma visita anterior -> clave única",
                       "segunda fila con la misma visita nueva -> clave única",
                       "dos cancelaciones (visita nueva NULL dos veces) -> aceptadas"):
            comprobar(titulo, False, "(la tabla no existe)")
        # Se salta al except de abajo y al finally (ROLLBACK), sin tocar la tabla.
        raise SinTabla
    # SQL: una fila de verdad (dentro de la transacción): el cambio VA_1 -> VA_2.
    cur.execute(f"INSERT INTO {TABLA} (oportunidad_id, accion, visita_anterior_id, visita_nueva_id, informe) "
                "VALUES (%s, 'cambiar', %s, %s, 'Primer cambio.');", (OP_A, VA_1, VA_2))
    # La misma visita anterior otra vez (ahora cancelada): clave única.
    debe_rechazarse("segunda fila de la misma visita anterior -> clave única",
                    {"accion": "cancelar", "visita_nueva_id": None, "motivo": "precio"}, "23505", {UQ_ANT})
    # Otra anterior con la MISMA nueva: clave única de la nueva.
    debe_rechazarse("segunda fila con la misma visita nueva -> clave única", {"visita_anterior_id": VA_3},
                    "23505", {UQ_NUEVA})
    # SQL: una cancelación de verdad (visita nueva NULL) ...
    cur.execute(f"INSERT INTO {TABLA} (oportunidad_id, accion, visita_anterior_id, motivo, informe) "
                "VALUES (%s, 'cancelar', %s, 'plazo', 'Primera cancelación.');", (OP_A, VA_4))
    # ... y otra con NULL otra vez: la única admite varios NULL.
    debe_aceptarse("dos cancelaciones (visita nueva NULL dos veces) -> aceptadas",
                   {"accion": "cancelar", "visita_anterior_id": VA_5, "visita_nueva_id": None, "motivo": "otro"})
# Sin tabla: ya se han contado los FALLOS; se sigue con el punto 3.
except SinTabla:
    pass
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
