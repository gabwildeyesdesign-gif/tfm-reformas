"""
VUELTA ATRÁS de paso12 - NO SE EJECUTA salvo que haga falta deshacer la
migración, y solo con la confirmación explícita de Gabi.

Deja decisiones_gate como estaba después de paso10 y paso10b (plan
docs/Plan_Resultado_Llamadas.txt, sección 4.3):
  1. Si hay alguna fila que no es del Gate o que tiene visita cancelada, SE
     PARA y avisa con sus ids: no la borra, porque no puede saber si es una
     fila propia de un script o una llamada real. Antes hay que quitarla a
     mano (cada script, por su marca), o decidir qué hacer con ella.
  2. Borra los CHECK nuevos (tipo_llamada y tipo_resultado).
  3. Restaura decision (paso10b), motivo (paso10) y coherencia (paso10b) con
     su texto EXACTO de entonces (DROP + ADD con el mismo nombre).
  4. Borra la clave foránea de la visita cancelada y las dos columnas.
  5. Quita el COMMENT ON TABLE.
Una sola transacción: o todo o nada.

Freno: sin el argumento --confirmo-vuelta-atras no hace nada.
"""

# os: el entorno. sys: argumentos, consola y salida. Path: rutas.
import os
import sys
from pathlib import Path

# psycopg2: hablar con PostgreSQL. load_dotenv: leer el .env.
import psycopg2
from dotenv import load_dotenv

# Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
sys.stdout.reconfigure(encoding="utf-8")

# El freno: sin la bandera, se para aquí sin conectarse a nada.
if "--confirmo-vuelta-atras" not in sys.argv:
    print("No se ejecuta: falta --confirmo-vuelta-atras (solo con la confirmación explícita de Gabi).")
    sys.exit(2)

# parents[2]: este archivo vive en scripts/migraciones_ejecutadas/.
RAIZ_REPO = Path(__file__).resolve().parents[2]
# Carga el .env de la raíz.
load_dotenv(RAIZ_REPO / ".env")

# La tabla y lo que añadió paso12.
TABLA = "decisiones_gate"
CHECKS_NUEVOS = ("chk_decisiones_gate_tipo_resultado", "chk_decisiones_gate_tipo_llamada")
FK_CANCELADA = "decisiones_gate_visita_cancelada_oportunidad_fkey"
COLUMNAS_NUEVAS = ("visita_cancelada_id", "tipo_llamada")

# Las definiciones de ANTES de paso12, copiadas de paso10 (motivo) y paso10b
# (decision sin cambios desde paso10, y coherencia), por nombre.
CHECKS_ANTERIORES = {
    "chk_decisiones_gate_decision": "decision IN ('visita_acordada', 'descartar')",
    "chk_decisiones_gate_motivo":
        "motivo IS NULL OR motivo IN ('precio', 'plazo', 'no_contesta', 'proyecto_no_viable', 'otro')",
    "chk_decisiones_gate_coherencia":
        "(decision <> 'visita_acordada' OR (visita_id IS NOT NULL AND motivo IS NULL)) "
        "AND (decision <> 'descartar' OR (motivo IS NOT NULL AND visita_id IS NULL))",
}

# Una conexión y su cursor.
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()

# try / except: cualquier error deshace todo y se enseña.
try:
    # 1. ¿Hay filas que solo paso12 permite? Si las hay, se para.
    # SQL: los ids de esas filas (no se imprime ningún otro dato).
    cur.execute(f"SELECT id FROM {TABLA} WHERE tipo_llamada <> 'gate' OR visita_cancelada_id IS NOT NULL ORDER BY id;")
    impiden = [f[0] for f in cur.fetchall()]
    # Con alguna, no se toca nada: se avisa y se sale sin cambios.
    if impiden:
        raise RuntimeError(f"Hay {len(impiden)} filas que solo admite paso12 (ids {impiden}); no se borra nada.")

    # 2. Los CHECK nuevos (IF EXISTS: si ya no están, no falla).
    for nombre in CHECKS_NUEVOS:
        # SQL: borra ese CHECK.
        cur.execute(f"ALTER TABLE {TABLA} DROP CONSTRAINT IF EXISTS {nombre};")
        print(f"  2. {nombre} borrado")

    # 3. Los tres CHECK ampliados vuelven a su definición anterior.
    for nombre, condicion in CHECKS_ANTERIORES.items():
        # SQL 1: borra la versión de paso12. SQL 2: la crea como antes.
        cur.execute(f"ALTER TABLE {TABLA} DROP CONSTRAINT IF EXISTS {nombre};")
        cur.execute(f"ALTER TABLE {TABLA} ADD CONSTRAINT {nombre} CHECK ({condicion});")
        print(f"  3. {nombre} restaurado")

    # 4. La clave foránea y las dos columnas (la clave primero: depende de
    #    visita_cancelada_id).
    # SQL: borra la clave foránea doble de la visita cancelada.
    cur.execute(f"ALTER TABLE {TABLA} DROP CONSTRAINT IF EXISTS {FK_CANCELADA};")
    for columna in COLUMNAS_NUEVAS:
        # SQL: borra la columna (y su DEFAULT).
        cur.execute(f"ALTER TABLE {TABLA} DROP COLUMN IF EXISTS {columna};")
        print(f"  4. columna {columna} borrada")

    # 5. Sin comentario, como estaba.
    # SQL: quita el COMMENT ON TABLE.
    cur.execute(f"COMMENT ON TABLE {TABLA} IS NULL;")
    print("  5. COMMENT ON TABLE quitado")

    # commit: todo a la vez.
    cn.commit()
    print("  commit() ejecutado: decisiones_gate vuelve a paso10b")
except Exception:
    # Deshace todo y enseña el error.
    cn.rollback()
    print("  rollback() ejecutado, no se ha cambiado nada")
    raise
finally:
    # Se cierra la conexión, pase lo que pase.
    cn.close()
