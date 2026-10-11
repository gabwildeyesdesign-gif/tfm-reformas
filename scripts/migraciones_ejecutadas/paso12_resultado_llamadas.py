"""
MIGRACIÓN DE ESQUEMA - NO ES UN SCRIPT DE VERIFICACIÓN.

paso12 (Bloque B2-M de docs/Plan_Resultado_Llamadas.txt, sección 4.1): prepara
decisiones_gate para guardar el resultado de los TRES tipos de llamada (Gate,
seguimiento y confirmación de una visita pedida desde el chat).

Solo AÑADE y AMPLÍA (expand): main sigue funcionando contra la base de datos
migrada, porque main solo escribe 'visita_acordada' o 'descartar' sin las
columnas nuevas (el DEFAULT pone 'gate' y los CHECK ampliados lo aceptan), y
gate-avisos y la ficha de main no leen las columnas nuevas.
  1. Columna tipo_llamada VARCHAR(20) NOT NULL DEFAULT 'gate' (las 6 filas
     reales son del Gate).
  2. Columna visita_cancelada_id INTEGER NULL: la visita 'solicitada' que se
     cancela en otra_hora y en cliente_cancela (P11).
  3. Su clave foránea DOBLE (visita_cancelada_id, oportunidad_id) ->
     visitas (id, oportunidad_id), como la de visita_id: la visita
     cancelada tiene que ser de la MISMA oportunidad.
  4. CHECK chk_decisiones_gate_tipo_llamada: los tres tipos.
  5. CHECK chk_decisiones_gate_decision, ampliado a los cinco resultados (P5).
  6. CHECK chk_decisiones_gate_motivo, ampliado con 'sin_decision' (D29.3).
  7. CHECK chk_decisiones_gate_coherencia, ampliado: qué ACOMPAÑA a cada
     resultado (patrón de paso10b, "si es X, entonces ...").
  8. CHECK chk_decisiones_gate_tipo_resultado: qué resultados tiene cada
     tipo, como IGUALDAD (sin repetir qué valores existen).
  9. COMMENT ON TABLE (P3 = b: la tabla conserva su nombre histórico).
El UNIQUE (oportunidad_id), el CHECK del informe, la clave doble de
visita_id y RLS NO cambian.

Una sola transacción: o se aplica todo o nada. Idempotente: cada paso mira
antes si ya está hecho (los CHECK, comparando su definición con la que
escribiría Postgres). Ejecutar solo con la confirmación explícita de Gabi
("confirmo la migración paso12"). Vuelta atrás: paso12_vuelta_atras.py (no
se ejecuta salvo que haga falta).
"""

# os: leer DATABASE_URL del entorno. sys: tildes en la consola.
import os
import sys
# Path: rutas sin escribirlas a mano.
from pathlib import Path

# psycopg2: hablar con PostgreSQL. load_dotenv: leer el .env.
import psycopg2
from dotenv import load_dotenv

# Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
sys.stdout.reconfigure(encoding="utf-8")

# parents[2]: este archivo vive en scripts/migraciones_ejecutadas/.
RAIZ_REPO = Path(__file__).resolve().parents[2]
# Carga el .env de la raíz para que os.getenv("DATABASE_URL") lo encuentre.
load_dotenv(RAIZ_REPO / ".env")

# ----------------------------------------------------------------------
# Nombres y definiciones (textos FIJOS de este script, nunca datos de fuera)
# ----------------------------------------------------------------------
TABLA = "decisiones_gate"
# Las dos columnas nuevas.
COL_TIPO = "tipo_llamada"
COL_CANCELADA = "visita_cancelada_id"
# La clave foránea doble de la visita cancelada.
FK_CANCELADA = "decisiones_gate_visita_cancelada_oportunidad_fkey"

# Las condiciones de los cinco CHECK, por nombre. Se usan dos veces: para
# saber cómo las normaliza Postgres y para crearlas.
CHECKS = {
    # Los tres tipos de llamada (D25.9).
    "chk_decisiones_gate_tipo_llamada":
        "tipo_llamada IN ('gate', 'seguimiento', 'confirmacion_visita')",
    # Los cinco resultados (P5).
    "chk_decisiones_gate_decision":
        "decision IN ('visita_acordada', 'descartar', 'confirmar_hora', 'otra_hora', 'cliente_cancela')",
    # El catálogo de motivos, con 'sin_decision' (D29.3).
    "chk_decisiones_gate_motivo":
        "motivo IS NULL OR motivo IN ('precio', 'plazo', 'no_contesta', 'proyecto_no_viable', 'otro', 'sin_decision')",
    # Qué acompaña a cada resultado. Cada línea se lee "si es X, entonces
    # Y"; un valor que no es ninguno las cumple todas (lo para el CHECK de
    # decision, como en paso10b).
    "chk_decisiones_gate_coherencia":
        # Con visita confirmada: visita_id y sin motivo.
        "(decision NOT IN ('visita_acordada', 'confirmar_hora', 'otra_hora') "
        "OR (visita_id IS NOT NULL AND motivo IS NULL)) "
        # descartar: motivo y sin visita.
        "AND (decision <> 'descartar' OR (motivo IS NOT NULL AND visita_id IS NULL)) "
        # cliente_cancela: motivo (ni 'no_contesta' ni 'sin_decision': quien
        # cancela, contestó y decidió), sin visita confirmada.
        "AND (decision <> 'cliente_cancela' OR (motivo IS NOT NULL AND visita_id IS NULL "
        "AND motivo NOT IN ('no_contesta', 'sin_decision'))) "
        # visita_cancelada_id existe en otra_hora y cliente_cancela, y solo ahí...
        "AND ((decision IN ('otra_hora', 'cliente_cancela')) = (visita_cancelada_id IS NOT NULL)) "
        # ...y nunca es la misma visita que la confirmada.
        "AND (visita_cancelada_id IS NULL OR visita_id IS NULL OR visita_cancelada_id <> visita_id)",
    # Qué resultados tiene cada tipo: los de la confirmación de visita si y
    # solo si el tipo es confirmacion_visita. Como igualdad, no repite qué
    # valores existen.
    "chk_decisiones_gate_tipo_resultado":
        "(tipo_llamada = 'confirmacion_visita') = (decision IN ('confirmar_hora', 'otra_hora', 'cliente_cancela'))",
}

# El comentario de la tabla (P3 = b), en una línea.
COMENTARIO = ("Resultados de las llamadas de administración (Gate, seguimiento y confirmación de visita). "
              "Nombre histórico de la tabla: ver docs/Plan_Resultado_Llamadas.txt.")

# Una sola conexión y su cursor (el objeto que envía el SQL y lee resultados).
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()


def columnas():
    """nombre -> (tipo, ¿admite NULL?, valor por defecto) de decisiones_gate."""
    # SQL: las columnas de la tabla en information_schema.
    cur.execute(
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_name = %s;",
        (TABLA,),
    )
    # Un diccionario: la primera columna es la clave; el resto, el valor.
    return {f[0]: f[1:] for f in cur.fetchall()}


def restricciones():
    """nombre -> definición de cada restricción de decisiones_gate, como la guarda Postgres."""
    # SQL: nombre y definición normalizada (pg_get_constraintdef).
    cur.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass;", (TABLA,))
    # Diccionario nombre -> definición.
    return dict(cur.fetchall())


def checks_normalizados():
    """
    Cómo escribiría Postgres cada CHECK de CHECKS, sin tocar decisiones_gate
    (mismo truco que paso10b): una tabla TEMPORAL con las columnas que usan,
    del mismo tipo, dentro de un SAVEPOINT que se deshace al final.
    """
    # Punto de guardado: se volverá a él y la tabla temporal desaparecerá.
    cur.execute("SAVEPOINT normalizar;")
    # try / finally: pase lo que pase, se deshace la tabla temporal.
    try:
        # Los CHECK, uno por línea, con su nombre de verdad.
        restr = ",\n".join(f"CONSTRAINT {n} CHECK ({c})" for n, c in CHECKS.items())
        # SQL: la tabla temporal con las mismas columnas y tipos.
        cur.execute(
            f"""
            CREATE TEMP TABLE normalizar_paso12 (
                visita_id           INTEGER NULL,
                visita_cancelada_id INTEGER NULL,
                decision            VARCHAR(20) NOT NULL,
                motivo              VARCHAR(30) NULL,
                tipo_llamada        VARCHAR(20) NOT NULL,
                {restr}
            );
            """
        )
        # SQL: cómo ha guardado Postgres cada uno de esos CHECK.
        cur.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'normalizar_paso12'::regclass;"
        )
        # Diccionario nombre -> definición normalizada.
        return dict(cur.fetchall())
    finally:
        # Siempre: deshacer la tabla temporal y quitar el punto de guardado.
        cur.execute("ROLLBACK TO SAVEPOINT normalizar;")
        cur.execute("RELEASE SAVEPOINT normalizar;")


def comentario_actual():
    """El COMMENT ON TABLE de decisiones_gate, o None."""
    # SQL: obj_description lee el comentario de la tabla.
    cur.execute("SELECT obj_description(%s::regclass, 'pg_class');", (TABLA,))
    # La única columna de la única fila.
    return cur.fetchone()[0]


def ensenar(titulo):
    """Imprime columnas, restricciones y comentario (foto ANTES / DESPUÉS)."""
    print(f"=== {titulo} ===")
    # Las columnas, en orden alfabético.
    for nombre, datos in sorted(columnas().items()):
        print(f"    columna {nombre:20} {datos}")
    # Las restricciones, en orden alfabético.
    for nombre, definicion in sorted(restricciones().items()):
        print(f"    {nombre}: {definicion}")
    # El comentario.
    print(f"    COMMENT: {comentario_actual()!r}")


# Foto ANTES de cambiar nada.
ensenar("ANTES")

# try / except: si cualquier paso falla, rollback de TODO y se vuelve a
# lanzar el error para verlo (mismo patrón que paso10 y paso10b).
try:
    # 0. La tabla de paso10 tiene que existir; si no, no hay nada que ampliar.
    if not columnas():
        raise RuntimeError(f"{TABLA} no existe: ¿se aplicó paso10?")
    print("\n--- Pasos ---")

    # 1. tipo_llamada. NOT NULL DEFAULT: Postgres rellena las filas que ya
    #    existen con 'gate' (las 6 reales lo son).
    if COL_TIPO not in columnas():
        # SQL: añade la columna con su valor por defecto.
        cur.execute(f"ALTER TABLE {TABLA} ADD COLUMN {COL_TIPO} VARCHAR(20) NOT NULL DEFAULT 'gate';")
        print(f"  1. columna {COL_TIPO} añadida (DEFAULT 'gate')")
    else:
        print(f"  1. columna {COL_TIPO} ya existe: no se toca")

    # 2. visita_cancelada_id, que admite NULL (las filas de hoy no tienen).
    if COL_CANCELADA not in columnas():
        # SQL: añade la columna, vacía en todas las filas.
        cur.execute(f"ALTER TABLE {TABLA} ADD COLUMN {COL_CANCELADA} INTEGER NULL;")
        print(f"  2. columna {COL_CANCELADA} añadida")
    else:
        print(f"  2. columna {COL_CANCELADA} ya existe: no se toca")

    # 3. La clave foránea doble de la visita cancelada (necesita la UNIQUE
    #    (id, oportunidad_id) de visitas, que existe desde paso10).
    if FK_CANCELADA not in restricciones():
        # SQL: la clave doble; con MATCH SIMPLE (por defecto), si la columna
        # es NULL no se comprueba.
        cur.execute(
            f"ALTER TABLE {TABLA} ADD CONSTRAINT {FK_CANCELADA} "
            f"FOREIGN KEY ({COL_CANCELADA}, oportunidad_id) REFERENCES visitas (id, oportunidad_id);"
        )
        print(f"  3. {FK_CANCELADA} añadida")
    else:
        print(f"  3. {FK_CANCELADA} ya existe: no se toca")

    # 4-8. Los cinco CHECK: si ya tienen la definición nueva, no se tocan; si
    #      no, se borran (si existen) y se crean con el MISMO nombre. Las dos
    #      órdenes van en esta transacción: en ningún momento visible la
    #      tabla se queda sin la regla. ADD CONSTRAINT comprueba las filas
    #      existentes: si una incumpliera, fallaría y el except desharía todo.
    objetivo = checks_normalizados()
    actuales = restricciones()
    for paso, (nombre, condicion) in enumerate(CHECKS.items(), start=4):
        # Ya está como debe (segunda ejecución): no se hace nada.
        if actuales.get(nombre) == objetivo[nombre]:
            print(f"  {paso}. {nombre} ya tiene la definición nueva: no se toca")
            continue
        # Existe con otra definición: se borra antes de crearla.
        if nombre in actuales:
            # SQL: borra la versión vieja.
            cur.execute(f"ALTER TABLE {TABLA} DROP CONSTRAINT {nombre};")
        # SQL: crea la versión nueva con el mismo nombre.
        cur.execute(f"ALTER TABLE {TABLA} ADD CONSTRAINT {nombre} CHECK ({condicion});")
        print(f"  {paso}. {nombre} -> {'sustituida' if nombre in actuales else 'añadida'}")

    # 9. El comentario de la tabla.
    if comentario_actual() != COMENTARIO:
        # SQL: COMMENT ON TABLE no admite parámetros %s para el texto; se usa
        # format('%L') de Postgres, que lo escribe como literal seguro.
        cur.execute("SELECT format('COMMENT ON TABLE decisiones_gate IS %%L;', %s);", (COMENTARIO,))
        # SQL: la orden ya montada por Postgres.
        cur.execute(cur.fetchone()[0])
        print("  9. COMMENT ON TABLE escrito")
    else:
        print("  9. COMMENT ON TABLE ya escrito: no se toca")

    # commit: hace definitivo todo lo anterior a la vez.
    cn.commit()
    print("  commit() ejecutado")
except Exception:
    # Deshace todo lo del try y vuelve a lanzar el error para verlo.
    cn.rollback()
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    raise

# Foto DESPUÉS, y comprobación de que los cinco CHECK quedaron como se querían.
print()
ensenar("DESPUÉS")
finales = restricciones()
print(f"\n    ¿los 5 CHECK iguales a la definición nueva? {all(finales.get(n) == objetivo[n] for n in CHECKS)}")

# Se cierran el cursor y la conexión.
cur.close()
cn.close()
