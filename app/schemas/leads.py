"""
Modelos Pydantic del dominio de leads.

Un "modelo" aquí es una clase que describe la FORMA que debe tener un
dato: qué campos lleva, de qué tipo es cada uno y qué límites tiene.
Pydantic usa esa descripción para validar y convertir automáticamente lo
que llega de fuera (el JSON del webhook de n8n), y FastAPI la usa además
para documentar el endpoint solo en /docs.

Este archivo NO contiene lógica de negocio: no habla con la base de
datos, no calcula nada y no comprueba reglas que dependan del estado del
sistema. Solo describe formas. La validación que sí depende del negocio
(por ejemplo, que las rutas de las fotos empiecen por
'leads-temp/{lead_token}/') vive en app/services/, no aquí.
"""

# BaseModel: la clase de la que heredan todos los modelos de Pydantic.
#   Heredar de ella es lo que activa toda la maquinaria de validación.
# ConfigDict: sirve para configurar el comportamiento de un modelo
#   concreto (en nuestro caso, qué hacer con los campos desconocidos).
# EmailStr: un tipo especial de Pydantic que, además de exigir texto,
#   comprueba que ese texto tiene forma de email de verdad.
# Field: se usa cuando un campo necesita algo más que su tipo — un valor
#   por defecto, un límite, o una descripción.
from pydantic import BaseModel, ConfigDict, EmailStr, Field

# Import completo (app.schemas.common) y no relativo (.common): así, al
# leer cualquier línea de abajo, se ve exactamente de qué archivo del
# proyecto sale cada nombre, sin tener que deducirlo.
from app.schemas.common import MAX_FOTOS_LEAD, NivelAcabados, TipoReforma


class PhotoUploadSlot(BaseModel):
    """
    Un "hueco" para subir una foto.

    El backend no recibe la foto en sí: le entrega al cliente una URL
    firmada y temporal, y el cliente sube el archivo directamente al
    almacenamiento. Este modelo describe uno de esos permisos de subida.
    """

    # "path: str" significa: campo llamado path, de tipo texto, y
    # OBLIGATORIO. En Pydantic un campo es obligatorio simplemente por no
    # tener valor por defecto; no hace falta marcarlo de ninguna forma.
    # Es la ruta donde quedará guardado el archivo dentro del bucket.
    path: str

    # La URL firmada a la que el cliente hará la subida. Firmada = lleva
    # incrustada una autorización con caducidad, por eso es temporal.
    signed_upload_url: str

    # Segundos que esa URL sigue siendo válida.
    # Field(gt=0) añade una restricción: gt es "greater than" (mayor
    # que). Una caducidad de 0 o negativa sería una URL nacida muerta,
    # así que se rechaza aquí y no más adelante.
    expires_in: int = Field(gt=0, description="Segundos de validez de la URL firmada")


class LeadPhotoUploadResponse(BaseModel):
    """
    Respuesta que recibe el cliente ANTES de enviar el formulario:
    un identificador temporal del lead y la lista de huecos de subida.
    """

    # Identificador temporal que agrupa las fotos de un mismo lead antes
    # de que el lead exista en la base de datos. Después viaja dentro de
    # LeadCreate para poder emparejar formulario y fotos.
    lead_token: str

    # "list[PhotoUploadSlot]" = una lista cuyos elementos son, cada uno,
    # un PhotoUploadSlot completo y válido. Pydantic valida también DENTRO
    # de la lista: si un solo hueco trae expires_in = 0, falla entero.
    #
    # Field lleva aquí dos cosas:
    # - default_factory=list -> valor por defecto: una lista vacía. Se
    #   escribe así, y NO como "= []", por un motivo real de Python: un
    #   valor por defecto se crea UNA sola vez al definir la clase, de
    #   modo que todas las respuestas compartirían la MISMA lista y lo
    #   que una añadiera lo verían las demás. default_factory le dice a
    #   Pydantic "llama a list() para fabricar una lista nueva cada vez".
    # - max_length=MAX_FOTOS_LEAD -> en Pydantic v2, aplicado a una
    #   lista, max_length limita el NÚMERO DE ELEMENTOS (en Pydantic v1
    #   esto se llamaba max_items; si ves max_items en un tutorial, es
    #   código de la versión antigua).
    photo_slots: list[PhotoUploadSlot] = Field(
        default_factory=list,
        max_length=MAX_FOTOS_LEAD,
    )


class LeadCreate(BaseModel):
    """
    Cuerpo de la petición POST /leads: lo que envía el formulario web a
    través del webhook de n8n.

    Es el único modelo de este archivo que describe datos que vienen de
    FUERA, así que es el único donde la validación protege de verdad.
    """

    # model_config configura ESTE modelo. extra="forbid" cambia el
    # comportamiento por defecto de Pydantic, que es IGNORAR EN SILENCIO
    # cualquier campo que no conozca. Con "forbid", un campo de más
    # provoca un error de validación que nombra al campo sobrante.
    # Motivo: si el workflow de n8n envía algún día "telefono_movil" en
    # vez de "telefono", sin esto el dato se perdería sin que nadie se
    # entere; con esto, la petición falla con un 422 explícito.
    model_config = ConfigDict(extra="forbid")

    # Los límites de longitud no son arbitrarios: replican el ancho real
    # de las columnas de Supabase (clientes.nombre es VARCHAR(150),
    # clientes.email VARCHAR(150), clientes.telefono VARCHAR(30)).
    # Validarlos aquí convierte lo que sería un error 500 de Postgres
    # ("value too long for type character varying") en un 422 limpio que
    # explica qué campo se pasó de largo. min_length=1 evita además que
    # se cuele una cadena vacía, que Postgres sí aceptaría como válida.
    nombre: str = Field(min_length=1, max_length=150)

    # EmailStr comprueba la FORMA del email (que tenga parte local, @ y
    # dominio con formato válido). No comprueba que el buzón exista de
    # verdad: eso solo se sabe enviando un correo. Necesita el paquete
    # email-validator instalado; está en el venv, pero PENDIENTE de
    # añadir a requirements.txt.
    email: EmailStr = Field(max_length=150)

    telefono: str = Field(min_length=1, max_length=30)

    # Al declarar el tipo como el Enum, Pydantic acepta el texto "bano" y
    # lo convierte en TipoReforma.BANO; cualquier otro texto se rechaza
    # con un error que lista los valores permitidos.
    tipo_reforma: TipoReforma

    # gt=0 -> estrictamente mayor que cero. Se usa gt y no ge ("greater
    # or equal") a propósito: una reforma de 0 m2 no existe.
    m2: float = Field(gt=0, description="Metros cuadrados a reformar")

    nivel_acabados: NivelAcabados

    # bool no lleva valor por defecto: es obligatorio. Es deliberado —
    # este campo fuerza el Gate HITL cuando es true (recargo de informe
    # técnico), así que no puede quedar implícito por descuido.
    incluye_cambios_estructurales: bool

    # Las fotos son OPCIONALES: default_factory=list hace que un lead sin
    # fotos sea perfectamente válido (llega una lista vacía).
    # max_length limita a MAX_FOTOS_LEAD elementos.
    # El tipo es list[str] (rutas), no las imágenes: el archivo se sube
    # aparte con las URLs firmadas de PhotoUploadSlot.
    fotos: list[str] = Field(
        default_factory=list,
        max_length=MAX_FOTOS_LEAD,
        description="Rutas de las fotos ya subidas; lista vacía si no hay",
    )

    # Identificador temporal que emparejará este formulario con las fotos
    # subidas antes. La comprobación de que las rutas de 'fotos'
    # pertenecen de verdad a este lead_token es lógica de negocio y va en
    # app/services/, no aquí.
    lead_token: str = Field(min_length=1)


class LeadCreateResponse(BaseModel):
    """
    Respuesta de POST /leads.

    lead_id y cliente_id son enteros porque las claves primarias reales
    de Supabase son SERIAL (enteros autoincrementales), VERIFICADO con
    una consulta a information_schema: ambas columnas son 'integer' con
    default nextval('..._id_seq'). No son UUID.
    """

    lead_id: int
    cliente_id: int

    # Texto por ahora. Queda pendiente de cerrar (decisión D4) si su
    # valor será el estado real de la oportunidad ('nueva') o una
    # etiqueta propia. Se deja como str para no fijar esa decisión desde
    # el esquema.
    status: str
