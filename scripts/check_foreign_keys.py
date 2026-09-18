"""
PASO 0 - Claves foraneas reales, consultadas por information_schema
usando constraint_type = 'FOREIGN KEY'. Solo lectura.
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
# La raiz del repositorio, calculada desde la ubicacion de este archivo
# (scripts/ -> raiz) en vez de con una ruta absoluta escrita a mano, para
# que funcione en cualquier maquina. Misma tecnica que
# scripts/check_graceful_shutdown.py.
RAIZ_REPO = Path(__file__).resolve().parents[1]
load_dotenv(RAIZ_REPO / ".env")
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()

print("=" * 88)
print("TODAS LAS FOREIGN KEY DEL ESQUEMA public (information_schema)")
print("=" * 88)
cur.execute(
    """
    SELECT tc.table_name        AS tabla_origen,
           kcu.column_name      AS columna_origen,
           ccu.table_name       AS tabla_destino,
           ccu.column_name      AS columna_destino,
           tc.constraint_name,
           rc.update_rule,
           rc.delete_rule
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema    = kcu.table_schema
    JOIN information_schema.constraint_column_usage ccu
      ON ccu.constraint_name = tc.constraint_name
     AND ccu.table_schema    = tc.table_schema
    JOIN information_schema.referential_constraints rc
      ON rc.constraint_name  = tc.constraint_name
     AND rc.constraint_schema = tc.table_schema
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND tc.table_schema = 'public'
    ORDER BY tc.table_name, kcu.column_name;
    """
)
filas = cur.fetchall()
print(f"{'ORIGEN':<38}{'DESTINO':<24}{'ON UPDATE':<12}ON DELETE")
print("-" * 88)
for t_o, c_o, t_d, c_d, nombre, upd, dele in filas:
    print(f"{t_o + '.' + c_o:<38}{t_d + '.' + c_d:<24}{upd:<12}{dele}")
    print(f"   constraint_name = {nombre}")
print(f"\nTotal de FOREIGN KEY en el esquema: {len(filas)}")

print()
print("=" * 88)
print("LAS TRES QUE SE PIDEN EXPLICITAMENTE")
print("=" * 88)
objetivo = [
    ("leads", "cliente_id"),
    ("presupuestos", "oportunidad_id"),
    ("visitas", "oportunidad_id"),
]
encontradas = {(f[0], f[1]): f for f in filas}
for tabla, columna in objetivo:
    clave = (tabla, columna)
    if clave in encontradas:
        f = encontradas[clave]
        print(f"  {tabla}.{columna}")
        print(f"      -> EXISTE, apunta a {f[2]}.{f[3]}")
        print(f"      -> constraint: {f[4]}  (ON UPDATE {f[5]} / ON DELETE {f[6]})")
    else:
        print(f"  {tabla}.{columna}  -> NO TIENE FOREIGN KEY")

cur.close()
cn.close()
print("\nFIN - no se ha modificado nada.")
