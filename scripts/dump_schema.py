"""
Genera docs/schema_actual.sql a partir del esquema REAL de Supabase.

Por que existe este script: schema_n0_v2.sql se escribio a mano y se
quedo desactualizado sin que nadie lo notara (le faltaban una columna,
dos tablas enteras y un CHECK). Un archivo escrito a mano siempre acaba
divergiendo de la base de datos. Este script no escribe SQL: LEE la
estructura que Postgres guarda sobre si mismo y la reconstruye, asi que
el resultado no puede mentir.

Fuentes consultadas, todas de information_schema:
  - columns                   -> columnas, tipos, nullable, defaults
  - table_constraints         -> que restricciones existen y de que tipo
  - key_column_usage          -> que columnas forman cada PK/UNIQUE/FK
  - check_constraints         -> la expresion de cada CHECK

Y ademas, fuera de information_schema:
  - pg_constraint + pg_get_constraintdef -> la definición COMPLETA de cada
    clave foránea, y de qué tabla depende cada tabla (ver pasos 3 y 3c).
  - pg_indexes + pg_constraint -> los indices que NO respaldan ninguna
    restriccion (ver paso 3b en main()).

CORRECCIÓN 2026-09-28 (dos fallos que aparecieron con la primera clave
foránea de dos columnas, decisiones_gate -> visitas (id, oportunidad_id)):
  1. El destino de cada clave foránea se leía de
     information_schema.constraint_column_usage, que da UNA FILA POR
     COLUMNA; se guardaba en un diccionario y la segunda fila pisaba a la
     primera, así que salía "REFERENCES visitas(oportunidad_id)". Ahora se
     usa pg_get_constraintdef, que la da completa y en orden.
  2. Las tablas se escribían en orden alfabético, y decisiones_gate salía
     antes que oportunidades y visitas, a las que apunta: el archivo no se
     podía ejecutar ("relation oportunidades does not exist"). Ahora se
     escriben en ORDEN TOPOLÓGICO: cada tabla después de las que
     referencia. Si hubiera un ciclo de claves foráneas, el script se
     detiene con un error y NO escribe el archivo.
Se comprueba con scripts/check_schema_actual_ejecutable.py.

Uso:
    .\\venv\\Scripts\\python.exe scripts\\dump_schema.py

No modifica NADA en la base de datos: solo hace SELECT.
"""

# os: para leer variables de entorno (os.getenv).
import os
# sys: para la codificación de la consola y para sys.exit.
import sys
# datetime y timezone: para la fecha y hora (en UTC) de la cabecera.
from datetime import datetime, timezone
# Path: para construir rutas y escribir el archivo.
from pathlib import Path

# psycopg2: la biblioteca con la que Python habla con PostgreSQL.
import psycopg2
# load_dotenv: lee el .env y mete sus valores como variables de entorno.
from dotenv import load_dotenv

# La consola de Windows no usa UTF-8 por defecto.
sys.stdout.reconfigure(encoding="utf-8")

# Path(__file__) es la ruta de ESTE archivo. .resolve() la vuelve
# absoluta, y .parents[1] sube un nivel (de scripts/ a la raiz del
# repositorio). Se calcula asi, y no con una ruta escrita a mano, para
# que el script funcione igual en cualquier maquina y desde cualquier
# directorio de trabajo. Es la misma tecnica que ya se uso en
# scripts/check_graceful_shutdown.py.
RAIZ = Path(__file__).resolve().parents[1]
# El archivo que se genera. El operador "/" de Path une trozos de ruta.
SALIDA = RAIZ / "docs" / "schema_actual.sql"

# Carga el .env de la raíz y lee la cadena de conexión.
load_dotenv(RAIZ / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")

# Traduccion de los nombres largos de information_schema a la forma
# corta que se escribe normalmente en un CREATE TABLE.
TIPOS_CORTOS = {
    "character varying": "VARCHAR",
    "timestamp with time zone": "TIMESTAMPTZ",
    "timestamp without time zone": "TIMESTAMP",
    "integer": "INTEGER",
    "bigint": "BIGINT",
    "smallint": "SMALLINT",
    "boolean": "BOOLEAN",
    "numeric": "NUMERIC",
    "text": "TEXT",
    "jsonb": "JSONB",
    "json": "JSON",
    "date": "DATE",
    "uuid": "UUID",
}


def tipo_sql(tipo, longitud, precision, escala, defecto):
    """
    Devuelve el tipo tal como se escribiria en un CREATE TABLE.

    Caso especial SERIAL: en Postgres, SERIAL no es un tipo real. Es un
    atajo que crea una columna INTEGER mas una secuencia, y pone como
    valor por defecto nextval('...'). Por eso information_schema informa
    de "integer" con ese default: hay que reconocer el patron para
    volver a escribirlo como SERIAL.
    """
    # Un default con nextval(...) delata una columna SERIAL (o BIGSERIAL si
    # la columna es bigint).
    if defecto and "nextval(" in defecto:
        return "BIGSERIAL" if tipo == "bigint" else "SERIAL"

    # El nombre corto si está en la tabla; si no, el nombre largo en mayúsculas.
    base = TIPOS_CORTOS.get(tipo, tipo.upper())
    # Los textos con longitud máxima: VARCHAR(150).
    if longitud is not None:
        return f"{base}({longitud})"
    # Los numéricos con precisión y escala: NUMERIC(10,2).
    if tipo == "numeric" and precision is not None:
        return f"{base}({precision},{escala})"
    # El resto, tal cual: INTEGER, TEXT, JSONB...
    return base


def ordenar_tablas(tablas, dependencias):
    """
    Devuelve las tablas en ORDEN TOPOLÓGICO: cada una después de todas
    las tablas a las que apunta con una clave foránea. Así el archivo se
    puede ejecutar de arriba abajo: cuando un CREATE TABLE dice
    REFERENCES otra_tabla, otra_tabla ya existe.

    tablas:       lista de nombres de tabla.
    dependencias: diccionario tabla -> conjunto de tablas a las que apunta
                  (sin contarse a sí misma: una tabla que se apunta a sí
                  misma se puede crear en un solo CREATE TABLE).

    Método (algoritmo de Kahn): se van sacando las tablas que ya no tienen
    dependencias pendientes. Entre varias disponibles a la vez se elige la
    primera por orden alfabético, para que el resultado sea siempre el
    mismo (y el diff entre dos volcados solo muestre cambios reales).

    Si en algún momento no queda ninguna disponible pero sí quedan tablas,
    es que hay un CICLO (A apunta a B y B apunta a A): se lanza un error,
    porque ningún orden permitiría crearlas con sus claves foráneas dentro
    del CREATE TABLE, y un archivo que no se puede ejecutar no debe
    escribirse.
    """
    # Copia de las dependencias, para ir tachando sin tocar el original.
    # set(...) crea un conjunto: una colección sin repetidos.
    pendientes = {t: set(dependencias.get(t, set())) for t in tablas}
    # Aquí se van apuntando las tablas en el orden en que se escribirán.
    orden = []
    # Mientras quede alguna tabla sin colocar.
    while pendientes:
        # Tablas cuyas dependencias ya están todas escritas.
        disponibles = sorted(t for t, deps in pendientes.items() if not deps)
        if not disponibles:
            # Ninguna disponible y aún quedan: ciclo. Se nombran las tablas
            # atrapadas para que el error diga dónde mirar.
            raise RuntimeError(
                "Ciclo de claves foráneas entre las tablas "
                f"{sorted(pendientes)}: no hay ningún orden que permita crearlas. "
                "No se escribe schema_actual.sql."
            )
        # La primera disponible (alfabéticamente) se coloca y se quita de
        # las pendientes.
        siguiente = disponibles[0]
        orden.append(siguiente)
        del pendientes[siguiente]
        # La tabla escrita deja de ser una dependencia pendiente de las demás.
        # discard() quita un elemento de un conjunto si está (y no falla si no).
        for deps in pendientes.values():
            deps.discard(siguiente)
    # Todas colocadas: este es el orden de escritura.
    return orden


def main():
    # Sin cadena de conexión no se puede hacer nada: se para con un mensaje.
    if not DATABASE_URL:
        print("ERROR: no se encontro DATABASE_URL en el .env")
        sys.exit(1)

    # Una sola conexión y su cursor (el objeto que envía el SQL).
    conexion = psycopg2.connect(DATABASE_URL)
    cursor = conexion.cursor()

    # ---- 1. Que tablas hay -------------------------------------------
    # No se escribe la lista a mano a proposito: si manana aparece una
    # tabla nueva, este script la recoge sin que nadie lo edite.
    # SQL: los nombres de las tablas normales (BASE TABLE, no vistas) del
    # esquema public, en orden alfabético.
    cursor.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name;
        """
    )
    # Lista con el primer (y único) valor de cada fila: el nombre.
    tablas = [fila[0] for fila in cursor.fetchall()]
    print(f"Tablas encontradas: {len(tablas)} -> {', '.join(tablas)}")

    # ---- 2. Columnas de cada tabla -----------------------------------
    # SQL: para cada columna de public, su tabla, nombre, tipo, longitud,
    # precisión, escala, si admite NULL, su default y su posición.
    cursor.execute(
        """
        SELECT table_name, column_name, data_type,
               character_maximum_length, numeric_precision, numeric_scale,
               is_nullable, column_default, ordinal_position
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position;
        """
    )
    # Diccionario: nombre de tabla -> lista de sus columnas.
    # setdefault(clave, []) devuelve la lista existente, o crea una vacia
    # la primera vez que se ve esa tabla.
    columnas = {}
    # fila[0] es el nombre de la tabla: cada fila va a la lista de su tabla.
    for fila in cursor.fetchall():
        columnas.setdefault(fila[0], []).append(fila)

    # ---- 3. Restricciones: PK, UNIQUE, FK, CHECK ---------------------
    # table_constraints dice QUE restricciones hay y de que tipo.
    # key_column_usage dice QUE COLUMNAS participan en cada una.
    # string_agg junta varias columnas en un solo texto separado por
    # comas, para las claves compuestas (PRIMARY KEY (a, b)).
    cursor.execute(
        """
        SELECT tc.table_name, tc.constraint_type, tc.constraint_name,
               string_agg(kcu.column_name, ', ' ORDER BY kcu.ordinal_position)
        FROM information_schema.table_constraints tc
        LEFT JOIN information_schema.key_column_usage kcu
               ON kcu.constraint_name = tc.constraint_name
              AND kcu.table_schema    = tc.table_schema
        WHERE tc.table_schema = 'public'
          AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')
        GROUP BY tc.table_name, tc.constraint_type, tc.constraint_name
        ORDER BY tc.table_name, tc.constraint_type;
        """
    )
    # Diccionario tabla -> lista de (tipo, nombre, columnas).
    restricciones = {}
    for tabla, tipo, nombre, cols in cursor.fetchall():
        restricciones.setdefault(tabla, []).append((tipo, nombre, cols))

    # Definición completa de cada clave foránea, y de qué tabla depende
    # cada tabla (para el orden topológico del paso 3c).
    #
    # pg_constraint es el catálogo interno de restricciones; contype = 'f'
    # son las claves foráneas. pg_get_constraintdef(oid) devuelve la
    # definición completa, con TODAS sus columnas en orden, por ejemplo
    # "FOREIGN KEY (visita_id, oportunidad_id) REFERENCES visitas(id,
    # oportunidad_id)". conrelid es la tabla que tiene la clave y confrelid
    # la tabla a la que apunta; se unen con pg_class para sacar sus nombres.
    #
    # (Antes se usaba information_schema.constraint_column_usage, que da
    # una fila por columna de destino; con una clave de dos columnas solo
    # sobrevivía una. Ver la cabecera.)
    cursor.execute(
        """
        SELECT k.conname, pg_get_constraintdef(k.oid), origen.relname, destino.relname
        FROM pg_constraint k
        JOIN pg_namespace n    ON n.oid = k.connamespace
        JOIN pg_class origen   ON origen.oid  = k.conrelid
        JOIN pg_class destino  ON destino.oid = k.confrelid
        WHERE n.nspname = 'public' AND k.contype = 'f';
        """
    )
    # nombre de la clave -> su definición; tabla -> tablas a las que apunta.
    definiciones_fk = {}
    dependencias = {}
    for nombre, definicion, tabla_origen, tabla_destino in cursor.fetchall():
        definiciones_fk[nombre] = definicion
        # Una tabla que se apunta a sí misma no cuenta como dependencia:
        # su clave se puede escribir en su propio CREATE TABLE.
        if tabla_destino != tabla_origen:
            dependencias.setdefault(tabla_origen, set()).add(tabla_destino)

    # ---- 3c. Orden de las tablas ---------------------------------------
    # Cada tabla después de las que referencia (ver ordenar_tablas). Se
    # calcula ANTES de escribir nada: si hubiera un ciclo, el error sale
    # aquí y el archivo anterior se queda como estaba.
    orden_tablas = ordenar_tablas(tablas, dependencias)
    print(f"Orden de escritura: {', '.join(orden_tablas)}")

    # CHECKs. information_schema.check_constraints incluye tambien los
    # CHECK automaticos que Postgres crea para cada columna NOT NULL
    # (con la forma "columna IS NOT NULL"); se filtran, porque el NOT
    # NULL ya se escribe en la propia linea de la columna y repetirlo
    # como CHECK seria ruido.
    # SQL: tabla, nombre y expresión de cada CHECK de public, sin los
    # automáticos de NOT NULL (el NOT LIKE '%IS NOT NULL').
    cursor.execute(
        """
        SELECT tc.table_name, tc.constraint_name, cc.check_clause
        FROM information_schema.table_constraints tc
        JOIN information_schema.check_constraints cc
          ON cc.constraint_name = tc.constraint_name
         AND cc.constraint_schema = tc.table_schema
        WHERE tc.table_schema = 'public'
          AND tc.constraint_type = 'CHECK'
          AND cc.check_clause NOT LIKE '%IS NOT NULL'
        ORDER BY tc.table_name, tc.constraint_name;
        """
    )
    # Diccionario tabla -> lista de (nombre, expresión).
    checks = {}
    for tabla, nombre, clausula in cursor.fetchall():
        checks.setdefault(tabla, []).append((nombre, clausula))

    # ---- 3b. Indices que no son restricciones ------------------------
    # information_schema solo conoce RESTRICCIONES (PK, UNIQUE, FK,
    # CHECK). Un indice unico sobre una EXPRESION, como el de
    # lower(clientes.email) que crea la migracion paso8, no es una
    # restriccion para el estandar SQL: es un indice, y no aparece en
    # ninguna de las consultas de arriba. Sin esta seccion, el volcado
    # diria que clientes.email ya no es unico, que es falso.
    #
    # pg_indexes es la vista de Postgres que lista TODOS los indices, con
    # su definicion completa (indexdef) lista para ejecutar. Se excluyen
    # los que respaldan una restriccion (cada PK y cada UNIQUE crean su
    # propio indice con el mismo nombre), porque esos ya salen dentro del
    # CREATE TABLE y aparecerian dos veces.
    # SQL: la definición de cada índice de public para el que NO existe una
    # restricción con su mismo nombre.
    cursor.execute(
        """
        SELECT i.indexdef
        FROM pg_indexes i
        WHERE i.schemaname = 'public'
          AND NOT EXISTS (
              SELECT 1 FROM pg_constraint k WHERE k.conname = i.indexname
          )
        ORDER BY i.tablename, i.indexname;
        """
    )
    # Lista con la sentencia CREATE INDEX de cada uno.
    indices_sueltos = [fila[0] for fila in cursor.fetchall()]

    # ---- 4. Filas por tabla, solo como dato informativo --------------
    conteos = {}
    for tabla in tablas:
        # SQL: filas de esa tabla. El nombre va entre comillas dobles, que
        # en SQL marcan un nombre de tabla. Viene del catálogo, no de fuera.
        cursor.execute(f'SELECT COUNT(*) FROM "{tabla}";')
        conteos[tabla] = cursor.fetchone()[0]

    # ---- 5. Construccion del archivo ---------------------------------
    # Se va acumulando en una lista de lineas y al final se unen todas.
    # Es mas eficiente que ir concatenando textos uno a uno.
    lineas = []
    # Fecha y hora actuales en UTC, como texto ("2026-10-03 10:00:00 UTC").
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    # La cabecera del archivo: líneas de comentario SQL (empiezan por --).
    # "=" * 74 repite el carácter 74 veces para dibujar una raya.
    lineas.append("-- " + "=" * 74)
    lineas.append("-- ESQUEMA REAL DE LA BASE DE DATOS - ARCHIVO GENERADO AUTOMATICAMENTE")
    lineas.append("--")
    lineas.append(f"-- Generado por scripts/dump_schema.py el {ahora}")
    lineas.append("-- NO EDITAR A MANO: se sobrescribe al volver a ejecutar el script.")
    lineas.append("--")
    lineas.append("-- Reconstruido leyendo information_schema (columns,")
    lineas.append("-- table_constraints, key_column_usage y check_constraints), pg_constraint")
    lineas.append("-- (claves foráneas) y pg_indexes, no copiado de ningun archivo previo.")
    lineas.append("--")
    lineas.append(f"-- Tablas: {len(tablas)}, en orden topológico: cada una después de las")
    lineas.append("-- tablas a las que apunta, para que el archivo se pueda ejecutar entero.")
    lineas.append("-- " + "=" * 74)
    lineas.append("")

    # Un CREATE TABLE por tabla, en el orden topológico del paso 3c.
    for tabla in orden_tablas:
        # Separador con el nombre de la tabla y sus filas.
        lineas.append("")
        lineas.append("-- " + "-" * 72)
        lineas.append(f"-- Tabla: {tabla}   ({conteos[tabla]} filas en el momento del volcado)")
        lineas.append("-- " + "-" * 72)
        lineas.append(f"CREATE TABLE {tabla} (")

        # Las líneas de dentro del CREATE TABLE (columnas y restricciones).
        definiciones = []
        # Cada fila de columnas se desempaqueta en sus nueve datos; "_" y
        # "_pos" son los que no se usan (la tabla y la posición).
        for (_, nombre, tipo, longitud, prec, escala,
             nullable, defecto, _pos) in columnas[tabla]:
            # Nombre alineado a 24 caracteres (":<24") y el tipo.
            partes = [f"    {nombre:<24}", tipo_sql(tipo, longitud, prec, escala, defecto)]
            # is_nullable "NO" significa NOT NULL.
            if nullable == "NO":
                partes.append("NOT NULL")
            # El default de una columna SERIAL ya esta representado por
            # la propia palabra SERIAL; repetirlo seria incorrecto.
            if defecto and "nextval(" not in defecto:
                partes.append(f"DEFAULT {defecto}")
            # Las partes, unidas con espacios, forman la línea de la columna.
            definiciones.append(" ".join(partes))

        # Las restricciones de tabla se escriben dentro del CREATE TABLE,
        # detras de las columnas.
        # .get(tabla, []): una tabla sin restricciones da una lista vacía.
        for tipo, nombre, cols in restricciones.get(tabla, []):
            # PK y UNIQUE se escriben con sus columnas.
            if tipo == "PRIMARY KEY":
                definiciones.append(f"    CONSTRAINT {nombre} PRIMARY KEY ({cols})")
            elif tipo == "UNIQUE":
                definiciones.append(f"    CONSTRAINT {nombre} UNIQUE ({cols})")
            elif tipo == "FOREIGN KEY":
                # La definición completa viene de pg_get_constraintdef
                # (paso 3). Si no estuviera, antes se omitía EN SILENCIO y
                # el archivo salía sin esa clave; ahora es un error.
                if nombre not in definiciones_fk:
                    raise RuntimeError(f"No se encontró la definición de la clave foránea {nombre}")
                definiciones.append(f"    CONSTRAINT {nombre} {definiciones_fk[nombre]}")

        # Los CHECK de la tabla, detrás de las demás restricciones.
        for nombre, clausula in checks.get(tabla, []):
            definiciones.append(f"    CONSTRAINT {nombre} CHECK {clausula}")

        # ",\n".join pega todas las definiciones separadas por coma y
        # salto de linea: la ultima se queda sin coma, que es justo lo
        # que exige la sintaxis de SQL.
        lineas.append(",\n".join(definiciones))
        # El paréntesis que cierra el CREATE TABLE.
        lineas.append(");")

    # Sección de índices sueltos, con su cabecera.
    lineas.append("")
    lineas.append("-- " + "=" * 74)
    lineas.append("-- Indices que no respaldan ninguna restriccion (pg_indexes)")
    lineas.append("-- " + "=" * 74)
    # Una lista vacía cuenta como False: si no hay ninguno, se dice.
    if indices_sueltos:
        # indexdef ya viene como una sentencia CREATE INDEX completa; solo
        # le falta el punto y coma final.
        for definicion in indices_sueltos:
            lineas.append(f"{definicion};")
    else:
        lineas.append("-- (ninguno)")

    lineas.append("")
    lineas.append("-- " + "=" * 74)
    lineas.append("-- Row Level Security")
    lineas.append("-- " + "=" * 74)
    # SQL: nombre de cada tabla normal de public y si tiene RLS activado
    # (pg_class.relrowsecurity).
    cursor.execute(
        """
        SELECT relname, relrowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY relname;
        """
    )
    for nombre_tabla, rls_activo in cursor.fetchall():
        # Con RLS: la sentencia que lo activa, para recrearlo igual.
        if rls_activo:
            lineas.append(f"ALTER TABLE {nombre_tabla:<16} ENABLE ROW LEVEL SECURITY;")
        # Sin RLS: solo un comentario que lo deja a la vista.
        else:
            lineas.append(f"-- {nombre_tabla}: RLS NO habilitada")

    # Línea vacía final.
    lineas.append("")

    # Todas las líneas unidas con saltos de línea: el texto del archivo.
    contenido = "\n".join(lineas)
    # Crea la carpeta docs/ si no existiera (exist_ok=True: sin error si ya está).
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": sin el, en Windows write_text convierte cada salto de
    # linea en CRLF (el problema que documenta .gitattributes).
    SALIDA.write_text(contenido, encoding="utf-8", newline="\n")

    # Se cierran el cursor y la conexión.
    cursor.close()
    conexion.close()

    # Resumen en consola: dónde se escribió y cuántas cosas lleva.
    print(f"\nEscrito: {SALIDA}")
    print(f"  {len(contenido)} caracteres, {len(lineas)} lineas")
    print(f"  Tablas volcadas: {len(tablas)}")
    # sum(...) suma la longitud de la lista de cada tabla: el total.
    total_restricciones = sum(len(v) for v in restricciones.values())
    total_checks = sum(len(v) for v in checks.values())
    print(f"  Restricciones PK/UNIQUE/FK: {total_restricciones}")
    print(f"  CHECK: {total_checks}")
    print(f"  Indices sin restriccion: {len(indices_sueltos)}")

    # Última defensa: un archivo vacío (o solo con espacios) es un error.
    if len(contenido.strip()) == 0:
        print("ERROR: el archivo ha quedado vacio")
        sys.exit(1)


# Solo se ejecuta main() cuando el archivo se lanza directamente
# (python scripts/dump_schema.py), no cuando otro archivo lo importa.
if __name__ == "__main__":
    main()
