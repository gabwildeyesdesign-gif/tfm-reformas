"""Conexión a la base de datos (Supabase/PostgreSQL) mediante un pool."""

from contextlib import contextmanager

from psycopg2.pool import ThreadedConnectionPool

from app.config import DATABASE_URL, DB_POOL_MIN, DB_POOL_MAX

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


@contextmanager
def get_db_connection():
    """
    Context manager que presta una conexión del pool y la devuelve sola.

    Uso previsto:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(...)

    Todo lo que hay antes del yield ocurre al ENTRAR en el "with"; lo
    que hay en el finally ocurre siempre al SALIR del "with", tanto si
    el código de dentro terminó bien como si lanzó una excepción.
    """
    # getconn() saca una conexión ya abierta del pool (o crea una nueva
    # si hace falta y no se ha llegado a maxconn). Es un préstamo: la
    # conexión sigue siendo del pool, nosotros solo la usamos un rato.
    conn = _pool.getconn()
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
        _pool.putconn(conn)
