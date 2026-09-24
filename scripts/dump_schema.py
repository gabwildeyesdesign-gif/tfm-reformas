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
  - constraint_column_usage   -> a que columna apunta cada FK
  - check_constraints         -> la expresion de cada CHECK

Y ademas, fuera de information_schema:
  - pg_indexes + pg_constraint -> los indices que NO respaldan ninguna
    restriccion (ver paso 3b en main()).

Uso:
    .\\venv\\Scripts\\python.exe scripts\\dump_schema.py

No modifica NADA en la base de datos: solo hace SELECT.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
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
SALIDA = RAIZ / "docs" / "schema_actual.sql"

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
    if defecto and "nextval(" in defecto:
        return "BIGSERIAL" if tipo == "bigint" else "SERIAL"

    base = TIPOS_CORTOS.get(tipo, tipo.upper())
    if longitud is not None:
        return f"{base}({longitud})"
    if tipo == "numeric" and precision is not None:
        return f"{base}({precision},{escala})"
    return base


def main():
    if not DATABASE_URL:
        print("ERROR: no se encontro DATABASE_URL en el .env")
        sys.exit(1)

    conexion = psycopg2.connect(DATABASE_URL)
    cursor = conexion.cursor()

    # ---- 1. Que tablas hay -------------------------------------------
    # No se escribe la lista a mano a proposito: si manana aparece una
    # tabla nueva, este script la recoge sin que nadie lo edite.
    cursor.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name;
        """
    )
    tablas = [fila[0] for fila in cursor.fetchall()]
    print(f"Tablas encontradas: {len(tablas)} -> {', '.join(tablas)}")

    # ---- 2. Columnas de cada tabla -----------------------------------
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
    restricciones = {}
    for tabla, tipo, nombre, cols in cursor.fetchall():
        restricciones.setdefault(tabla, []).append((tipo, nombre, cols))

    # Destino de cada clave foranea.
    cursor.execute(
        """
        SELECT tc.constraint_name, ccu.table_name, ccu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.table_schema    = tc.table_schema
        WHERE tc.table_schema = 'public'
          AND tc.constraint_type = 'FOREIGN KEY';
        """
    )
    destinos_fk = {n: (t, c) for n, t, c in cursor.fetchall()}

    # CHECKs. information_schema.check_constraints incluye tambien los
    # CHECK automaticos que Postgres crea para cada columna NOT NULL
    # (con la forma "columna IS NOT NULL"); se filtran, porque el NOT
    # NULL ya se escribe en la propia linea de la columna y repetirlo
    # como CHECK seria ruido.
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
    indices_sueltos = [fila[0] for fila in cursor.fetchall()]

    # ---- 4. Filas por tabla, solo como dato informativo --------------
    conteos = {}
    for tabla in tablas:
        cursor.execute(f'SELECT COUNT(*) FROM "{tabla}";')
        conteos[tabla] = cursor.fetchone()[0]

    # ---- 5. Construccion del archivo ---------------------------------
    # Se va acumulando en una lista de lineas y al final se unen todas.
    # Es mas eficiente que ir concatenando textos uno a uno.
    lineas = []
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lineas.append("-- " + "=" * 74)
    lineas.append("-- ESQUEMA REAL DE LA BASE DE DATOS - ARCHIVO GENERADO AUTOMATICAMENTE")
    lineas.append("--")
    lineas.append(f"-- Generado por scripts/dump_schema.py el {ahora}")
    lineas.append("-- NO EDITAR A MANO: se sobrescribe al volver a ejecutar el script.")
    lineas.append("--")
    lineas.append("-- Reconstruido leyendo information_schema (columns,")
    lineas.append("-- table_constraints, key_column_usage, constraint_column_usage y")
    lineas.append("-- check_constraints) y pg_indexes, no copiado de ningun archivo previo.")
    lineas.append("--")
    lineas.append(f"-- Tablas: {len(tablas)}")
    lineas.append("-- " + "=" * 74)
    lineas.append("")

    for tabla in tablas:
        lineas.append("")
        lineas.append("-- " + "-" * 72)
        lineas.append(f"-- Tabla: {tabla}   ({conteos[tabla]} filas en el momento del volcado)")
        lineas.append("-- " + "-" * 72)
        lineas.append(f"CREATE TABLE {tabla} (")

        definiciones = []
        for (_, nombre, tipo, longitud, prec, escala,
             nullable, defecto, _pos) in columnas[tabla]:
            partes = [f"    {nombre:<24}", tipo_sql(tipo, longitud, prec, escala, defecto)]
            if nullable == "NO":
                partes.append("NOT NULL")
            # El default de una columna SERIAL ya esta representado por
            # la propia palabra SERIAL; repetirlo seria incorrecto.
            if defecto and "nextval(" not in defecto:
                partes.append(f"DEFAULT {defecto}")
            definiciones.append(" ".join(partes))

        # Las restricciones de tabla se escriben dentro del CREATE TABLE,
        # detras de las columnas.
        for tipo, nombre, cols in restricciones.get(tabla, []):
            if tipo == "PRIMARY KEY":
                definiciones.append(f"    CONSTRAINT {nombre} PRIMARY KEY ({cols})")
            elif tipo == "UNIQUE":
                definiciones.append(f"    CONSTRAINT {nombre} UNIQUE ({cols})")
            elif tipo == "FOREIGN KEY":
                destino = destinos_fk.get(nombre)
                if destino:
                    definiciones.append(
                        f"    CONSTRAINT {nombre} FOREIGN KEY ({cols}) "
                        f"REFERENCES {destino[0]}({destino[1]})"
                    )

        for nombre, clausula in checks.get(tabla, []):
            definiciones.append(f"    CONSTRAINT {nombre} CHECK {clausula}")

        # ",\n".join pega todas las definiciones separadas por coma y
        # salto de linea: la ultima se queda sin coma, que es justo lo
        # que exige la sintaxis de SQL.
        lineas.append(",\n".join(definiciones))
        lineas.append(");")

    lineas.append("")
    lineas.append("-- " + "=" * 74)
    lineas.append("-- Indices que no respaldan ninguna restriccion (pg_indexes)")
    lineas.append("-- " + "=" * 74)
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
    cursor.execute(
        """
        SELECT relname, relrowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY relname;
        """
    )
    for nombre_tabla, rls_activo in cursor.fetchall():
        if rls_activo:
            lineas.append(f"ALTER TABLE {nombre_tabla:<16} ENABLE ROW LEVEL SECURITY;")
        else:
            lineas.append(f"-- {nombre_tabla}: RLS NO habilitada")

    lineas.append("")

    contenido = "\n".join(lineas)
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": sin el, en Windows write_text convierte cada salto de
    # linea en CRLF (el problema que documenta .gitattributes).
    SALIDA.write_text(contenido, encoding="utf-8", newline="\n")

    cursor.close()
    conexion.close()

    print(f"\nEscrito: {SALIDA}")
    print(f"  {len(contenido)} caracteres, {len(lineas)} lineas")
    print(f"  Tablas volcadas: {len(tablas)}")
    total_restricciones = sum(len(v) for v in restricciones.values())
    total_checks = sum(len(v) for v in checks.values())
    print(f"  Restricciones PK/UNIQUE/FK: {total_restricciones}")
    print(f"  CHECK: {total_checks}")
    print(f"  Indices sin restriccion: {len(indices_sueltos)}")

    if len(contenido.strip()) == 0:
        print("ERROR: el archivo ha quedado vacio")
        sys.exit(1)


if __name__ == "__main__":
    main()
