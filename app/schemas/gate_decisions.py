"""
Esquemas de datos del Gate que siguen vivos tras quitar el alias POST
/gate-decisions: solo DecisionGate, las dos decisiones posibles de una
llamada de Gate (plan: docs/Plan_Endpoint_Gate_Decisions.txt, sección 1.3).

Cambio 2026-10-22: alias POST /gate-decisions quitado (bloque 5); versión
anterior en aba39c4. Con él se fueron GateDecisionCreate, GateDecisionResponse
y MotivoDescarte, que solo usaba el alias. DecisionGate se queda porque lo
usan GET /gate-avisos (app/schemas/aviso_gate.py) y la ficha
(app/services/ficha_oportunidad_service.py). El contrato vigente del
resultado de una llamada está en app/schemas/resultados_llamada.py.
"""

# Enum: para los conjuntos CERRADOS de valores (DecisionGate).
from enum import Enum


class DecisionGate(str, Enum):
    """
    Las dos decisiones posibles tras la llamada (plan, 1.3).

    Mismo patrón que TipoReforma en common.py: (str, Enum) hace que cada
    valor sea también un texto normal, que psycopg2 envía a Postgres tal
    cual. Los valores coinciden con el CHECK chk_decisiones_gate_decision
    (migración paso10): si se añade uno aquí, hay que añadirlo también allí.
    """

    # El cliente sigue adelante: sin ver la obra no hay presupuesto
    # definitivo, así que "seguir" equivale a acordar una visita.
    VISITA_ACORDADA = "visita_acordada"
    # El cliente no sigue (o no se le localiza): la oportunidad se pierde.
    DESCARTAR = "descartar"
