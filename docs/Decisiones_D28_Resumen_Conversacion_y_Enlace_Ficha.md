Reformas Integrales Amedida — Decisiones N0
D28 — Enlace a la ficha en WF2 descartado y resumen de la conversación para administración

Sesión: 2026-10-11. Aceptado por Gabi el 2026-10-11 ("dejamos lo anterior anotado para hacerlo cuando creemos el agente 2"). Base: D26.1, D26.7, D26.8, D18 (el Agente 2 entra después del cálculo), D21.6 (n8n no lee la base de datos).

1. Hallazgo (pregunta de Gabi, 2026-10-11)

"¿De qué sirve enseñar la ficha completa en el aviso del Gate si el email ya informa del tipo de reforma?" Comprobado en el código (main = 9153532): cuando WF2 avisa, la oportunidad acaba de recibir su presupuesto con Gate. POST /visits rechaza los casos con Gate (409 presupuesto_con_gate, visits_service.py), nadie ha llamado todavía y el historial solo tiene el alta y el cálculo. Lo único que la ficha añadiría es "otras solicitudes con el mismo email". El paso 3a del handoff ("enlace a la ficha en WF2", puesto primero "por orden de valor") se propuso sin medir ese valor: error del tutor.

2. Decisiones
D28.1 — Resumen de la conversación para quien llama (opción A). Lo que el cliente cuenta en el chat sobre su proyecto no llega hoy a administración: leads.mensaje_original existe pero se guarda siempre a NULL (leads_service.py) y el historial del chat solo vive en la memoria de n8n. Se añadirá un resumen corto escrito por el agente, guardado en la base de datos, con las protecciones de D26.7 (prompt que prohíbe datos personales, límite de longitud y rechazo 422 de emails y teléfonos), visible en el email de WF2 y en la ficha, y NO en la lista diaria (acumulación, D26 sección 1.3).
B. Guardar la conversación completa: más riesgo RGPD (conversaciones enteras con datos personales, retención) y mucho texto que nadie lee. Rechazada.
C. Nada (administración pregunta en la llamada): llamada a ciegas en lo cualitativo. Rechazada. Detalle de diseño: WF2 se dispara ANTES de que hable el Agente 2 (D18), así que, para que el resumen llegue al aviso del Gate, lo natural es que lo escriba el Agente 1 al capturar (un campo más de lo que envía a POST /leads). Pregunta abierta para ese plan: dónde se guarda (columna nueva o leads.mensaje_original, hoy sin uso) y si el Agente 2 lo amplía después. Valor medible (business case, D26.8): tiempo de preparación de la llamada de un caso con Gate. Cuándo: en el bloque del Agente 2, junto con D26.7 (misma migración, mismo filtro, misma prueba de prompts).
D28.2 — Sin enlace a la ficha en WF2. WF2 no se toca. El enlace a la ficha se pone en cada elemento de WF3, donde sí añade lo que falta (importes, visitas, informes de llamadas anteriores, historial): la lista diaria solo lleva el resumen sin email ni importes. Los casos con Gate también lo reciben por WF3 (apartado a) si nadie los llama a tiempo.

**[Nota 2026-10-20, rama `feat/n0-resumen-solicitud`: D28.1 implementado; revisado en un punto]**
- **D28.1 revisada por D36.8 (opción b): nunca un 422 por el resumen.** El texto de D28.1 dice "rechazo 422 de emails y
  teléfonos". D36.8 (decisión de Gabi y del tutor del 2026-10-19; su documento lo tiene el tutor en el Proyecto de
  claude.ai, todavía no en `docs/`; es la P1 = b de `docs/Plan_Resumen_Solicitud_D28.txt`, secciones 4.4 y 12) lo
  cambia: si el resumen no pasa el filtro (email, teléfono o más de 300 caracteres), el lead se guarda **siempre** con
  `resumen_solicitud` NULL y `POST /leads` lo dice en `resumen_solicitud_estado`. Motivos: C1 y C6 del plan (con el
  prompt del Agente 1, un 422 que nombra un campo hace que pida la corrección AL CLIENTE, que nunca escribió ese
  resumen; un fallo del modelo perdería el lead entero).
- **Lo implementado.** Columna `leads.resumen_solicitud` (TEXT NULL, CHECK de 1 a 300 caracteres; migración paso15,
  aplicada el 2026-10-20 a las 12:42:46). Campo opcional `resumen_solicitud` en `POST /leads` (el chat publicado, que
  no lo envía, sigue dando 201) y `resumen_solicitud_estado` en su respuesta: `guardado`, `sin_resumen`,
  `descartado_longitud`, `descartado_email` o `descartado_telefono`. En una repetición con el mismo `lead_token`, el
  resumen nuevo se ignora y el estado describe lo guardado. Se ve en la raíz de `GET /gate-avisos` y de la ficha;
  **nunca** en `GET /llamadas-del-dia` (acumulación, D26 sección 1.3) ni en `GET /leads/session`. La conversación
  completa sigue sin guardarse. Contrato: Adenda, sección 5.12.
- **Límite conocido D37.1.** El patrón de teléfono (el mismo de D26.7) toma por teléfono un rango de importes con
  guion: "presupuesto 15.000-20.000 euros" y "8.000 - 10.000 €" se descartan como `descartado_telefono`; "entre
  15.000 y 20.000 euros" se guarda. No se cambia el patrón (P6 del plan): se corrige en el prompt del Agente 1 (escribir
  los rangos como "entre X y Y"), en n8n, después del merge. `scripts/check_resumen_solicitud_service.py` fija los
  tres casos con su resultado de hoy, como límite conocido.
- **Pendiente en n8n (tutor y Gabi):** el parámetro de la tool `guardar_datos_reforma` (con un cuerpo que sea JSON
  válido con cualquier texto: requisito C5, que afecta también a nombre, email y teléfono), el prompt del Agente 1
  (`prompts/agente1_captura.md` y el nodo a la vez) y la plantilla del email de WF2 y la página de la ficha (pintar el
  resumen con `esc()`, "—" si es null, etiquetado "Resumen del asistente").
