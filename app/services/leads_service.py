"""
Lógica de negocio para la gestión de leads.

Esta es la ÚNICA capa con lógica de negocio real del proyecto. Por regla
de disciplina arquitectónica, este archivo NO importa fastapi ni fastmcp:
si lo hiciera, dejaría de ser lógica de negocio pura y pasaría a ser un
adaptador disfrazado. Los adaptadores son app/api/ (puerta REST) y
app/mcp_server/server.py (puerta MCP), y los dos llaman a las funciones
de aquí.

Sí importa Pydantic a través de app/schemas/, porque los esquemas son el
contrato de datos del proyecto, no un framework de entrada.
"""

# Json es un adaptador de psycopg2: envuelve un diccionario o una lista
# de Python y le dice a psycopg2 "esto va a una columna JSONB, conviértelo
# a JSON tú". Sin él habría que llamar a json.dumps() a mano y psycopg2
# guardaría el resultado como TEXTO, no como JSONB.
from psycopg2.extras import Json

# psycopg2.errors contiene una clase de excepción por cada código de error
# de Postgres. UniqueViolation es la del código 23505: "se ha intentado
# repetir un valor que una restricción UNIQUE prohíbe". Capturar esa clase
# concreta, y no Exception, garantiza que solo se trata ESE error y que
# cualquier otro fallo sigue subiendo como hasta ahora.
from psycopg2.errors import UniqueViolation

from app.db.connection import get_db_connection, get_transactional_connection
from app.schemas.leads import LeadCreate, LeadCreateResponse

# Canal fijo de N0: el lead llega de un CHAT web (el Agente 1 de n8n
# conversa con el cliente y, al confirmar los datos, llama a POST /leads).
# Antes valía "formulario_web", un nombre que ya no describe el canal real.
# Se escribe como constante y no como texto suelto dentro del SQL para que,
# el día que exista un segundo canal (WhatsApp), el sitio donde cambiarlo
# sea evidente.
#
# OJO: los leads antiguos conservan canal = 'formulario_web' (17 filas el
# 2026-09-24); no se reescriben, porque son la evidencia de cómo entraron.
# La columna leads.canal no tiene CHECK (comprobado en pg_constraint), así
# que el valor nuevo no necesita migración.
CANAL_CHAT_WEB = "chat_web"

# Nombre de la restricción UNIQUE (lead_token) que crea la migración paso8.
# create_lead() lo compara con el de la restricción que ha saltado, para
# distinguir "este lead_token ya se usó" (repetición legítima de n8n) de
# cualquier otra violación de unicidad, que sería un error de verdad.
RESTRICCION_LEAD_TOKEN = "leads_lead_token_key"


def create_lead(data: LeadCreate) -> LeadCreateResponse:
    """
    Alta IDEMPOTENTE de un lead: si lead_token ya se usó, devuelve el lead
    existente (creado=False) en vez de crear otro, sin escribir nada.

    Cómo funciona (opción "UNIQUE + captura", elegida en la Fase 2):
      1. Se intenta el alta completa con _crear_lead_nuevo().
      2. Si lead_token ya existe, el INSERT en leads choca con la UNIQUE
         leads_lead_token_key y Postgres lanza UniqueViolation.
      3. Esa excepción sale del "with" de get_transactional_connection(),
         que hace ROLLBACK de TODA la transacción. Eso incluye el upsert
         del cliente, que ya se había ejecutado antes del lead, así que
         no queda escrito nada de esta llamada.
      4. Se captura aquí, ya fuera de la transacción, y se lee el lead que
         ya existía.

    Por qué resiste dos peticiones SIMULTÁNEAS con el mismo token: la
    segunda no ve la fila de la primera mientras esta no haga commit,
    pero el índice único sí. Postgres deja a la segunda ESPERANDO en su
    INSERT hasta que la primera termina: si la primera hace commit, la
    segunda recibe UniqueViolation; si hace rollback, la segunda inserta
    sin problema. Nunca pueden existir dos leads con el mismo token,
    porque quien lo impide es el índice, no una comprobación en Python.

    Un "SELECT para ver si existe y, si no, INSERT" NO tendría esa
    garantía: las dos peticiones harían el SELECT a la vez, las dos verían
    "no existe" y las dos insertarían.

    Si una repetición trae el mismo token pero DATOS DISTINTOS, se
    devuelve el lead original y los datos nuevos se ignoran: eso es lo
    que significa idempotente.
    """
    try:
        return _crear_lead_nuevo(data)
    except UniqueViolation as error:
        # error.diag.constraint_name es el nombre de la restricción que ha
        # saltado, tal como lo informa Postgres. Si NO es la del lead_token,
        # es otra violación de unicidad que no sabemos tratar: "raise" sin
        # nada detrás vuelve a lanzar la MISMA excepción, sin tocarla, y
        # acaba en el manejador genérico (500), como cualquier error
        # inesperado.
        if error.diag.constraint_name != RESTRICCION_LEAD_TOKEN:
            raise
        return _buscar_lead_por_token(data.lead_token)


def _buscar_lead_por_token(lead_token: str) -> LeadCreateResponse:
    """
    Devuelve el lead que ya existe con ese lead_token, con creado=False.

    El guion bajo inicial del nombre es una convención de Python: indica
    que la función es INTERNA de este módulo. Nadie de fuera debería
    llamarla; la puerta de entrada es create_lead().

    Usa get_db_connection() (solo lectura) porque no escribe nada.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Se devuelve el estado ACTUAL de la oportunidad, que puede haber
        # avanzado desde que se creó (por ejemplo, 'pendiente_aprobacion'
        # si ya se calculó el presupuesto y activó el Gate).
        #
        # ORDER BY o.id LIMIT 1: oportunidades.lead_id NO tiene UNIQUE, así
        # que el esquema permitiría varias oportunidades por lead. Hoy
        # create_lead solo crea una; si algún día hubiera más, se devuelve
        # la primera, que es la que nació con el lead.
        cursor.execute(
            """
            SELECT l.id, l.cliente_id, o.id, o.estado
            FROM leads l
            JOIN oportunidades o ON o.lead_id = l.id
            WHERE l.lead_token = %s
            ORDER BY o.id
            LIMIT 1;
            """,
            (lead_token,),
        )
        fila = cursor.fetchone()
        cursor.close()

    # Solo puede ser None si el lead se borró entre el choque y esta
    # lectura: un caso que no debería darse nunca. Se lanza un error
    # explícito en vez de devolver una respuesta inventada.
    if fila is None:
        raise RuntimeError(
            f"lead_token {lead_token!r} chocó con la UNIQUE pero no se encuentra el lead"
        )

    lead_id, cliente_id, oportunidad_id, estado = fila
    return LeadCreateResponse(
        lead_id=lead_id,
        cliente_id=cliente_id,
        oportunidad_id=oportunidad_id,
        status=estado,
        creado=False,
    )


def _crear_lead_nuevo(data: LeadCreate) -> LeadCreateResponse:
    """
    Da de alta un lead completo: cliente, lead y oportunidad.

    Es la parte de ESCRITURA de create_lead(). Si lead_token ya existe,
    lanza UniqueViolation (con todo deshecho) y create_lead() lo trata.

    Las tres filas se escriben dentro de UNA SOLA transacción. O se
    crean las tres, o no se crea ninguna: no puede quedar un cliente sin
    lead, ni un lead sin oportunidad. Eso lo garantiza
    get_transactional_connection(), que confirma al terminar bien y
    deshace si salta cualquier excepción.

    El orden de los tres INSERT lo impone la base de datos:
    leads.cliente_id apunta a clientes.id, y oportunidades.lead_id apunta
    a leads.id. Insertar en otro orden haría fallar la clave foránea.

    Parámetros:
        data: el cuerpo ya validado de POST /leads. Cuando llega aquí,
              Pydantic ya garantizó que el email tiene forma válida, que
              m2 es mayor que cero y que tipo_reforma y nivel_acabados
              son valores existentes en tarifas_base. Esta función NO
              vuelve a comprobar nada de eso.

    Devuelve:
        LeadCreateResponse con los tres identificadores creados y el
        estado real de la oportunidad.
    """
    with get_transactional_connection() as conn:
        # Un cursor es el objeto con el que se envían consultas y se leen
        # resultados. Se crea uno solo y se reutiliza para los tres
        # INSERT: los tres viajan por la misma conexión y, por tanto,
        # pertenecen a la misma transacción.
        cursor = conn.cursor()

        # ------------------------------------------------------------
        # 2a. CLIENTE: se crea, o se reutiliza si el email ya existe.
        # ------------------------------------------------------------
        # clientes tiene un índice ÚNICO sobre lower(email) (migración
        # paso8; antes era una UNIQUE sobre email a secas). Un INSERT
        # normal fallaría si el cliente ya existe, y ese NO es un caso de
        # error: que alguien pida una segunda reforma es negocio normal.
        #
        # ON CONFLICT ((lower(email))), con DOBLE paréntesis: el exterior
        # es el de la sintaxis de ON CONFLICT y el interior indica que lo
        # de dentro es una EXPRESIÓN y no un nombre de columna. Postgres
        # busca entonces un índice único cuya expresión sea exactamente
        # lower(email), que es clientes_email_lower_key. Si se escribiera
        # ON CONFLICT (email), fallaría: desde paso8 no existe ninguna
        # restricción única sobre la columna email tal cual.
        #
        # El email ya llega en minúsculas (validador de LeadCreate), así
        # que en la práctica lower(email) y email coinciden. El índice es
        # la segunda defensa, para emails que entren por otra vía.
        #
        # DO UPDATE SET email = EXCLUDED.email es una
        # actualización deliberadamente vacía: le asigna al email el
        # valor que ya tenía. Se hace así, y no con DO NOTHING, por un
        # motivo concreto: con DO NOTHING, la cláusula RETURNING no
        # devuelve NINGUNA fila cuando hay conflicto, y necesitamos el id
        # en los dos casos (cliente nuevo o cliente reutilizado) sin
        # tener que hacer una segunda consulta.
        #
        # EXCLUDED es el nombre que Postgres le da a la fila que se
        # intentaba insertar y que provocó el conflicto.
        #
        # DECISIÓN DE NEGOCIO (D2): NO se actualizan nombre ni telefono
        # del cliente existente: un "Gabi" tecleado con prisa no debe
        # pisar un "Gabriela Gómez" ya correcto. Es deliberado, no un
        # descuido.
        #
        # Los datos de contacto de ESTA llamada no se pierden: se guardan
        # en leads.datos_estructurados, bajo la clave "contacto" (ver 2b).
        # Hasta el 2026-09-24 este comentario ya lo afirmaba, pero era
        # FALSO: solo se guardaban los cuatro datos de la reforma, así que
        # con un email repetido el nombre y el teléfono nuevos se perdían.
        cursor.execute(
            """
            INSERT INTO clientes (nombre, email, telefono, created_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT ((lower(email))) DO UPDATE SET email = EXCLUDED.email
            RETURNING id;
            """,
            (data.nombre, data.email, data.telefono),
        )
        # fetchone() devuelve la fila como una tupla, por ejemplo (7,).
        # El [0] saca el primer (y único) valor de esa tupla.
        cliente_id = cursor.fetchone()[0]

        # ------------------------------------------------------------
        # 2b. LEAD: la evidencia cruda de lo que llegó en la petición.
        # ------------------------------------------------------------
        # fotos_urls: las rutas tal cual, como lista JSON. list(...) crea
        # una lista normal a partir de la de Pydantic; si no había fotos
        # será una lista vacía [], que es un valor JSON perfectamente
        # válido y distinto de NULL ("no mandó fotos" en vez de "no
        # sabemos").
        #
        # datos_estructurados: los cuatro datos de la reforma tal como
        # llegaron, más el contacto de ESTA llamada. Es la evidencia
        # inmutable de la petición original.
        #
        # "contacto" guarda nombre, email y telefono de esta llamada
        # aunque el cliente ya existiera (D2: la ficha de clientes no se
        # toca). Así, si alguien repite email con otro teléfono, el número
        # nuevo queda en SU lead y un técnico puede verlo. Los valores son
        # los que ya validó Pydantic: el teléfono va normalizado
        # ("+34666777444") y el email con el dominio en minúsculas, que es
        # lo que hace EmailStr.
        #
        # Se usa .value en los dos enums para guardar el texto plano
        # ("bano", "medio") y no el objeto de Python. Aunque TipoReforma
        # hereda de str y probablemente funcionaría igual, escribirlo
        # explícito elimina la duda al leerlo.
        #
        # SÍ, tipo_reforma queda duplicado: aquí dentro del JSON y
        # también en la columna oportunidades.tipo_reforma de más abajo.
        # Es INTENCIONAL y está documentado (decisión D3): el JSON es
        # "lo que llegó" y la columna es "el dato de negocio consultable
        # con SQL normal".
        #
        # mensaje_original se deja en NULL a propósito. El canal es un
        # chat, pero la conversación vive en la memoria de n8n, no en el
        # backend: POST /leads solo recibe los datos ya confirmados, no el
        # texto de la conversación, así que aquí no hay nada que guardar.
        cursor.execute(
            """
            INSERT INTO leads (
                cliente_id, canal, mensaje_original, lead_token,
                fotos_urls, datos_estructurados, created_at
            )
            VALUES (%s, %s, NULL, %s, %s, %s, now())
            RETURNING id;
            """,
            (
                cliente_id,
                CANAL_CHAT_WEB,
                # lead_token: clave de idempotencia. Si ya existe, ESTE
                # INSERT es el que lanza UniqueViolation (restricción
                # leads_lead_token_key) y todo se deshace; lo trata
                # create_lead(). Es un INSERT normal, sin ON CONFLICT, a
                # propósito: se QUIERE que falle, para que el rollback
                # arrastre también el upsert del cliente de arriba.
                data.lead_token,
                Json(list(data.fotos)),
                Json(
                    {
                        "tipo_reforma": data.tipo_reforma.value,
                        "nivel_acabados": data.nivel_acabados.value,
                        # float(...) porque el conversor JSON estándar de
                        # Python no sabe escribir un Decimal: Json() lanzaría
                        # "Object of type Decimal is not JSON serializable" y
                        # el alta fallaría entera.
                        #
                        # Convertir aquí no perjudica la exactitud donde
                        # importa: el número vuelve al JSON con su misma
                        # representación decimal (8.7 -> 8.7), y el cálculo
                        # del presupuesto NO lee este diccionario;
                        # estimate_service extrae el dato con
                        # (datos_estructurados ->> 'm2')::numeric, que lo
                        # convierte de texto a NUMERIC dentro de Postgres sin
                        # pasar por float.
                        #
                        # Se guarda como número JSON, y no como texto, para no
                        # cambiar el formato de la evidencia ya escrita en los
                        # leads anteriores.
                        "m2": float(data.m2),
                        "incluye_cambios_estructurales": data.incluye_cambios_estructurales,
                        # Un diccionario dentro de otro: en el JSONB queda
                        # como un objeto anidado y se consulta con
                        # datos_estructurados -> 'contacto' ->> 'telefono'.
                        "contacto": {
                            "nombre": data.nombre,
                            "email": data.email,
                            "telefono": data.telefono,
                        },
                    }
                ),
            ),
        )
        lead_id = cursor.fetchone()[0]

        # ------------------------------------------------------------
        # 2c. OPORTUNIDAD: la entidad viva del flujo comercial.
        # ------------------------------------------------------------
        # Se crea aquí, en la misma operación que el lead (decisión D1).
        # Motivo: presupuestos.oportunidad_id y visitas.oportunidad_id
        # son NOT NULL y apuntan a esta tabla, pero ningún otro endpoint
        # del contrato la creaba. Creándola en la entrada, ningún lead
        # queda huérfano del resto de la cadena.
        #
        # Valores y por qué:
        # - tipo_reforma: la columna estructurada que consultará
        #   calculate-estimate. Desde el Paso 1 tiene un CHECK que
        #   rechaza cualquier valor fuera de los cuatro válidos.
        # - prioridad y confianza_ia: NULL. Son campos que rellenará el
        #   agente de IA más adelante, no este endpoint. NULL significa
        #   aquí "todavía no evaluado", que es la verdad.
        # - datos_completos: false EXPLÍCITO, no confiando en el valor
        #   por defecto de la tabla. Significa "todavía no evaluado por
        #   el agente"; será el agente quien lo ponga a true tras
        #   procesar el lead. Escribirlo a mano deja la intención visible
        #   en el código en vez de esconderla en el esquema.
        # - estado: 'nueva' EXPLÍCITO, por el mismo motivo.
        #
        # RETURNING id, estado devuelve también el estado REALMENTE
        # escrito. Así el status de la respuesta no es una etiqueta que
        # escribimos aparte en Python y que podría desincronizarse del
        # dato: es el valor que tiene la fila en la base de datos.
        cursor.execute(
            """
            INSERT INTO oportunidades (
                lead_id, tipo_reforma, prioridad, confianza_ia,
                datos_completos, estado, created_at, updated_at
            )
            VALUES (%s, %s, NULL, NULL, false, 'nueva', now(), now())
            RETURNING id, estado;
            """,
            (lead_id, data.tipo_reforma.value),
        )
        # Aquí la tupla tiene dos valores, así que se desempaquetan los
        # dos a la vez en dos variables.
        oportunidad_id, estado = cursor.fetchone()

        # Cerrar el cursor libera sus recursos. No confirma nada: el
        # commit lo hace get_transactional_connection() al salir del
        # "with" sin excepciones.
        cursor.close()

    # Al llegar a esta línea ya se ha salido del "with", así que el
    # commit se ha ejecutado y las tres filas están guardadas de verdad.
    return LeadCreateResponse(
        lead_id=lead_id,
        cliente_id=cliente_id,
        oportunidad_id=oportunidad_id,
        status=estado,
        # Esta llamada ha creado el lead: es la única ruta que llega aquí.
        creado=True,
    )
