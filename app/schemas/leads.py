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
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# re: el módulo de expresiones regulares de la biblioteca estándar. Se usa
# para limpiar y comprobar el teléfono.
import re

# Decimal: el tipo de número decimal EXACTO de la biblioteca estándar. Se
# usa para m2 por el mismo motivo que en los importes del presupuesto:
# float guarda los números en binario y muchos decimales corrientes no
# tienen representación exacta.
from decimal import Decimal

# Import completo (app.schemas.common) y no relativo (.common): así, al
# leer cualquier línea de abajo, se ve exactamente de qué archivo del
# proyecto sale cada nombre, sin tener que deducirlo.
from app.schemas.common import (
    MAX_FOTOS_LEAD,
    MAX_M2_LEAD,
    NivelAcabados,
    TipoReforma,
)

# Caracteres de "decoración" que una persona escribe en un teléfono y que
# no forman parte del número: espacios, guiones, puntos y paréntesis.
# \s cubre cualquier espacio en blanco: el normal, el tabulador, el salto
# de línea y el espacio duro (U+00A0) que aparece al copiar un número de
# una web. "+34 (666) 777-444" y "+34666777444" son el mismo teléfono; se
# guardan del segundo modo.
# re.compile prepara la expresión una sola vez al importar el archivo, en
# vez de volver a interpretarla en cada petición.
_DECORACION_TELEFONO = re.compile(r"[\s\-.()]")

# Forma que debe tener el teléfono YA LIMPIO: un "+" opcional al principio
# y después entre 9 y 15 dígitos (15 es el máximo del estándar
# internacional E.164; 9 es el largo de un número español sin prefijo).
#
# Se escribe [0-9] y NO \d a propósito: en Python, \d acepta cualquier
# dígito Unicode, incluidos los árabes ("٦٦٦") o los devanagari. Con \d,
# un teléfono escrito con esos símbolos pasaría la validación y llegaría
# a la base de datos. [0-9] son exactamente los diez dígitos de siempre.
_FORMATO_TELEFONO = re.compile(r"\+?[0-9]{9,15}")


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
    Cuerpo de la petición POST /leads: los datos que el Agente 1 del chat
    web ha recogido y confirmado con el cliente, enviados por n8n.

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
    # email-validator instalado (fijado en requirements.txt como
    # email-validator==2.3.0): sin él, Pydantic falla al importar este
    # archivo.
    email: EmailStr = Field(max_length=150)

    # Los límites de Field se aplican al texto TAL COMO LLEGA, antes de
    # limpiarlo (max_length=30 es el ancho de clientes.telefono). La forma
    # del número la comprueba después el validador validar_telefono, más
    # abajo en esta misma clase.
    telefono: str = Field(min_length=1, max_length=30)

    # Al declarar el tipo como el Enum, Pydantic acepta el texto "bano" y
    # lo convierte en TipoReforma.BANO; cualquier otro texto se rechaza
    # con un error que lista los valores permitidos.
    tipo_reforma: TipoReforma

    # Decimal y no float, por el mismo motivo que en los importes: float
    # guarda los números en binario y muchos decimales sencillos no tienen
    # representación exacta (Decimal(8.7) vale 8.699999999999999289...).
    # Aquí Pydantic convierte el número del JSON pasando por su texto, así
    # que 8.7 llega como Decimal('8.7') exacto (comprobado).
    #
    # gt es "greater than" (mayor que) y le es "less or equal" (menor o
    # igual). gt=0 rechaza el 0 y los negativos —una reforma de 0 m² no
    # existe—, y se usa gt y no ge a propósito. le=MAX_M2_LEAD rechaza una
    # superficie imposible. El límite es INCLUSIVO: 500 se acepta, 500.01
    # no. Sin ese tope, un lead de 60.000 m² se aceptaba aquí y reventaba
    # después al calcular el presupuesto, con un 500 para el cliente.
    m2: Decimal = Field(
        gt=0,
        le=MAX_M2_LEAD,
        description=f"Metros cuadrados a reformar (más de 0 y hasta {MAX_M2_LEAD})",
    )

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

    # ------------------------------------------------------------------
    # Validación del teléfono
    # ------------------------------------------------------------------
    # Hasta ahora telefono solo se validaba por LONGITUD, así que
    # "telefono666555" se aceptaba y se guardaba en clientes.telefono.
    #
    # @field_validator("telefono") es un DECORADOR de Pydantic: registra la
    # función de debajo como una comprobación extra del campo telefono.
    # mode="after" significa que se ejecuta DESPUÉS de las comprobaciones
    # normales (que sea texto, y los min_length/max_length de Field), así
    # que aquí "valor" ya es seguro un str de 1 a 30 caracteres.
    #
    # @classmethod es obligatorio en los validadores de Pydantic v2: la
    # función se llama sobre la CLASE, antes de que exista el objeto, y
    # por eso recibe "cls" en vez de "self".
    #
    # Lo que la función DEVUELVE es lo que queda guardado en el campo. Por
    # eso sirve también para normalizar: devuelve el número ya limpio.
    # Si lanza ValueError, Pydantic lo convierte en un error de validación
    # con loc = ["telefono"], y FastAPI responde 422 nombrando el campo.
    @field_validator("telefono", mode="after")
    @classmethod
    def validar_telefono(cls, valor: str) -> str:
        # sub("", valor) sustituye cada carácter de decoración por nada,
        # es decir, lo borra: "+34 666-777-444" -> "+34666777444".
        limpio = _DECORACION_TELEFONO.sub("", valor)

        # fullmatch exige que la expresión cubra el texto ENTERO. No se usa
        # match con un "$" al final porque, en Python, "$" también encaja
        # justo antes de un salto de línea final. Hoy es una defensa extra:
        # \s ya borra cualquier salto de línea en el paso anterior, así que
        # "666777444\n" llega aquí como "666777444". Se mantiene para que
        # la comprobación siga siendo correcta si algún día se cambia la
        # lista de caracteres de decoración.
        if _FORMATO_TELEFONO.fullmatch(limpio) is None:
            raise ValueError(
                "teléfono no válido: tras quitar espacios, guiones, puntos "
                "y paréntesis debe quedar un '+' opcional y de 9 a 15 dígitos"
            )
        return limpio


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

    # Identificador de la oportunidad creada EN LA MISMA OPERACIÓN que el
    # lead, dentro de la misma transacción: o se crean las tres filas
    # (cliente, lead y oportunidad) o no se crea ninguna.
    #
    # Por qué está aquí y no se deduce después: la tabla oportunidades es
    # obligatoria en el resto de la cadena (presupuestos.oportunidad_id y
    # visitas.oportunidad_id son NOT NULL y apuntan a ella), pero ningún
    # endpoint del contrato original decía crearla. Al devolverla aquí,
    # n8n puede agendar la visita más adelante sin tener que hacer una
    # consulta extra para averiguar qué oportunidad corresponde al lead.
    #
    # Es una AMPLIACIÓN del contrato de la Adenda (que para POST /leads
    # solo listaba lead_id, cliente_id y status), decidida en D1 y
    # anotada como tal para la memoria.
    #
    # Lo rellena app/services/leads_service.py con el id que devuelve
    # el INSERT en oportunidades (RETURNING id), dentro de la misma
    # transacción que las otras dos filas. Verificado por HTTP real en
    # scripts/check_leads_endpoint_http.py.
    oportunidad_id: int

    # Estado REAL de la oportunidad recién creada: decisión D4, CERRADA
    # (ver docs/TFM_Decisiones_Modelo_Datos_Leads.txt, apartado D4).
    # services/ lo obtiene con RETURNING id, estado en el mismo INSERT,
    # así que el valor devuelto es literalmente el que Postgres acaba de
    # escribir en la fila, no una copia escrita aparte en Python que
    # pudiera desincronizarse del dato. En N0 siempre vale 'nueva'.
    # Se descartó una etiqueta propia ("completo"/"incompleto") porque
    # por la puerta REST todos los campos son obligatorios, así que ese
    # caso no puede darse.
    #
    # Se mantiene como str (y no como un enum cerrado) porque el valor
    # viene de la base de datos, que ya lo restringe con su propio CHECK;
    # repetir la lista aquí crearía una tercera copia de los estados que
    # habría que mantener sincronizada a mano.
    status: str
