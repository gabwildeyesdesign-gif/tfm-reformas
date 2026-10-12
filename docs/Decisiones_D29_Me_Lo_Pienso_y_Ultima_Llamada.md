Reformas Integrales Amedida — Decisiones N0
D29 — "Me lo pienso": aplazamiento a N1 y aviso de "última llamada" por regla de negocio

Sesión: 2026-10-11. Aceptado por Gabi el 2026-10-11. Base: D19 (el Gate "me lo pienso" no se registra), D24.4 (registro de intentos de llamada, condicionado al 6 de noviembre), D25.14 (la lista diaria es el recordatorio), D25.15 (plazos estrictos en horas), D13 (las reglas viven en reglas_negocio), D21.6 (n8n no lee la base de datos), plan docs/Plan_Resultado_Llamadas.txt (5f7d4df, C2 y 5.3).

1. Problema

Un cliente de seguimiento (o con Gate) que dice "me lo pienso" no tiene resultado registrable: sale cada día en la lista diaria (apartados a o c) sin final, y lo hablado en esa llamada no queda registrado.

2. Decisiones
D29.1 — Resultado 'aplazar' (un aplazamiento único, volver a llamar a las 48 h): APLAZADO a N1, junto con D24.4. Evaluado y rechazado para N0 (Regla de Tres): exige retirar el UNIQUE (oportunidad_id) de decisiones_gate (migración expand/contract), cambiar la lista diaria y GET /gate-avisos (usados por flujos publicados), con más de un día de trabajo; y es el mismo mecanismo que el registro de intentos de llamada (llamada no definitiva): hacerlo ahora sería construirlo dos veces. El tutor había estimado "25-35 %" sin medirlo; corregido. Queda como limitación de N0 (R5 del plan de 4b) y como trabajo futuro con su diseño: un aplazamiento único, regla horas_volver_a_llamar, dos índices únicos parciales, filtro en la lista y en gate-avisos.
D29.2 — Aviso de "última llamada" por regla de negocio (propuesta de Gabi, opción B). El backend marca en GET /llamadas-del-dia los elementos de a) y c) que llevan más de horas_ultima_llamada desde presupuestos.created_at (regla NUEVA en reglas_negocio; propuesta 168 h, valor a confirmar por Gabi; entero 1..8760, misma validación y mismo 503 que las otras dos). Administración hace una última llamada y, si sigue sin decisión, registra "descartar" con motivo sin_decision. El sistema nunca cancela solo.
A. n8n calcula los días con un número fijo en el flujo: incumple D13 y D21.6. Rechazada.
C. Cancelación automática pasados X días: una persona decide el cierre de una venta grande. Rechazada. Cubre aproximadamente el 80 % del valor (fin del bucle) a una fracción del coste; no registra lo hablado en la primera llamada (eso llega con D29.1 / D24.4 en N1). Dónde: bloque 4c, rama propia después de 4b y antes de WF3 (un endpoint por rama, CLAUDE.md). Solo AÑADE un campo al contrato (como 4a-2).
D29.3 — Motivo nuevo sin_decision en el catálogo, en 4b. Va en la migración paso12 del plan de 4b, que ya rehace el CHECK de motivos. Válido en "descartar"; prohibido en "cliente_cancela" (quien cancela, ha decidido).
D29.4 — Mejora gratuita para WF3: con fecha_presupuesto (ya en cada elemento), n8n puede escribir "lleva X días en seguimiento" sin tocar el backend.