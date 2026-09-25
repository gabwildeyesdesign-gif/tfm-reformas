"""Conexión a la base de datos (Supabase/PostgreSQL) mediante un pool."""

import sys
import time
from contextlib import contextmanager

import psycopg2
from psycopg2.extensions import connection as ConexionPsycopg2
from psycopg2.pool import ThreadedConnectionPool

from app.config import (
    DATABASE_URL,
    DB_CONNECT_TIMEOUT_S,
    DB_KEEPALIVES,
    DB_KEEPALIVES_COUNT,
    DB_KEEPALIVES_IDLE_S,
    DB_KEEPALIVES_INTERVAL_S,
    DB_POOL_MAX,
    DB_POOL_MIN,
    DB_STATEMENT_TIMEOUT_MS,
)

# Etiqueta que cada conexión del pool envía a Postgres al conectarse.
# Postgres la muestra en la columna application_name de pg_stat_activity.
# OJO: al conectar a través del Session pooler de Supabase esta etiqueta
# NO llega a Postgres (ver el comentario ampliado en init_pool()).
# Se escribe en MAYÚSCULAS por convención de Python: indica que es una
# constante, un valor que no debe cambiar mientras el programa corre.
APPLICATION_NAME = "reformas-backend-fastapi"

# Variable a nivel de módulo donde guardaremos el pool una vez creado.
# Empieza en None porque hasta que no se llame a init_pool() (en el
# arranque del servidor) no existe ningún pool todavía.
_pool: ThreadedConnectionPool | None = None


class ConexionConTimeout(ConexionPsycopg2):
    """
    Conexión de psycopg2 que, nada más abrirse, fija su statement_timeout.

    Por qué hace falta una clase propia: lo natural sería pasar
    options="-c statement_timeout=..." al conectar, pero el Session pooler
    de Supabase (Supavisor) descarta ese parámetro (comprobado: seguía
    valiendo 2 min, el valor del rol). Lo que sí funciona es enviar un SET
    después de conectar. psycopg2.connect() admite el argumento
    connection_factory (API pública): la clase con la que construir la
    conexión. init_pool() le pasa esta, así que TODAS las conexiones del
    pool, las del arranque y las que se abran después, pasan por aquí.

    "class ConexionConTimeout(ConexionPsycopg2)" significa que esta clase
    HEREDA de la conexión normal de psycopg2: se comporta exactamente igual
    que ella y solo añade lo que se escribe aquí.
    """

    def __init__(self, *args, **kwargs):
        # __init__ se ejecuta al crear el objeto. super().__init__(...)
        # ejecuta primero el __init__ de la clase madre, que es el que
        # abre de verdad la conexión con Postgres. *args y **kwargs
        # recogen todos los argumentos recibidos (dsn, keepalives...) y se
        # los pasan tal cual, sin tener que enumerarlos.
        super().__init__(*args, **kwargs)

        # Momento en que se abrió esta conexión, con un reloj monotónico
        # (time.monotonic): nunca retrocede aunque se cambie la hora del
        # sistema, que es lo que se quiere para medir intervalos.
        # _obtener_conexion_validada() lo usa para distinguir una conexión
        # recién abierta de una que llevaba tiempo ociosa en el pool.
        self.creada_en = time.monotonic()

        # SET sin LOCAL cambia el valor para TODA la sesión, no solo para
        # la transacción actual. %s lo rellena psycopg2 con el número de
        # forma segura; sin unidad, Postgres lo interpreta en milisegundos.
        with self.cursor() as cursor:
            cursor.execute("SET statement_timeout = %s", (DB_STATEMENT_TIMEOUT_MS,))
        # psycopg2 abre una transacción implícita incluso para un SET. Si
        # después alguien hiciera rollback() SIN haber confirmado antes,
        # Postgres desharía también el SET y la conexión volvería a los
        # 2 min. El commit() lo hace definitivo para toda la sesión.
        self.commit()


def init_pool():
    """
    Crea el pool de conexiones a Postgres y lo guarda en _pool.

    Se debe llamar UNA sola vez, al arrancar el servidor (desde el
    lifespan de app/main.py). Usamos ThreadedConnectionPool en vez de
    SimpleConnectionPool porque FastAPI ejecuta los endpoints sincronos
    (def, no async def) en un pool de hilos: pueden llegar varias
    peticiones a la vez pidiendo/devolviendo conexiones desde hilos
    distintos, y ThreadedConnectionPool es la variante de psycopg2
    preparada para eso (protege su estado interno con un lock).
    """
    global _pool  # Necesario para reasignar la variable definida arriba,
    # no una copia local: sin "global", Python crearía una variable nueva
    # solo visible dentro de esta función y _pool seguiría siendo None
    # fuera de ella.
    _pool = ThreadedConnectionPool(
        minconn=DB_POOL_MIN,  # conexiones abiertas desde el arranque
        maxconn=DB_POOL_MAX,  # tope de conexiones simultáneas permitidas
        dsn=DATABASE_URL,  # "dsn" = data source name, la cadena de conexión
        # Cualquier argumento extra que no sea minconn/maxconn/dsn,
        # ThreadedConnectionPool se lo pasa tal cual a psycopg2.connect()
        # cada vez que abre una conexión nueva. psycopg2 lo combina con
        # los datos del dsn, así que todas las conexiones del pool (las 2
        # iniciales y las que se creen después) llevarán este nombre.
        #
        # LIMITACIÓN COMPROBADA (no es un fallo de este código):
        # nuestro DATABASE_URL apunta al Session pooler de Supabase, cuyo
        # software se llama Supavisor. Supavisor abre sus propias
        # conexiones contra Postgres y las etiqueta con su propio
        # application_name ('Supavisor'), descartando el que enviamos
        # nosotros. Resultado: consultar
        #     SELECT ... FROM pg_stat_activity
        #     WHERE application_name = 'reformas-backend-fastapi'
        # devuelve SIEMPRE 0 filas mientras se pase por el pooler, por lo
        # que este valor no sirve para identificar nuestras conexiones ahí.
        # Se verificó lanzando un script de diagnóstico con un nombre
        # propio distinto: tampoco aparecía, salía también como
        # 'Supavisor'.
        # Se mantiene porque no cuesta nada y sí sería visible el día que
        # se conecte directamente a Postgres (sin pooler). Para comprobar
        # el tamaño real del pool, usar scripts/check_graceful_shutdown.py,
        # que lo inspecciona desde el propio objeto pool.
        application_name=APPLICATION_NAME,
        # Tiempos límite y keepalives (valores y motivo de cada uno en
        # app/config.py). Son parámetros de libpq, la biblioteca en C que
        # psycopg2 usa por debajo: actúan en el propio socket TCP, así que
        # Supavisor no puede descartarlos como hace con options.
        connect_timeout=DB_CONNECT_TIMEOUT_S,
        keepalives=DB_KEEPALIVES,
        keepalives_idle=DB_KEEPALIVES_IDLE_S,
        keepalives_interval=DB_KEEPALIVES_INTERVAL_S,
        keepalives_count=DB_KEEPALIVES_COUNT,  # sin efecto en Windows
        # statement_timeout NO va aquí como options: lo fija la propia
        # clase de conexión con un SET (ver ConexionConTimeout).
        connection_factory=ConexionConTimeout,
    )


def close_pool():
    """
    Cierra ordenadamente todas las conexiones del pool.

    Se debe llamar al apagar el servidor (desde el lifespan), después
    del yield, para no dejar conexiones abiertas colgando en Supabase
    cuando el proceso termina.
    """
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


def _obtener_conexion_validada():
    """
    Saca una conexión del pool y comprueba que está viva ANTES de
    entregarla. Si está muerta, la descarta y pide otra.

    El problema: una conexión que llevaba minutos ociosa en el pool puede
    haber muerto (corte de red o del pooler) sin que psycopg2 lo sepa
    (conn.closed sigue valiendo 0). El fallo aparecía en la primera
    consulta de negocio, en mitad de POST /leads.

    La solución: una consulta mínima (SELECT 1) antes de entregarla. Si
    falla con OperationalError o InterfaceError, la conexión se cierra y
    se sustituye. NO se reintenta ninguna operación de negocio: esto
    ocurre antes de que el llamador haya ejecutado nada.

    Por qué se sigue buscando tras el primer reemplazo: un corte de red
    no mata una conexión, mata TODAS las que están ociosas a la vez, y
    getconn() entrega otra ociosa antes de abrir una nueva (código fuente
    de psycopg2, pool.py, _getconn). Con un único reemplazo, el segundo
    intento recibiría casi seguro otra conexión muerta.

    Cuándo se rinde:
      - Si falla una conexión RECIÉN ABIERTA en este mismo préstamo: la
        conexión estaba fresca, así que el problema no es una conexión
        rancia sino la base de datos o la red. Seguir no arreglaría nada.
      - Si getconn() no consigue ni abrir una conexión (base de datos
        caída, connect_timeout agotado): su excepción sube tal cual.
      - Tras DB_POOL_MAX intentos, como tope de seguridad. En la práctica
        nunca se alcanza: el pool guarda como mucho DB_POOL_MIN
        conexiones ociosas (putconn cierra el resto), así que tras
        descartarlas todas la siguiente es una conexión nueva.
    En los tres casos se lanza el error ORIGINAL: el de la primera
    conexión muerta, que es el que explica qué pasó.

    COSTE MEDIDO (2026-09-25, desde esta máquina a eu-central-1): unos
    120 ms por préstamo, la ida y vuelta del SELECT 1 (~60 ms) más la del
    rollback (~60 ms). Depende de la red: ese mismo día, en otra medición,
    fueron ~190 ms. Se paga en cada petición, con la conexión viva o
    muerta. Se acepta en N0: es pequeño frente a la latencia de n8n.

    LÍMITE CONOCIDO: la conexión medio abierta en Windows. Si el otro
    extremo desaparece sin avisar (no llega el cierre de TCP), el propio
    SELECT 1 puede quedarse esperando lo que tarde el sistema operativo en
    rendirse reenviando paquetes: minutos. statement_timeout no lo evita
    (lo aplica el servidor, que ya no está), y el único parámetro de libpq
    que lo acota, tcp_user_timeout, no existe en Windows. Lo que reduce
    este caso son los keepalives: detectan la conexión muerta mientras
    está ociosa (unos 130 s en Windows), antes de que se preste.
    """
    # Primer error encontrado. Empieza en None ("todavía ninguno").
    error_original = None

    for intento in range(1, DB_POOL_MAX + 1):
        # Momento en que empieza este intento. Si la conexión que entrega
        # getconn() se creó DESPUÉS de este instante, es que getconn()
        # tuvo que abrirla ahora: es recién abierta. Si se creó antes,
        # venía de la lista de ociosas del pool.
        inicio = time.monotonic()
        conn = _pool.getconn()
        recien_abierta = conn.creada_en >= inicio

        try:
            # Si psycopg2 ya sabe que está cerrada (closed distinto de 0),
            # cursor() lanza InterfaceError sin tocar la red. Si no lo
            # sabe, es el SELECT 1 el que falla con OperationalError al
            # encontrarse el socket cerrado.
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
            # El SELECT 1 abrió una transacción implícita; se cierra para
            # entregar la conexión limpia, como si nadie la hubiera usado.
            conn.rollback()
            return conn
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as error:
            # Solo se guarda el PRIMER error; los siguientes no lo pisan.
            if error_original is None:
                error_original = error

            # Aviso en consola (stderr), NO en la tabla logs: escribir en
            # logs necesita otra conexión, justo lo que está fallando.
            # Mismo formato que el aviso de app/main.py.
            print(
                f"[AVISO] Conexión del pool descartada (intento {intento}, "
                f"{'recién abierta' if recien_abierta else 'estaba ociosa'}): "
                f"{type(error).__name__}: {str(error).strip()}",
                file=sys.stderr,
                flush=True,
            )

            # close=True: el pool la CIERRA y la olvida en vez de
            # guardarla de nuevo entre las ociosas.
            _pool.putconn(conn, close=True)

            if recien_abierta:
                raise error_original

    # Solo se llega aquí si se agotaron los intentos sin encontrar una
    # conexión viva (ver "Cuándo se rinde" en el docstring).
    raise error_original


@contextmanager
def get_db_connection():
    """
    Context manager de SOLO LECTURA: presta una conexión del pool, la
    devuelve sola, y NO confirma nada.

    Uso previsto (consultas que no modifican datos):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT ...")

    ATENCIÓN: esta función NO hace commit(). Cualquier INSERT, UPDATE o
    DELETE hecho a través de ella se DESHACE al salir del "with" y no
    queda guardado en la base de datos. Para escrituras hay que usar
    get_transactional_connection(), definida más abajo.

    Todo lo que hay antes del yield ocurre al ENTRAR en el "with"; lo
    que hay en el finally ocurre siempre al SALIR del "with", tanto si
    el código de dentro terminó bien como si lanzó una excepción.
    """
    # getconn() saca una conexión ya abierta del pool (o crea una nueva
    # si hace falta y no se ha llegado a maxconn). Es un préstamo: la
    # conexión sigue siendo del pool, nosotros solo la usamos un rato.
    # No se llama a getconn() directamente sino a través de
    # _obtener_conexion_validada(), que comprueba que la conexión está
    # viva antes de entregarla (ver su docstring). Va FUERA del try a
    # propósito: si falla, esa función ya ha devuelto al pool (cerradas)
    # las conexiones muertas, y aquí no hay ninguna que devolver.
    conn = _obtener_conexion_validada()
    try:
        # yield entrega la conexión al bloque "with" que llamó a esta
        # función. La ejecución de get_db_connection() se queda aquí
        # "congelada" mientras el código de dentro del "with" corre.
        yield conn
    finally:
        # Este bloque se ejecuta SIEMPRE al salir del "with", incluso si
        # dentro del "with" una consulta lanzó una excepción. Sin este
        # finally, una consulta que fallara a mitad (por ejemplo, un
        # error de sintaxis SQL) haría que el "return" o la excepción
        # saltaran directamente fuera de esta función sin pasar por
        # putconn(): la conexión se quedaría marcada como "en uso" en
        # el pool para siempre, aunque en realidad nadie la esté usando
        # ya. Repetido varias veces, el pool acabaría agotando sus
        # conexiones (llegando a DB_POOL_MAX) y las siguientes peticiones
        # se quedarían esperando indefinidamente una conexión libre que
        # nunca llega — un servidor "atascado" sin ningún error visible.

        # rollback() incluso para lecturas: en psycopg2 un simple SELECT
        # también abre una transacción implícita, y una conexión devuelta
        # sin cerrarla se queda en el estado "idle in transaction" —
        # inactiva pero reteniendo recursos del servidor (bloqueos y
        # visibilidad de versiones antiguas de las filas, lo que impide
        # al recolector de basura de Postgres limpiar). Cerrarla aquí es
        # gratis y deja la conexión en estado IDLE limpio.
        #
        # conn.closed vale 0 mientras la conexión está viva; si se
        # perdió, intentar hacerle rollback lanzaría otra excepción
        # ENCIMA de la que ya estuviera propagándose, tapando el error
        # original. Por eso se comprueba antes.
        if not conn.closed:
            conn.rollback()
        _pool.putconn(conn)


@contextmanager
def get_transactional_connection():
    """
    Context manager de ESCRITURA: presta una conexión del pool y
    confirma o deshace la transacción automáticamente.

    Uso previsto (cuando se modifican datos):
        with get_transactional_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO clientes ...")
            cursor.execute("INSERT INTO leads ...")
        # al llegar aquí sin excepciones, las DOS filas están guardadas

    Qué garantiza:
      - Si el bloque "with" termina sin excepción -> commit(): los
        cambios quedan guardados de verdad.
      - Si dentro del "with" salta CUALQUIER excepción -> rollback():
        no queda ni una fila a medias, y la excepción sigue subiendo
        hacia quien llamó (para que la capa de API pueda convertirla en
        una respuesta HTTP de error).

    Esto es lo que se llama ATOMICIDAD: varias operaciones se comportan
    como una sola indivisible — o se aplican todas, o ninguna. Es
    imprescindible en POST /leads, donde se escriben tres filas
    encadenadas (cliente, lead y oportunidad): un fallo a mitad no puede
    dejar un cliente sin lead ni un lead sin oportunidad.

    Se llama get_transactional_connection() y no algo parecido a
    get_db_connection() A PROPÓSITO: los dos nombres tienen que ser
    difíciles de confundir de un vistazo, porque usar la de lectura para
    escribir no da ningún error — simplemente los datos no se guardan.
    """
    # Misma validación previa que en get_db_connection(), y también fuera
    # del try: si no hay conexión viva, no hay nada que confirmar,
    # deshacer ni devolver.
    conn = _obtener_conexion_validada()
    try:
        # Se entrega la conexión al bloque "with". Mientras ese bloque
        # se ejecuta, esta función está detenida justo en esta línea.
        yield conn

        # Solo se llega aquí si el bloque "with" terminó SIN excepción.
        # commit() le dice a Postgres "haz definitivos todos los cambios
        # de esta transacción". Sin esta línea se perderían: psycopg2 no
        # confirma solo (no trabaja en modo autocommit).
        conn.commit()
    except Exception:
        # "except Exception" captura cualquier error ocurrido dentro del
        # "with": un fallo de SQL, una violación de clave única, o
        # incluso un error de Python del código de negocio a mitad de la
        # escritura. rollback() deshace TODO lo hecho en esta
        # transacción, dejando la base de datos como estaba al empezar.
        #
        # conn.closed vale 0 mientras la conexión está viva; cualquier
        # otro valor significa que psycopg2 ya la dio por cerrada. Si se
        # perdió y aun así se le pidiera rollback(), psycopg2 lanzaría un
        # InterfaceError ENCIMA de la excepción que ya venía subiendo, y
        # esa sería la que vería quien llamó: el error original quedaría
        # tapado (incidente real, documentado en D16). Por eso se
        # comprueba antes. Es exactamente la misma guarda que ya tenía
        # get_db_connection(); esta función era la única de las dos sin
        # ella, y esa asimetría es la que provocó el incidente.
        #
        # LÍMITE CONOCIDO: la guarda solo cubre el caso en que psycopg2
        # YA sabe que la conexión murió. Si murió y todavía no se ha
        # detectado, closed sigue valiendo 0, se entra en el if y
        # rollback() puede lanzar OperationalError, tapando otra vez el
        # error original. Queda abierto A PROPÓSITO (pendiente de backend
        # en D16): cerrarlo no es envolver esta línea en un try, sino
        # decidir antes qué hacer con esa excepción secundaria.
        if not conn.closed:
            conn.rollback()

        # "raise" a secas vuelve a lanzar la MISMA excepción que se
        # acaba de capturar, con su traza original intacta. Sin esta
        # línea, el error se quedaría aquí tragado en silencio y quien
        # llamó creería que todo fue bien.
        raise
    finally:
        # Pase lo que pase (commit correcto, rollback, o un error dentro
        # del propio rollback), la conexión vuelve al pool. Si no, se
        # quedaría marcada como "en uso" para siempre.
        _pool.putconn(conn)
