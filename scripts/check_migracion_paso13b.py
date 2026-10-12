"""
Verificación de la migración paso13b (plan: docs/Plan_Agente2_Calendar.txt,
secciones 3.5 y 11): el CHECK chk_visitas_texto_cliente_longitud de visitas
tiene que ser de 1 a 300 caracteres (D26.7, defensa doble con
MAX_TEXTO_VISITA = 300 de Pydantic).

Qué comprueba:
  1. La definición del CHECK, como la escribe Postgres: ">= 1" y "<= 300".
  2. Su comportamiento, dentro de UNA transacción que termina siempre en
     ROLLBACK, con datos propios (email check-paso13b@example.com): 301
     caracteres -> rechazado; 300 "á" (600 bytes) -> aceptado (cuenta
     caracteres, no bytes); texto vacío -> rechazado. Cada intento en su
     propio SAVEPOINT. Si un intento que debe fallar NO falla, FALLO
     explícito ("la excepción se ha tragado").
  3. Ninguna fila real supera 300 caracteres (solo lectura).

Ejecutado ANTES de la migración tiene que dar FALLO (la definición dice
1000 y 301 caracteres se aceptan): es la prueba en negativo de este script.
No pasa su cursor a ningún código de app/ (solo SQL propio), por eso no
necesita ConexionSinCommit. Al final comprueba que no queda el cliente de
prueba.

Código de salida: 0 si todo está bien, 1 si hay algún fallo.
"""

# sys: sys.path, la salida estándar y el código de salida.
import sys
# Path: rutas.
from pathlib import Path

# La raíz del repositorio en sys.path (para "import app...").
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Primera en la lista de búsqueda.
sys.path.insert(0, str(RAIZ_REPO))
# Tildes correctas en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# psycopg2: hablar con PostgreSQL.
import psycopg2
# Json: un diccionario de Python a una columna JSONB.
from psycopg2.extras import Json

# La cadena de conexión del .env (nunca se imprime).
from app.config import DATABASE_URL

# El CHECK de visitas y el máximo esperado.
RESTRICCION = "chk_visitas_texto_cliente_longitud"
# El máximo nuevo.
MAXIMO = 300
# La marca propia (dominio reservado).
EMAIL = "check-paso13b@example.com"

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


# Conexión propia; todo lo que escribe se deshace al final.
cn = psycopg2.connect(DATABASE_URL)
# El cursor.
cur = cn.cursor()

# ----------------------------------------------------------------------
print("1. La definición del CHECK")
# SQL: la definición, como la escribe Postgres.
cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s;", (RESTRICCION,))
# La fila, o None.
fila = cur.fetchone()
# El texto, o vacío.
definicion = fila[0] if fila else ""
# Se enseña.
print(f"    {RESTRICCION}: {definicion or 'NO EXISTE'}")
# 1..300.
comprobar(f"el CHECK es de 1 a {MAXIMO} caracteres", ">= 1" in definicion and f"<= {MAXIMO}" in definicion)

# ----------------------------------------------------------------------
print("\n2. Su comportamiento (todo se deshace al final)")


def intento(texto):
    """Inserta una visita propia con ese texto en un SAVEPOINT. Devuelve
    None si se aceptó, o el error de Postgres si se rechazó."""
    # Un punto al que volver.
    cur.execute("SAVEPOINT paso13b;")
    # try: el INSERT puede fallar por el CHECK.
    try:
        # SQL: la visita, en la oportunidad propia, cancelada (no ocupa hueco).
        cur.execute("INSERT INTO visitas (oportunidad_id, fecha_propuesta, estado, texto_cliente) "
                    "VALUES (%s, '2030-01-07 10:00+01', 'cancelada', %s);", (OPORTUNIDAD, texto))
    # Rechazado: se devuelve el error.
    except psycopg2.Error as error:
        # Se vuelve al punto de guardado (la transacción sigue viva).
        cur.execute("ROLLBACK TO SAVEPOINT paso13b;")
        # El error.
        return error
    # Aceptado: también se deshace (solo se quería saber si pasaba).
    cur.execute("ROLLBACK TO SAVEPOINT paso13b;")
    # Ningún error.
    return None


# try / finally: pase lo que pase, ROLLBACK.
try:
    # SQL: el cliente propio.
    cur.execute("INSERT INTO clientes (nombre, email, telefono) VALUES ('Prueba paso13b', %s, '600000000') "
                "RETURNING id;", (EMAIL,))
    # Su id.
    CLIENTE = cur.fetchone()[0]
    # SQL: su lead.
    cur.execute("INSERT INTO leads (cliente_id, canal, datos_estructurados) VALUES (%s, 'check_paso13b', %s) "
                "RETURNING id;", (CLIENTE, Json({"m2": 10})))
    # Su id.
    LEAD = cur.fetchone()[0]
    # SQL: su oportunidad.
    cur.execute("INSERT INTO oportunidades (lead_id, tipo_reforma, estado) VALUES (%s, 'bano', 'nueva') RETURNING id;",
                (LEAD,))
    # Su id.
    OPORTUNIDAD = cur.fetchone()[0]
    # 301 caracteres: tiene que rechazarse (rama else: si pasa, FALLO).
    error = intento("x" * (MAXIMO + 1))
    # Rechazado por ESTE CHECK.
    if error is not None:
        # Se comprueba que es el CHECK y no otra cosa.
        comprobar(f"{MAXIMO + 1} caracteres -> rechazado por {RESTRICCION}",
                  error.pgcode == "23514" and RESTRICCION in str(error), f"({error.pgcode})")
    # No se rechazó.
    else:
        # FALLO explícito.
        comprobar(f"{MAXIMO + 1} caracteres -> rechazado", False, "(la excepción se ha tragado: se acepta)")
    # 300 "á": aceptado (caracteres, no bytes).
    error = intento("á" * MAXIMO)
    # Sin error.
    comprobar(f"{MAXIMO} 'á' ({MAXIMO * 2} bytes) -> aceptado", error is None, f"({error and error.pgcode})")
    # Vacío: rechazado.
    error = intento("")
    # Rechazado.
    if error is not None:
        # Por este CHECK.
        comprobar("texto vacío -> rechazado", error.pgcode == "23514" and RESTRICCION in str(error), f"({error.pgcode})")
    # No se rechazó.
    else:
        # FALLO explícito.
        comprobar("texto vacío -> rechazado", False, "(la excepción se ha tragado: se acepta)")
# Siempre: ROLLBACK de todo.
finally:
    # Se deshace todo.
    cn.rollback()

# ----------------------------------------------------------------------
print("\n3. Las filas reales")
# SQL: cuántas visitas superan el máximo (solo lectura).
cur.execute("SELECT count(*) FROM visitas WHERE char_length(texto_cliente) > %s;", (MAXIMO,))
# Ninguna.
comprobar(f"ninguna visita real supera {MAXIMO} caracteres", cur.fetchone()[0] == 0)
# SQL: el cliente de prueba no queda.
cur.execute("SELECT count(*) FROM clientes WHERE email = %s;", (EMAIL,))
# Cero.
comprobar("no queda ningún dato de prueba (ROLLBACK)", cur.fetchone()[0] == 0)
# Se cierra la lectura y la conexión.
cn.rollback()
# Cerrada.
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
