"""
CAMBIO DE DATOS D39.9 - reescribe los 12 textos de tarifas_base.incluye_tipico.

Decisión D39.9 (Gabi, 2026-10-21, aprobada a las 14:04). Es un cambio de
DATOS, no de esquema: solo cambia la columna incluye_tipico. NO cambia
precio_m2, fuente ni fecha_actualizacion (fecha_actualizacion fecha el
PRECIO, no el texto). Los textos viven en UN solo sitio:
docs/datos/D39_9_incluye_tipico.json (SHA-256 aprobado d9d734a0...2d37c2),
con el texto anterior y el nuevo de cada fila.

MODOS (el argumento tiene que ser EXACTO; cualquier otro, código 2):
  (sin argumentos)    ENSAYO: hace todo, incluidos los 12 UPDATE y los 12
                      logs, dentro de una transacción que termina en
                      ROLLBACK. La conexión es ConexionSinCommit: si algo
                      llamara a commit(), saltaría CommitProhibido.
  --confirmo-d39-9    APLICAR: lo mismo, con COMMIT. Solo tras la frase de
                      Gabi "confirmo el cambio de datos D39.9".
  --deshacer-d39-9    DESHACER: la vuelta atrás (del texto nuevo al
                      anterior), con COMMIT y la misma disciplina.

DISCIPLINA:
  - Precondición: las 12 filas de tarifas_base (ni una más ni una menos)
    tienen EXACTAMENTE el texto de partida (el "anterior" al aplicar, el
    "nuevo" al deshacer). Se leen con FOR UPDATE, que las bloquea hasta el
    final de la transacción. Si no se cumple: nada escrito, código 3.
  - Cada UPDATE lleva WHERE tipo_reforma, nivel_acabados E incluye_tipico =
    texto de partida (así nunca pisa un cambio ajeno) y tiene que dar
    rowcount exactamente 1; si alguno no lo da, ROLLBACK de todo y código 1.

REGISTRO EN logs (opción A, decidida por el tutor y Gabi el 2026-10-21):
una fila por fila cambiada, en la MISMA transacción que su UPDATE, con
entity_type 'tarifa_base', entity_id = id real de la fila de tarifas_base,
accion 'incluye_tipico_cambiado' y detalle {"decision": "D39.9",
"sentido": "aplicar"|"deshacer", "anterior": texto antes de ESTA
operación, "nuevo": texto después, "sha256_json": SHA-256 del JSON}.
Las migraciones de scripts/migraciones_ejecutadas/ no escriben en logs;
este cambio sí, para que quede rastro dentro de la propia base de datos.
OJO: el ENSAYO también inserta (y deshace) sus 12 logs; el ROLLBACK borra
las filas pero el contador SERIAL de logs.id no retrocede, así que deja
12 números sin usar. No es un fallo.

Regla mientras tanto (H-D39-4): --confirmo-d39-9 nunca se ejecuta a la vez
que scripts/check_webhook_auth_http.py ni que la suite (ese script cuenta
TODAS las filas de logs antes y después).

Verificación: scripts/check_datos_d39_9_incluye_tipico.py (solo lectura).

Códigos de salida: 0 bien; 1 algún UPDATE no dio rowcount 1 (ROLLBACK);
2 argumento desconocido; 3 precondición no cumplida (nada escrito).
"""

# hashlib: el SHA-256 del JSON, que se guarda en cada log.
import hashlib
# json: leer el archivo de datos y escribir el detalle de cada log.
import json
# os: leer DATABASE_URL del entorno.
import os
# re: buscar dígitos en los textos nuevos.
import re
# sys: argumentos, salida en UTF-8 y código de salida.
import sys
# Path: rutas a partir de la ubicación de este archivo.
from pathlib import Path

# psycopg2: la conexión a PostgreSQL.
import psycopg2
# load_dotenv: carga el .env en el entorno (sin enseñar ningún valor).
from dotenv import load_dotenv

# Salida en UTF-8: si no, en Windows las tildes salen rotas en una tubería.
sys.stdout.reconfigure(encoding="utf-8")

# La raíz del repositorio: este archivo está en scripts/.
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Se carga el .env de la raíz.
load_dotenv(RAIZ_REPO / ".env")
# La URL de la base de datos (nunca se imprime).
DATABASE_URL = os.environ.get("DATABASE_URL")
# El archivo con los 12 textos anterior y nuevo.
RUTA_JSON = RAIZ_REPO / "docs" / "datos" / "D39_9_incluye_tipico.json"

# Los dos únicos argumentos que escriben de verdad.
FLAG_APLICAR = "--confirmo-d39-9"
FLAG_DESHACER = "--deshacer-d39-9"
# Lo que se escribe en cada fila de logs (opción A).
ENTITY_TYPE = "tarifa_base"
ACCION = "incluye_tipico_cambiado"
DECISION = "D39.9"
# Las claves exactas de cada entrada del JSON.
CLAVES = {"tipo_reforma", "nivel_acabados", "incluye_tipico_anterior", "incluye_tipico_nuevo"}
# Caracteres que no pueden ir en un texto nuevo (los usa el email de WF7).
PROHIBIDOS = set("<>&\"'")
# Cuántas filas tiene tarifas_base: 4 tipos x 3 niveles.
NUM_FILAS = 12


class CommitProhibido(Exception):
    """Alguien ha llamado a commit() en la conexión del ENSAYO."""


class ConexionSinCommit(psycopg2.extensions.connection):
    """Una conexión de psycopg2 normal, salvo commit(), que lanza un error.
    rollback() funciona como siempre (es como termina el ENSAYO)."""

    def commit(self):
        # No se confirma NADA: se lanza el error y la transacción sigue
        # abierta, para que el ROLLBACK la deshaga.
        raise CommitProhibido("commit() en el ENSAYO: el ensayo termina siempre en ROLLBACK")


def leer_modo(argumentos):
    """'ensayo', 'aplicar' o 'deshacer'; None si los argumentos no valen."""
    # Sin argumentos: el ensayo, que nunca confirma.
    if argumentos == []:
        return "ensayo"
    # Exactamente el flag de aplicar, y nada más.
    if argumentos == [FLAG_APLICAR]:
        return "aplicar"
    # Exactamente el flag de deshacer, y nada más.
    if argumentos == [FLAG_DESHACER]:
        return "deshacer"
    # Cualquier otra cosa (un flag mal escrito, dos flags...): no vale.
    return None


def cargar_json():
    """Lee el JSON. Devuelve (entradas, sha256, problemas)."""
    # Los bytes tal cual: el SHA-256 se calcula sobre ellos.
    contenido = RUTA_JSON.read_bytes()
    # La huella del archivo, que irá en cada log.
    sha = hashlib.sha256(contenido).hexdigest()
    # Se interpreta como UTF-8 (las tildes).
    entradas = json.loads(contenido.decode("utf-8"))
    # Aquí se van anotando los problemas que se encuentren.
    problemas = []
    # Tiene que ser una lista de 12 entradas.
    if not isinstance(entradas, list) or len(entradas) != NUM_FILAS:
        problemas.append(f"el JSON no es una lista de {NUM_FILAS} entradas")
        # Sin la forma básica no se puede seguir mirando.
        return entradas, sha, problemas
    # Las parejas (tipo, nivel) vistas, para detectar repetidas.
    vistas = set()
    # Se revisa cada entrada.
    for entrada in entradas:
        # Claves exactas y todos los valores de texto.
        if not isinstance(entrada, dict) or set(entrada) != CLAVES or \
                not all(isinstance(v, str) and v for v in entrada.values()):
            problemas.append(f"entrada mal formada: {entrada}")
            # Las demás comprobaciones de esta entrada no tendrían sentido.
            continue
        # La pareja que identifica la fila de tarifas_base.
        pareja = (entrada["tipo_reforma"], entrada["nivel_acabados"])
        # Repetida: dos UPDATE sobre la misma fila.
        if pareja in vistas:
            problemas.append(f"pareja repetida: {pareja}")
        # Se anota como vista.
        vistas.add(pareja)
        # El texto nuevo, que es el que se va a escribir.
        nuevo = entrada["incluye_tipico_nuevo"]
        # Ningún dígito (C4 de check_mcp_business_rules: ningún número suelto).
        if re.search(r"\d", nuevo):
            problemas.append(f"{pareja}: el texto nuevo contiene dígitos")
        # Ningún carácter que haya que escapar en HTML.
        if PROHIBIDOS & set(nuevo):
            problemas.append(f"{pareja}: el texto nuevo contiene alguno de < > & \" '")
        # Un "cambio" que no cambia nada sería un error del JSON.
        if nuevo == entrada["incluye_tipico_anterior"]:
            problemas.append(f"{pareja}: el texto nuevo es igual al anterior")
    # Se devuelve todo.
    return entradas, sha, problemas


def main():
    """Ejecuta el modo pedido y devuelve el código de salida."""
    # El modo sale de los argumentos (sin el nombre del script).
    modo = leer_modo(sys.argv[1:])
    # Argumentos que no valen: se niega sin conectarse siquiera.
    if modo is None:
        print(f"Argumentos no válidos: {sys.argv[1:]}. Solo se admite: nada (ensayo), "
              f"{FLAG_APLICAR} o {FLAG_DESHACER}. No se ha hecho nada.")
        return 2

    # El JSON, con su huella y sus problemas de forma.
    entradas, sha, problemas = cargar_json()
    # Se enseña qué archivo se usa y su huella.
    print(f"Modo: {modo.upper()}")
    print(f"JSON: {RUTA_JSON.relative_to(RAIZ_REPO).as_posix()}  SHA-256 {sha}")
    # Un JSON mal formado no se usa para nada.
    if problemas:
        # Se enseña cada problema.
        for problema in problemas:
            print(f"  [PRECONDICIÓN] {problema}")
        print("No se ha escrito nada (código 3).")
        return 3

    # El sentido del cambio, tal como se guarda en el log.
    sentido = "deshacer" if modo == "deshacer" else "aplicar"
    # Para cada fila: (tipo, nivel, texto de partida, texto de llegada).
    # Al deshacer, se intercambian.
    if sentido == "aplicar":
        cambios = [(e["tipo_reforma"], e["nivel_acabados"], e["incluye_tipico_anterior"],
                    e["incluye_tipico_nuevo"]) for e in entradas]
    else:
        cambios = [(e["tipo_reforma"], e["nivel_acabados"], e["incluye_tipico_nuevo"],
                    e["incluye_tipico_anterior"]) for e in entradas]

    # El ENSAYO usa la conexión que prohíbe commit(); los otros, la normal.
    fabrica = ConexionSinCommit if modo == "ensayo" else psycopg2.extensions.connection
    # Se conecta (psycopg2 abre la transacción con la primera orden).
    cn = psycopg2.connect(DATABASE_URL, connection_factory=fabrica)
    # try / except / finally: ante cualquier error, ROLLBACK; siempre, close.
    try:
        # El cursor de todas las órdenes.
        cur = cn.cursor()
        # SQL: las 12 filas, bloqueadas (FOR UPDATE) hasta el final de la
        # transacción: nadie puede cambiarlas entre la lectura y el UPDATE.
        cur.execute("SELECT id, tipo_reforma, nivel_acabados, incluye_tipico "
                    "FROM tarifas_base ORDER BY tipo_reforma, nivel_acabados FOR UPDATE;")
        # Diccionario (tipo, nivel) -> (id, texto actual).
        filas = {(t, n): (i, texto) for i, t, n, texto in cur.fetchall()}

        # PRECONDICIÓN: las mismas 12 parejas, ni una más ni una menos...
        parejas_json = {(t, n) for t, n, _, _ in cambios}
        if set(filas) != parejas_json:
            problemas.append(f"tarifas_base tiene {len(filas)} filas y no coinciden con las "
                             f"{len(parejas_json)} del JSON: sobran {sorted(set(filas) - parejas_json)}, "
                             f"faltan {sorted(parejas_json - set(filas))}")
        # ...y cada una con EXACTAMENTE el texto de partida.
        for tipo, nivel, desde, _ in cambios:
            # Solo si la fila existe (si no, ya está anotado arriba).
            if (tipo, nivel) in filas:
                # El texto que tiene ahora.
                actual = filas[(tipo, nivel)][1]
                # ¿Es el de partida? Se enseña fila a fila.
                bien = actual == desde
                print(f"  {'[OK]    ' if bien else '[DISTINTO]'} {tipo}/{nivel}: "
                      f"{'texto de partida' if bien else 'texto inesperado: ' + repr(actual)}")
                # Si no lo es, es un problema.
                if not bien:
                    problemas.append(f"{tipo}/{nivel}: no tiene el texto de partida")
        # Algún problema: se suelta el bloqueo sin escribir y se sale.
        if problemas:
            # Se enseña cada problema.
            for problema in problemas:
                print(f"  [PRECONDICIÓN] {problema}")
            # ROLLBACK: suelta el bloqueo de FOR UPDATE; no se escribió nada.
            cn.rollback()
            print("ROLLBACK: no se ha escrito nada (código 3).")
            return 3

        # Los ids de logs insertados, para comprobar después del ensayo.
        ids_logs = []
        # Un UPDATE y un log por fila.
        for tipo, nivel, desde, hacia in cambios:
            # SQL: solo la columna incluye_tipico, y solo si sigue teniendo el
            # texto de partida (segunda barrera, además del FOR UPDATE).
            cur.execute("UPDATE tarifas_base SET incluye_tipico = %s "
                        "WHERE tipo_reforma = %s AND nivel_acabados = %s AND incluye_tipico = %s;",
                        (hacia, tipo, nivel, desde))
            # Tiene que haber cambiado exactamente UNA fila.
            if cur.rowcount != 1:
                # Se deshace TODO lo hecho hasta aquí.
                cn.rollback()
                print(f"  [FALLO] {tipo}/{nivel}: el UPDATE cambió {cur.rowcount} filas, no 1.")
                print("ROLLBACK de todo: no se ha escrito nada (código 1).")
                return 1
            # El detalle del log: qué decisión, en qué sentido, de qué a qué.
            detalle = {"decision": DECISION, "sentido": sentido, "anterior": desde,
                       "nuevo": hacia, "sha256_json": sha}
            # SQL: el log de ESTA fila, en la misma transacción que su UPDATE;
            # RETURNING id devuelve el id que le ha tocado.
            cur.execute("INSERT INTO logs (entity_type, entity_id, accion, detalle) "
                        "VALUES (%s, %s, %s, %s::jsonb) RETURNING id;",
                        (ENTITY_TYPE, filas[(tipo, nivel)][0], ACCION,
                         json.dumps(detalle, ensure_ascii=False)))
            # Se guarda el id del log.
            ids_logs.append(cur.fetchone()[0])
            # Se enseña la fila cambiada.
            print(f"  UPDATE {tipo}/{nivel}: 1 fila; log id {ids_logs[-1]}")
        # Resumen antes de decidir si se confirma.
        print(f"Filas cambiadas: {len(cambios)} de {NUM_FILAS}; logs escritos: {len(ids_logs)}.")

        # ENSAYO: se deshace todo y se comprueba que no queda nada.
        if modo == "ensayo":
            # Se deshace la transacción entera (UPDATE y logs).
            cn.rollback()
            print("ROLLBACK (ensayo): nada confirmado.")
            # SQL: después del ROLLBACK, ¿queda alguno de esos logs?
            cur.execute("SELECT count(*) FROM logs WHERE id = ANY(%s);", (ids_logs,))
            quedan_logs = cur.fetchone()[0]
            # SQL: ¿alguna fila tiene ya el texto de llegada?
            cur.execute("SELECT count(*) FROM tarifas_base WHERE incluye_tipico = ANY(%s);",
                        ([hacia for _, _, _, hacia in cambios],))
            con_llegada = cur.fetchone()[0]
            # Se cierra también esta lectura.
            cn.rollback()
            # Se enseñan las dos cuentas: tienen que ser 0.
            print(f"Tras el ROLLBACK: logs del ensayo que quedan {quedan_logs}; "
                  f"filas con el texto de llegada {con_llegada} (deben ser 0 y 0). "
                  f"El SERIAL de logs.id no retrocede: los ids {ids_logs[0]}-{ids_logs[-1]} quedan sin usar.")
            # Si queda algo, el ensayo NO ha sido un ensayo.
            return 0 if quedan_logs == 0 and con_llegada == 0 else 1

        # APLICAR o DESHACER: se confirma todo a la vez.
        cn.commit()
        print(f"COMMIT ({sentido}): {len(cambios)} filas y {len(ids_logs)} logs confirmados.")
        return 0
    # Cualquier error inesperado: se deshace y se deja ver el error.
    except BaseException:
        # Solo si la conexión sigue abierta.
        if not cn.closed:
            cn.rollback()
        raise
    # Siempre se cierra la conexión.
    finally:
        cn.close()


# Solo al ejecutar el archivo (no al importarlo).
if __name__ == "__main__":
    # El código de salida es lo que devuelve main().
    sys.exit(main())
