"""
Verificación de la migración paso14b (plan docs/Plan_Horario_Visitas_D32.txt,
secciones 2.6 y 13): la fila paso_huecos_min ya no existe en reglas_negocio, y
las reglas que D32 sí lee siguen ahí.

Qué comprueba (solo LEE; la sesión es de solo lectura):
  1. paso_huecos_min NO existe.
  2. desplazamiento_min y dias_oferta_visita (paso14) SÍ existen: borrar la
     fila equivocada sería peor que no borrar ninguna.
  3. leer_reglas_visita (el código que leen POST /visits, POST
     /resultados-llamada, el alias, GET /huecos-libres y las dos tools) lee
     las reglas reales sin ningún problema.

Ejecutado ANTES de la migración tiene que dar FALLO (la fila existe): es la
prueba en negativo de este script.

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

# La cadena de conexión del .env (nunca se imprime).
from app.config import DATABASE_URL
# La lectura de las reglas de visita, la de los endpoints.
from app.services.reglas_visita import leer_reglas_visita

# Contadores.
ok, fallos = 0, []


def comprobar(titulo, condicion, detalle=""):
    """[OK] o [FALLO], y se cuenta."""
    # global: el contador de fuera.
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
        # Se enseña.
        print(f"  [FALLO] {titulo}  {detalle}")


# Conexión de solo lectura.
cn = psycopg2.connect(DATABASE_URL)
# Postgres rechazaría cualquier escritura.
cn.set_session(readonly=True)
# El cursor.
cur = cn.cursor()

# SQL: las tres claves que importan, con su valor.
cur.execute("SELECT clave, valor FROM reglas_negocio WHERE clave = ANY(%s);",
            (["paso_huecos_min", "desplazamiento_min", "dias_oferta_visita"],))
# clave -> valor.
filas = dict(cur.fetchall())
# [INFO]: lo que hay.
print(f"  [INFO]  {filas}")
# 1. La fila borrada.
comprobar("paso_huecos_min ya no existe", "paso_huecos_min" not in filas, f"({filas.get('paso_huecos_min')})")
# 2. Las de D32, presentes.
comprobar("desplazamiento_min y dias_oferta_visita siguen existiendo",
          "desplazamiento_min" in filas and "dias_oferta_visita" in filas, f"({sorted(filas)})")
# 3. La lectura del código.
_, problemas = leer_reglas_visita(cur)
# Sin problemas.
comprobar("leer_reglas_visita lee las reglas reales sin problemas", problemas == [], f"({problemas})")
# Se cierra la lectura.
cn.rollback()
# Y la conexión.
cn.close()

# Resumen.
print("\n" + "=" * 78)
# El recuento.
print(f"RESULTADO: {ok}/{ok + len(fallos)} correctas")
# Separador.
print("=" * 78)
# Con fallos, código 1.
if fallos:
    # Cabecera.
    print("FALLOS:")
    # Uno por línea.
    for f in fallos:
        # Con su detalle.
        print(f"  - {f}")
    # Código 1.
    sys.exit(1)
