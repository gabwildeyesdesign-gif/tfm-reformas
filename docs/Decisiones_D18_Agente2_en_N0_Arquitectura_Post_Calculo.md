# Reformas Integrales Amedida — Decisiones N0
## D18 — Agente 2 (asesor post-cálculo) entra en N0: arquitectura, alcance y datos de negocio

**Sesión:** 2026-09-24. **Revisa explícitamente D16.5** (que difería el Agente 2 a N1 y cerraba N0 con un
paso determinista sin LLM). **Estado al 2026-09-25:** alcance cerrado (D18.1); plazos cerrados (D18.7, con
la interpretación confirmada por Gabi); Code node hecho (D17.6); estado de sesión en backend hecho en parte
(`lead_token` persistido, `50a3c9d`); endpoint de consulta por sesión en curso (rama
`feat/n0-endpoint-sesion`).

**Entrega del TFM: como muy tarde, finales de noviembre de 2026.**

---

### D18.1 — Alcance (REVISADO por Gabi el 2026-09-24 por la tarde; sustituye a la primera versión)

Primera versión (misma sesión, descartada): N1 desaparecía y todo su contenido se redistribuía.
**Versión vigente:**

- **N0** = captura (Agente 1) + presupuesto + **Agente 2 asesor con solicitud de visita** + Gate HITL (WF2)
  + seguimiento (WF3). Sin RAG.
- **N1 sigue existiendo** con lo complejo: RAG, fotos en el asesor, búsqueda de huecos reales en calendario,
  persuasión rica.
- **N2** = motor OCR de facturas (se renombrará).

**Precisión registrada:** Gate (`POST /gate-decisions`) y seguimiento (`create-followup-task`, barrido D13)
**ya eran N0** según `CLAUDE.md` y la Adenda. La única adición real a N0 es el Agente 2 con la tool de
solicitud de visita.

**Presupuesto de tiempo:** de 2026-09-24 a finales de noviembre hay ~9 semanas. Reservando 3 para la memoria,
quedan ~6 de implementación para: Code node, estado de sesión, `/visits`, `/gate-decisions`,
`get_business_rules()`, Agente 2, WF2, WF3, WF4. Ajustado pero viable sin RAG. **Punto de corte:** si a
mitad de octubre no están `/visits` y `/gate-decisions`, el Agente 2 baja a N1 y N0 se entrega con la
Opción C de D18.2.

**Acción:** actualizar `CLAUDE.md` y `Propuesta_TFM_Actualizada_N0`; informar al tutor del Agente 2 en N0.

### D18.2 — Regla de Tres: conversación posterior al presupuesto

| Opción | Evaluación |
|---|---|
| A — Un solo agente de principio a fin | Descartada: mezcla captura estricta con conversación abierta, amplía la superficie de inyección (D16.9), el agente tendría la cifra en contexto y la reformularía (choca con Adenda 1.1a), y un cambio de prompt puede romper la captura |
| **B — Dos agentes con traspaso determinista** (Agente 1 captura → Code node renderiza la cifra sin LLM → Agente 2 asesora) | **Elegida.** Cada agente con un único trabajo, testeables por separado; la cifra nunca pasa por un LLM |
| C — Cierre determinista con menú, sin Agente 2 | Descartada como destino, conservada como entregable mínimo / punto de corte (D18.1) |

**Coste:** el coste de API es irrelevante (D16.7, céntimos por lead). La variable de negocio es el coste
humano: cada duda no resuelta es una llamada del equipo.

### D18.3 — Prerrequisito: estado de sesión para enrutar turnos

Cada mensaje del cliente vuelve a disparar el workflow desde el Chat Trigger. Sin estado persistente, el
turno siguiente al presupuesto vuelve a entrar en el Agente 1. `intermediateSteps` (D17.2) solo sirve dentro
del mismo turno.

**Hecho (`50a3c9d`, 2026-09-24):** `leads.lead_token` (= `sessionId` del chat) persistido con UNIQUE;
`POST /leads` idempotente por token (repetición → mismo lead, `creado: false`, HTTP 200).
**En curso:** `GET /leads/session/{lead_token}` (sin importes; 200 con `existe=false` si no hay lead; con
varias oportunidades devuelve la primera, igual que la idempotencia, y deja un `[AVISO]`).

### D18.4 — Reparto de responsabilidades

- **Gate HITL: nunca en un agente.** WF2 determinista: se dispara si `requiere_aprobacion = true`, Telegram
  send-and-wait, `POST /gate-decisions`, resultado al cliente por email (el chat ya estará cerrado). El
  Agente 2 no tiene la tool de gate-decisions: podría aprobar su propio Gate.
- **Seguimiento 48 h: fuera del agente.** Barrido periódico de D13 (WF3), con plantilla.
- **Herramientas del Agente 2:** lectura de reglas de negocio (MCP, `get_business_rules()`, ya especificada
  como solo-MCP), `solicitar_visita` (HTTP tool → `POST /visits`, solo REST, con confirmación explícita).
  Nunca: calcular, modificar presupuesto, Gate, guardar datos del lead.
- **Conocimiento:** `tarifas_base.incluye_tipico` (ya existe; el agente lo presenta como orientativo) + filas
  de `reglas_negocio` para plazos de baño/cocina y política de visita/informe. Sin RAG, sin conocimiento
  pegado en el prompt.
- **Límites conversacionales (borrador):** no negocia ni ofrece descuentos; no repite ni recalcula cifras;
  no da consejo técnico fuera de la base de conocimiento; no compromete fechas de inicio; no opina sobre
  licencias o aspectos legales; caso estructural con guion fijo que explica por qué hace falta la visita;
  ante "me equivoqué en un dato" responde que un técnico lo corregirá al revisar la solicitud (el lead ya
  guardado no se modifica desde el chat).
- **Contexto del Agente 2:** recibe datos estructurados del lead y del estado del presupuesto desde la base
  de datos, no el historial del Agente 1. Memoria propia con clave distinta (p. ej. `sessionId + "_asesor"`)
  para no heredar el prompt ni los turnos de la captura.

### D18.5 — Decisiones de negocio cerradas

1. **Gate por importe (caso 3 de D17.4): NO se muestra ninguna cifra.** Redacción: *"Por las
   características de tu proyecto, un técnico lo revisará personalmente y se pondrá en contacto contigo para
   darte una información más precisa."* (evitar "agente" y "valor elevado"). El Code node no renderiza el
   importe y **el importe no entra en el contexto del Agente 2** en casos de Gate. Sí va en el aviso de
   Telegram al equipo (WF2).
2. **Visita técnica gratuita.** Solo se cobra si el arquitecto debe emitir un informe; el agente comunica el
   importe de `recargo_informe_tecnico` (fuente única, coherente con el cálculo).
3. **Umbrales del Gate sin cambios** (D9): 13.000 € baño/cocina, 10.000 € integral/parcial, sin IVA.
4. **Visita = solicitud, no reserva.** El cliente indica día o franja; `POST /visits`; administración
   verifica y llama.
5. **Agente 2 en N0** (D18.1).

### D18.6 — Mejora futura (N1): comprobación de disponibilidad en calendario

Los Code nodes de n8n son **JavaScript**, no Java; n8n tiene nodo nativo de Google Calendar — verificar en la
versión instalada si incluye consulta de disponibilidad antes de diseñar nada.

### D18.7 — Plazos de ejecución (CERRADO)

**Regla confirmada por Gabi:** el agente da plazo orientativo **solo si** `tipo_reforma ∈ {bano, cocina}`
**y** no hay Gate **y** no hay cambios estructurales. En cualquier otro caso, el plazo lo da el humano.

| tipo_reforma | Plazo de ejecución (orientativo) |
|---|---|
| `bano` | 2–3 semanas |
| `cocina` | 2–4 semanas de obra, más 3–6 semanas de fabricación de muebles antes del montaje |

Fuentes: La Bagnoteca, Geteco, Vip Reformas (ver abajo). Los tramos de `integral_vivienda` investigados se
conservan solo como referencia para la memoria, no se cargan en la base de datos.

### D18.8 — `incluye_tipico` real (leído de la base de datos, 2026-09-24)

12 filas con contenido. Se deja como está (decisión de Gabi: las reglas de negocio no necesitan ser exactas
con el mundo real para el TFM). Defectos anotados para la memoria:
- **Cocina incluye "electrodomésticos"** en los tres niveles; si el precio de mercado usado no los incluye,
  sería un compromiso comercial. Mitigación: el Agente 2 presenta `incluye_tipico` como orientativo.
- `parcial_acabados` medio y alto solo difieren en "suelo calidad media/alta".
- `integral_vivienda/basico` no menciona carpintería; medio y alto sí.

### D18.9 — Ventana de chat única con dos agentes (Regla de Tres)

El Chat Trigger (hosted o widget embebido) está ligado al webhook de **un** workflow. La ventana solo cambia
si el cliente tiene que ir a otro webhook.

| Opción | Evaluación |
|---|---|
| **A — Un workflow de entrada con router al inicio: Chat Trigger → consulta de estado de sesión → Switch → Agente 1 o Agente 2** | **Elegida para N0.** Un solo webhook, una sola ventana, mínimo movimiento de datos. Cada agente con su memoria y su prompt |
| B — Workflow de entrada con router que llama a sub-workflows (Execute Workflow) por agente | Más modular y testeable por separado, pero obliga a pasar sessionId, configurar la clave de memoria a mano y devolver la respuesta al padre. Migración natural si el lienzo de A se vuelve ilegible |
| C — Un Chat Trigger por agente | Descartada: dos URLs → dos ventanas; el cliente tendría que cambiar de chat |

WF2, WF3 y WF4 sí son workflows separados: no hablan con el cliente por el chat.

---

## Orden de implementación (actualizado 2026-09-25)

1. ~~Code node de cierre determinista~~ — **Hecho** (D17.6).
2. Estado de sesión: ~~persistir `lead_token`~~ (**hecho**, `50a3c9d`) + `GET /leads/session/{lead_token}`
   (**en curso**) + router en n8n (D18.9).
3. Backend: `POST /visits` y `POST /gate-decisions`.
4. Filas de conocimiento en `reglas_negocio` + `get_business_rules()`.
5. Agente 2.
6. WF2 (Gate HITL), WF3 (barrido D13), WF4 (errores).

## Diagramas BPMN y de workflows iniciales: DESFASADOS

Hechos para el chat dirigido. No reflejan D16 (captura libre), D13 (barrido periódico, no Wait 48 h) ni D18.
Redibujar cuando el router y el Agente 2 estén construidos; no llevarlos a la memoria en su estado actual.

## Fuentes (plazos y calidades)
- https://labagnoteca.es/tiempo-reforma-de-bano/
- https://www.geteco.es/tiempo-reforma-integral
- https://www.vipreformas.es/blog/cuanto-se-tarda-en-reformar-una-cocina/
- https://www.batecs.es/cuanto-se-tarda-en-reformar-un-piso-de-verdad-calendario-por-m2-y-los/ (referencia, integral)
- https://www.pintoresmadrid.eu/cuanto-tarda-pintar-piso.html (referencia, pintura)
- https://www.batecs.es/calidad-media-alta-o-premium-en-una-reforma-integral-en-que-se-nota-el/
