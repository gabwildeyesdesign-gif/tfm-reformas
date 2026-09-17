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
    conn = _pool.getconn()
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
        # Nota: si la conexión se perdiera del todo, este rollback podría
        # fallar; aun así el putconn() del finally se ejecuta igualmente,
        # y se comprobó empíricamente que el pool descarta o limpia las
        # conexiones rotas antes de reutilizarlas.
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
