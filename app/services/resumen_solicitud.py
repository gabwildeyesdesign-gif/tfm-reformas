"""
Evaluación del resumen de la solicitud (D28.1): decide si el texto que
escribe el Agente 1 se guarda en leads.resumen_solicitud o se descarta, y
por qué. Plan: docs/Plan_Resumen_Solicitud_D28.txt, secciones 4.4, 5.1 y 12.

Regla de negocio (P1 = b): el resumen NUNCA hace perder un lead. Si no pasa
el filtro, el lead se guarda igual con resumen NULL, y POST /leads lo dice en
resumen_solicitud_estado. Por eso aquí no se lanza ningún error: se devuelve
el texto que se guarda (o None) y el estado.

Funciones puras, sin base de datos: se prueban directamente. Como todo
services/, este archivo NO importa fastapi ni fastmcp.
"""

# El máximo de caracteres (el mismo del CHECK de paso15) y el detector de
# datos de contacto compartido con el filtro de visitas (P6), con sus dos
# motivos.
from app.schemas.common import (
    MAX_RESUMEN_SOLICITUD,
    MOTIVO_EMAIL,
    MOTIVO_TELEFONO,
    motivo_datos_de_contacto,
)

# Los cinco estados posibles del resumen.
from app.schemas.leads import EstadoResumenSolicitud

# Los tres estados que significan "llegó un resumen, pero no se ha guardado".
# leads_service los usa para escribir el [AVISO] en stderr.
ESTADOS_DESCARTADO = (
    EstadoResumenSolicitud.DESCARTADO_LONGITUD,
    EstadoResumenSolicitud.DESCARTADO_EMAIL,
    EstadoResumenSolicitud.DESCARTADO_TELEFONO,
)


def evaluar_resumen(texto: str | None) -> tuple[str | None, EstadoResumenSolicitud]:
    """
    Devuelve (lo que se guarda en la columna, su estado). En orden, y con un
    solo motivo (el primero que falle, plan 4.4):
      1. None, vacío o solo espacios -> (None, SIN_RESUMEN).
      2. Más de MAX_RESUMEN_SOLICITUD caracteres -> (None, DESCARTADO_LONGITUD).
      3. Forma de email -> (None, DESCARTADO_EMAIL).
      4. Forma de teléfono -> (None, DESCARTADO_TELEFONO).
      5. Si no -> (el texto recortado, GUARDADO).
    La longitud se mide DESPUÉS de recortar, igual que el CHECK la mide sobre
    lo que se guarda. len() cuenta caracteres, como char_length en Postgres.
    """
    # Sin resumen: no llegó nada.
    if texto is None:
        return None, EstadoResumenSolicitud.SIN_RESUMEN
    # .strip() quita espacios, tabuladores y saltos de línea de los extremos.
    recortado = texto.strip()
    # Vacío o solo espacios: como si no hubiera llegado.
    if not recortado:
        return None, EstadoResumenSolicitud.SIN_RESUMEN
    # Demasiado largo: se descarta entero (nunca se corta: cortarlo podría
    # cambiar el sentido de la frase, opción c rechazada).
    if len(recortado) > MAX_RESUMEN_SOLICITUD:
        return None, EstadoResumenSolicitud.DESCARTADO_LONGITUD
    # Qué dato de contacto tiene, si tiene alguno (el mismo detector que las
    # visitas: email antes que teléfono).
    motivo = motivo_datos_de_contacto(recortado)
    # Con un email: se descarta.
    if motivo == MOTIVO_EMAIL:
        return None, EstadoResumenSolicitud.DESCARTADO_EMAIL
    # Con un teléfono: se descarta.
    if motivo == MOTIVO_TELEFONO:
        return None, EstadoResumenSolicitud.DESCARTADO_TELEFONO
    # Pasa el filtro: se guarda el texto recortado.
    return recortado, EstadoResumenSolicitud.GUARDADO


def estado_guardado(resumen_guardado: str | None) -> EstadoResumenSolicitud:
    """
    El estado de un resumen YA guardado, para la repetición de POST /leads
    (P3): GUARDADO si la columna tiene texto, SIN_RESUMEN si es NULL. No
    distingue un resumen descartado de uno que no llegó: en la base de datos
    los dos son NULL.
    """
    # Expresión condicional en una línea: "A if condición else B".
    return EstadoResumenSolicitud.GUARDADO if resumen_guardado is not None else EstadoResumenSolicitud.SIN_RESUMEN
