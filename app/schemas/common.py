"""
Tipos y constantes compartidos por varios esquemas.

Viven aquí, y no dentro de leads.py, porque los mismos valores los va a
necesitar también el esquema de calculate-estimate en el siguiente bloque.
Si estuvieran en leads.py, el esquema de presupuestos tendría que importar
del de leads: una dependencia entre dos dominios distintos que no
significa nada en el negocio.
"""

# "enum" (de "enumeration") es un módulo de la librería estándar de Python.
# Sirve para definir un conjunto CERRADO de valores con nombre: en vez de
# "esto es un texto cualquiera", decimos "esto es uno de estos cuatro".
from enum import Enum


class TipoReforma(str, Enum):
    """
    Tipos de reforma admitidos.

    ¡OJO! Los valores de la derecha NO son decorativos: son exactamente
    los que están almacenados en la columna tarifas_base.tipo_reforma de
    Supabase, verificados con una consulta real (SELECT DISTINCT), no
    copiados de la documentación. La tabla tiene además un CHECK que
    solo admite estos cuatro.

    Por eso van SIN eñe y SIN tildes: en la base de datos es 'bano', no
    'baño'. Si aquí escribiéramos 'baño', el lead se guardaría sin error
    (la columna oportunidades.tipo_reforma no tiene CHECK), pero después
    calculate-estimate buscaría 'baño' en tarifas_base, no encontraría
    tarifa, y TODOS los leads de baño acabarían marcados como
    'requiere_revision' por un fallo imposible de ver a simple vista.

    Heredamos de (str, Enum) y no solo de Enum. Eso hace que cada miembro
    SEA además un texto de Python de pleno derecho: se puede comparar con
    "bano", psycopg2 lo envía a Postgres como texto sin conversión
    manual, y FastAPI lo escribe en el JSON como "bano" y no como un
    objeto raro. Python 3.12 ofrece también StrEnum, que hace algo muy
    parecido; se usa (str, Enum) porque es la forma que aparece en la
    inmensa mayoría de la documentación y los ejemplos que vas a
    encontrar mientras aprendes.
    """

    # A la izquierda, el NOMBRE con el que se usa desde Python
    # (TipoReforma.BANO). A la derecha, el VALOR real que viaja por el
    # JSON y que se guarda en la base de datos.
    BANO = "bano"
    COCINA = "cocina"
    INTEGRAL_VIVIENDA = "integral_vivienda"
    PARCIAL_ACABADOS = "parcial_acabados"


class NivelAcabados(str, Enum):
    """
    Niveles de acabados admitidos.

    Mismo criterio que TipoReforma: valores verificados contra
    tarifas_base.nivel_acabados en Supabase. Son 'basico' (sin tilde),
    'medio' y 'alto'. La combinación de estos 3 niveles con los 4 tipos
    de reforma da las 12 filas de tarifas_base, que tiene además un
    UNIQUE (tipo_reforma, nivel_acabados) para que no haya duplicados.
    """

    BASICO = "basico"
    MEDIO = "medio"
    ALTO = "alto"


# Número máximo de fotos que se aceptan en un lead.
#
# PROVISIONAL — PENDIENTE PARA EL SIGUIENTE BLOQUE:
# este valor debería leerse de la tabla reglas_negocio (clave
# 'max_fotos_lead'), igual que se hace con el umbral del Gate o el margen
# de empresa, para poder cambiarlo sin tocar código.
#
# VERIFICADO contra Supabase: esa fila NO EXISTE todavía. reglas_negocio
# solo tiene 4 claves (umbral_aprobacion_manual, margen_empresa_pct,
# recargo_informe_tecnico, dias_espera_decision_post_visita). Hasta que se
# inserte, services/ no puede leerla y el valor se queda aquí escrito a
# mano. Se define como constante compartida (y no como un 5 suelto
# repetido en dos modelos) para que el día que se sustituya por la
# lectura real solo haya que cambiar un sitio.
MAX_FOTOS_LEAD = 5
