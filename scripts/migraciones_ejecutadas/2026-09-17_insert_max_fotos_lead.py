"""
MIGRACION DE DATOS YA EJECUTADA - NO ES UN SCRIPT DE VERIFICACION.

Ejecutada una sola vez el 2026-09-17 contra la base de datos real.
Se conserva aqui como EVIDENCIA de como entro el dato, no para volver a
lanzarlo de forma rutinaria.

Que hizo: insertar la fila 'max_fotos_lead' = 5 en la tabla
reglas_negocio, que no existia aunque el diseno la daba por supuesta.

Por que esta en scripts/migraciones_ejecutadas/ y no en scripts/ a secas:
CLAUDE.md define scripts/ como "verificacion, no produccion". Los
archivos de scripts/ son comprobaciones repetibles que se pueden lanzar
en cualquier momento para confirmar que el sistema sigue sano. Este NO
lo es: es un cambio de datos de un solo uso. Mezclarlos borraria esa
distincion, que para la memoria del TFM separa dos categorias de
artefacto distintas (la solucion y la evidencia de que se probo).

Es idempotente (ON CONFLICT (clave) DO NOTHING), asi que volver a
ejecutarlo no duplicaria ni romperia nada; simplemente no haria nada.
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

# La consola de Windows no usa UTF-8 por defecto; sin esto, imprimir las
# descripciones con tildes saldria con caracteres rotos.
sys.stdout.reconfigure(encoding="utf-8")

# Este archivo vive en scripts/migraciones_ejecutadas/, DOS niveles por
# debajo de la raiz del repositorio, asi que se sube con parents[2] (los
# scripts de scripts/ usan parents[1]). Se calcula asi, y no con una ruta
# absoluta escrita a mano, para que funcione en cualquier maquina.
RAIZ_REPO = Path(__file__).resolve().parents[2]
load_dotenv(RAIZ_REPO / ".env")
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()

DESCRIPCION = (
    "Número máximo de fotos aceptadas por lead. Acota cuántas URLs firmadas "
    "de subida se emiten por formulario y cuántas rutas se guardan en "
    "leads.fotos_urls, limitando el coste de almacenamiento y el abuso del "
    "formulario público. Lo valida el esquema Pydantic de POST /leads."
)

print("=== ANTES ===")
cur.execute("SELECT COUNT(*) FROM reglas_negocio WHERE clave = 'max_fotos_lead'")
print(f"  filas con clave 'max_fotos_lead': {cur.fetchone()[0]}")

# ON CONFLICT DO NOTHING: si la fila ya existiera (por la restriccion
# UNIQUE de 'clave'), no falla ni la duplica. Hace el script repetible.
# Los %s son marcadores de posicion: psycopg2 sustituye ahi los valores
# de la tupla de forma segura (escapandolos), en vez de pegarlos con
# formato de texto, que es como se abren las inyecciones SQL.
cur.execute(
    """
    INSERT INTO reglas_negocio (clave, valor, descripcion)
    VALUES (%s, %s, %s)
    ON CONFLICT (clave) DO NOTHING
    RETURNING id;
    """,
    ("max_fotos_lead", 5, DESCRIPCION),
)
devuelto = cur.fetchone()
print(f"\n  INSERT ... RETURNING id -> {devuelto}")
print(f"  filas afectadas (cur.rowcount) -> {cur.rowcount}")

# psycopg2 NO confirma solo: sin este commit, el INSERT se perderia al
# cerrar la conexion. Es exactamente el bug del Paso 3.
cn.commit()
print("  commit() ejecutado")

print("\n=== DESPUES: SELECT de comprobacion ===")
cur.execute(
    """
    SELECT id, clave, valor, fecha_actualizacion, descripcion
    FROM reglas_negocio
    WHERE clave = 'max_fotos_lead';
    """
)
fila = cur.fetchone()
if fila is None:
    print("  ERROR: la fila NO esta en la tabla")
    sys.exit(1)
print(f"  id                  = {fila[0]}")
print(f"  clave               = {fila[1]!r}")
print(f"  valor               = {fila[2]!r}   (tipo Python: {type(fila[2]).__name__})")
print(f"  fecha_actualizacion = {fila[3]}")
print(f"  descripcion         = {fila[4]}")

print("\n=== TABLA COMPLETA (las 5 filas) ===")
cur.execute("SELECT id, clave, valor FROM reglas_negocio ORDER BY id")
for r in cur.fetchall():
    print(f"  {r[0]}  {r[1]:<34} {r[2]}")

cur.close()
cn.close()
