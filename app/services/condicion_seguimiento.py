"""
La condición "seguimiento por abrir", escrita UNA sola vez (plan:
docs/Plan_Endpoint_Create_Followup_Task.txt, sección 3, P2).

La usan dos sitios, que no pueden contradecirse:
  - GET /llamadas-del-dia, apartado b) seguimientos_por_abrir
    (listado_llamadas_service.py): une los criterios con AND en su WHERE;
  - POST /create-followup-task (followup_service.py, paso B2): pide cada
    criterio como una columna de sí o no, y si uno falla responde 409 con
    su nombre.

Como los dos leen la MISMA tupla de criterios, con los MISMOS parámetros,
una oportunidad sale en la lista si y solo si el endpoint la acepta.

También vive aquí la regla del plazo (horas_seguimiento_presupuesto) y su
validación (entero entre 1 y 8760), para que se valide igual en sus dos
lectores.

Como todo services/, este archivo NO importa fastapi ni fastmcp.
"""

# datetime: el tipo del "ahora" que se pasa como parámetro a la consulta.
from datetime import datetime

# Decimal: reglas_negocio.valor llega de Postgres como Decimal ('48.00').
from decimal import Decimal

# ======================================================================
# Constantes
# ======================================================================

# Clave de reglas_negocio con las horas del plazo (migración paso11).
CLAVE_SEGUIMIENTO = "horas_seguimiento_presupuesto"

# Máximo de horas de una regla de plazo: un año (P6 del plan del listado).
# Sin máximo, un valor enorme hacía caer "ahora - esas horas" fuera del
# rango de fechas de Postgres y respondía 500 en vez de 503.
MAX_HORAS_REGLA = 8760

# El ÚNICO estado de la oportunidad desde el que se abre un seguimiento.
ESTADO_POR_ABRIR = "presupuesto_enviado"

# Estado de visita que NO cuenta como "tiene visita" (P5 del listado: una
# cancelada solo existe cuando otra la sustituye).
VISITA_CANCELADA = "cancelada"

# Los criterios, EN ORDEN (plan, 2.9: de lo estructural a lo pasajero).
# Cada uno es un par:
#   (motivo del 409 si NO se cumple, expresión SQL que debe ser verdadera)
# Las expresiones usan los alias de las consultas que las incluyen:
#   o = oportunidades, p = presupuestos (con LEFT JOIN en el endpoint, así
#   que p.* puede ser NULL). Son textos FIJOS del programa: los valores
#   van siempre como parámetros %(...)s (ver parametros_condicion).
CRITERIOS_SEGUIMIENTO = (
    # Tiene presupuesto (sin él, no hay nada que seguir).
    ("sin_presupuesto", "p.id IS NOT NULL"),
    # Su presupuesto NO tiene Gate (P1: el seguimiento es para clientes
    # sin Gate; el Gate es permanente).
    ("con_gate", "p.requiere_aprobacion = false"),
    # Está en 'presupuesto_enviado'.
    ("estado_no_permitido", "o.estado = %(estado_por_abrir)s"),
    # Nadie ha registrado todavía un contacto con el cliente.
    ("contacto_registrado", "o.fecha_ultimo_contacto IS NULL"),
    # No tiene ninguna visita, salvo canceladas. NOT EXISTS: "no hay
    # ninguna fila que cumpla esto".
    (
        "visita_existente",
        "NOT EXISTS (SELECT 1 FROM visitas v"
        " WHERE v.oportunidad_id = o.id AND v.estado <> %(visita_cancelada)s)",
    ),
    # El presupuesto tiene MÁS de esas horas (límite ESTRICTO, "<",
    # D25.15). make_interval(hours => ...) es una duración de esas horas.
    ("plazo_no_cumplido", "p.created_at < %(ahora)s - make_interval(hours => %(horas)s)"),
)


# ======================================================================
# Funciones
# ======================================================================


def problema_horas(clave: str, valor: Decimal | None) -> str | None:
    """
    Valida UNA regla de horas. Devuelve None si es válida, o el texto del
    problema si no lo es (no adivina ningún valor por defecto).

    clave: el nombre de la regla (solo para el texto del problema).
    valor: lo leído de reglas_negocio, o None si la fila no existe.

    Válida = un ENTERO entre 1 y MAX_HORAS_REGLA: 24.50, 0 o 8761 no.
    """
    # La fila no existe.
    if valor is None:
        return f"{clave}: no existe"
    # Tiene decimales (to_integral_value() redondea al entero: si cambia,
    # los tenía), o no está entre 1 y el máximo.
    if valor != valor.to_integral_value() or not (1 <= valor <= MAX_HORAS_REGLA):
        return f"{clave}: valor no válido ({valor}); debe ser un entero entre 1 y {MAX_HORAS_REGLA}"
    # Válida: no hay problema.
    return None


def parametros_condicion(ahora: datetime, horas: int) -> dict:
    """
    Los valores de los %(...)s de los criterios, para pasarlos a
    cursor.execute junto con la consulta.

    ahora: el now() de Postgres de la transacción de quien llama.
    horas: las horas de la regla, ya validadas con problema_horas.
    """
    # Un diccionario: nombre del parámetro -> valor.
    return {
        "estado_por_abrir": ESTADO_POR_ABRIR,
        "visita_cancelada": VISITA_CANCELADA,
        "ahora": ahora,
        "horas": horas,
    }


def where_condicion() -> str:
    """
    Los criterios unidos por AND, para el WHERE del apartado b) del
    listado (sin la palabra WHERE).

    Se calcula en el momento de llamarla (no al cargar el archivo), para
    que una prueba que cambie la tupla en memoria lo note.
    """
    # Cada expresión entre paréntesis, para que el AND no se mezcle con
    # nada de dentro; " AND ".join las une con " AND " entre cada dos.
    return " AND ".join(f"({expresion})" for _, expresion in CRITERIOS_SEGUIMIENTO)


def columnas_criterios() -> str:
    """
    Los criterios como COLUMNAS de un SELECT, una por criterio y en el
    mismo orden, cada una con el nombre de su 409:
        (p.id IS NOT NULL) AS sin_presupuesto, (...) AS con_gate, ...
    Para POST /create-followup-task, que necesita saber CUÁL falla.
    Se calcula en el momento de llamarla, como where_condicion.
    """
    # Cada criterio: "(expresión) AS motivo"; ", ".join los separa con
    # comas. Los motivos son nombres fijos de este archivo, válidos como
    # nombre de columna en SQL.
    return ", ".join(f"({expresion}) AS {motivo}" for motivo, expresion in CRITERIOS_SEGUIMIENTO)


def primer_criterio_incumplido(valores) -> str | None:
    """
    Recibe los valores de las columnas de columnas_criterios(), en el mismo
    orden, y devuelve el motivo del PRIMER criterio que no se cumple, o
    None si se cumplen todos.

    Un valor None (NULL en SQL) cuenta como NO cumplido, igual que en un
    WHERE, donde una fila con NULL tampoco entra.
    """
    # zip empareja cada criterio con su valor, en orden.
    for (motivo, _), valor in zip(CRITERIOS_SEGUIMIENTO, valores, strict=True):
        # "is not True": False y None son incumplidos (no basta con "not
        # valor", que diría lo mismo, pero así se lee la intención).
        if valor is not True:
            return motivo
    # Todos dieron True: la oportunidad cumple la condición.
    return None
