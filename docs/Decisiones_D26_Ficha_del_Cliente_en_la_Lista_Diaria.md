# Reformas Integrales Amedida — Decisiones N0
## D26 — Ficha del cliente en la lista diaria, visitas en un solo bloque y nuevo orden de bloques

**Sesión:** 2026-10-10. **Cerrado el 2026-10-10** (decisión de Gabi, opción C). **Revisado el 2026-10-10 (tarde):
D26.6 y D26.7, sección 4.**
**Base:** D25.9 (un endpoint de ficha para los tres tipos de llamada), D25.12 (orden de bloques),
plan de `GET /llamadas-del-dia` (`docs/Plan_Endpoint_Listado_WF3.txt`, pre-decisión 6 y sección 1.5),
`GET /gate-avisos/{oportunidad_id}` (Adenda 5.6).
**Revisa:** D25.12 (orden de los bloques a partir del 3) y la justificación de la pre-decisión 6 del plan del listado.

---

## 1. Hallazgo (Gabi, 2026-10-10)

1. **Administración llamaría sin saber qué pidió el cliente.** Cada elemento de `GET /llamadas-del-dia` lleva
   `oportunidad_id`, motivo, `tipo_reforma`, contacto (nombre y teléfono) y fechas: nada de m², acabados, cambios
   estructurales ni lo que escribió el cliente al pedir la visita.
2. **La ficha completa solo existe para los casos con Gate.** `GET /gate-avisos/{id}` responde `409 sin_gate` a una
   oportunidad sin Gate. Para un seguimiento de 48 h o una visita pedida desde el chat no hay ninguna forma de ver la
   ficha antes de llamar. Es un hueco de negocio: la llamada de confirmación de una visita se haría a ciegas.
3. **Corrección del tutor sobre su propia justificación.** La pre-decisión 6 del plan del listado dejó la lista sin
   email ni importes "porque viaja por email". Es incoherente con WF2, cuyo email a administración ya lleva la ficha
   completa (con email e importes) de cada caso con Gate. La razón válida es otra: la lista diaria **acumula** a
   muchos clientes en un solo mensaje, y su filtración expondría mucho más de golpe. Esa razón justifica no meter en
   la lista los datos personales completos; no justifica dejar a administración sin saber qué pidió cada cliente.

## 2. Decisiones

- **D26.1 — La lista diaria lleva un resumen de lo que pidió el cliente + un enlace a su ficha completa (opción C).**
  Cada elemento de los cinco apartados añade: `m2`, `nivel_acabados` e `incluye_cambios_estructurales` (de la
  solicitud, `leads.datos_estructurados`); en los apartados de visitas, además, el texto que escribió el cliente al
  pedirla (`visitas.texto_cliente`; en una visita acordada por teléfono tras el Gate es un texto fijo del sistema,
  decisión P2 del plan de `/gate-decisions`). Siguen fuera: email e importes (motivo: acumulación, sección 1.3).
  El enlace a la ficha lo construye n8n con el `oportunidad_id`, como hace WF2 con el formulario: no es un campo del
  endpoint.
  Rechazadas: **A**, la ficha completa dentro de la lista (el email diario se convierte en una base de datos de
  clientes en el buzón); **B**, solo el enlace (para saber qué quería cada cliente hay que abrir enlace por enlace).
  **[Nota 2026-10-10, tarde]** `texto_cliente` NO es la transcripción del cliente: es un resumen que escribe el
  Agente 2 (Gabi). Ver D26.7.
- **D26.2 — Ficha completa para CUALQUIER oportunidad, con o sin Gate.** Es el "endpoint de ficha" de D25.9,
  adelantado y con su contenido fijado. Solo lectura, llave de administración (`X-Gate-Secret`), vista en una página
  protegida de n8n. Contenido mínimo:
  - contacto: nombre, teléfono y email (de la solicitud, con su origen, como hoy `GET /gate-avisos`);
  - solicitud: tipo de reforma, m², nivel de acabados, cambios estructurales, fecha;
  - presupuesto: horquilla con IVA (la que vio el cliente) y sin IVA, si hubo Gate y su motivo, umbral aplicado;
  - estado actual de la oportunidad;
  - todas sus visitas: estado, fecha y texto del cliente;
  - resultados de llamadas anteriores, CON su informe;
  - historial: cambios de estado con su fecha.
  Preguntas abiertas para el plan (no decididas aquí): (a) el informe debe verse en la ficha, pero hoy esa misma
  lectura alimenta el email de WF2, donde no debe imprimirse; (b) si se muestran las fotos de la solicitud;
  (c) si se listan las otras oportunidades del mismo cliente.
  **[Nota 2026-10-10, tarde]** La pregunta (a) queda resuelta por D26.6 (la ficha es un endpoint nuevo; WF2 sigue
  con `GET /gate-avisos`). Historial: los cuatro servicios que cambian `oportunidades.estado` (estimate, visits,
  gate_decisions, followup) escriben también una fila en `logs` (comprobado en el código de `main`, 1af758a), y el
  alta queda en `created_at`: el historial se puede reconstruir sin tabla nueva, salvo cambios hechos a mano en la
  base de datos. El plan de 4a lo mide con datos reales (solo lectura).
- **D26.3 — Visitas: un solo bloque para administración, sin cambio de backend.** En los datos ya están unificadas:
  con Gate o sin Gate van a la misma tabla y la oportunidad pasa a `visita_agendada`; solo cambia quién la crea (el
  cliente desde el chat, `solicitada`; administración por teléfono, `confirmada`). El endpoint mantiene los apartados
  d) y e) porque piden acciones distintas (confirmar la cita / recordarla); el email de WF3 los muestra en un único
  bloque "Visitas", con cada una marcada "por confirmar" o "mañana".
- **D26.4 — Nuevo orden de bloques (sustituye D25.12 desde el bloque 3):** **[REVISADO por D26.6]**
  1. (hecho) regla de 48 h + `GET /llamadas-del-dia`; 2. (hecho) `POST /create-followup-task`;
  3. renombrado a "resultado de la llamada" sin cambios de comportamiento (D25.9);
  4a. ficha para cualquier oportunidad (D26.2) y resumen en la lista (D26.1), en DOS ramas (un endpoint por rama,
     CLAUDE.md): primero la ficha, después el resumen en la lista;
  4b. registro del resultado de todas las llamadas (seguimientos, confirmar o cancelar visitas) + formulario ampliado
     (D25.9–D25.11);
  5. WF3 en n8n, ya con el resumen, los enlaces a la ficha y las visitas en un solo bloque.
  WF3 no se construye antes de 4a: así no hay nada que rehacer en n8n.
- **D26.5 — Búsqueda de clientes por identificador: mejora posterior.** Una app donde administración meta un
  identificador del cliente y vea toda su ficha. Se hará cuando termine lo pendiente (N1 o al final de N0, según el
  tiempo). Identificador por decidir: el email es único en `clientes` (índice sobre `lower(email)`); el teléfono no es
  único, admite varios formatos y el chat lo guarda dentro de cada solicitud, así que una búsqueda por teléfono
  devolvería una lista. Cada consulta de la ficha debería dejar una línea en `logs`.

## 3. Qué cambia en lo ya hecho

- `GET /llamadas-del-dia` (en `main`) cambia su contrato en 4a: campos nuevos en cada elemento. Sus scripts comprueban
  las claves EXACTAS de la respuesta (CASO 2) y que no salga ningún email ni importe (N8): habrá que actualizarlos, y
  la minimización (sin email ni importes) se mantiene y se sigue probando.
- ~~`GET /gate-avisos` (renombrado en el bloque 3) deja de ser solo para casos con Gate en 4a.~~ **[Superado por
  D26.6]** `GET /gate-avisos` no se renombra ni cambia: sigue siendo el aviso de WF2. La ficha es un endpoint nuevo.
- Nada de lo integrado en `main` está mal: el hueco es de alcance, no un defecto del código.

## 4. Revisión (2026-10-10, tarde; aceptada por Gabi)

- **D26.6 — Orden de bloques, opción C: renombrar solo donde de todos modos hay que cambiar algo (revisa D26.4 y el
  modo de ejecución de D25.9).** Defecto de D26.4: el bloque 3 tocaba el formulario del Gate (PUBLICADO) y 4b lo
  volvía a tocar, y renombrar `GET /gate-avisos` para convertirlo en la ficha obligaba a una misma ruta a servir dos
  contratos (el email de WF2, sin informe, y la ficha, con informe).
  Nuevo orden:
  - **4a-1. Ficha para cualquier oportunidad: endpoint NUEVO con su nombre definitivo** (D26.2). Reutiliza las
    funciones de `aviso_gate_service` (sin copiarlas, como `condicion_seguimiento`). `GET /gate-avisos` se queda
    tal cual para WF2: WF2 no se toca.
  - **4a-2. Resumen en cada elemento de `GET /llamadas-del-dia`** (D26.1), en otra rama.
  - **4b. Registro del resultado de todas las llamadas: en UNA rama, DOS commits.** Primero renombrar sin cambios de
    comportamiento (mismos recuentos que hoy: se conserva la demostración que pedía D25.9); después ampliar
    (seguimientos, confirmar/cancelar visitas, D25.9–D25.11). La ruta antigua `POST /gate-decisions` queda como
    alias de la nueva hasta que el formulario esté actualizado, y se quita después. El formulario se toca una vez.
  - **5. WF3 en n8n**, después de 4a.
  Rechazadas: **A**, D26.4 tal cual (dos regresiones completas, el formulario publicado tocado dos veces y una ruta con
  dos contratos); **B**, ampliar sin renombrar (la tabla `decisiones_gate` guardaría llamadas que no son decisiones
  del Gate: nombre engañoso permanente).
  Coste aceptado: 4b es más grande y el alias es complejidad temporal.
- **D26.7 — `texto_cliente` es un resumen del Agente 2, con dos capas de protección (opción B).** Gabi: es un resumen
  informativo que escribe el Agente 2 en la conversación, no la transcripción del cliente. Hoy el esquema
  (`app/schemas/visits.py`) dice "lo que dijo el cliente, tal cual" y admite 1.000 caracteres (`MAX_TEXTO_VISITA`):
  hay que corregir ese comentario y el contrato. Protección: (1) el prompt del Agente 2 le prohíbe incluir datos
  personales o sensibles; (2) el backend limita la longitud (unos 300 caracteres, a fijar en el plan) y rechaza con
  422 los emails y teléfonos detectables, para que el agente reescriba el resumen. Riesgo residual documentado: una
  dirección o un dato de salud escritos en prosa no se detectan. Se implementa en el bloque del Agente 2.
  Ojo: el límite de 1.000 está también en la base de datos (CHECK `chk_visitas_texto_cliente_longitud`, 1..1000, en
  `docs/schema_actual.sql`): bajarlo exige una migración además del esquema Pydantic (defensa doble, como `m2`).
  Rechazadas: **A**, solo el prompt (probabilística: un fallo del modelo mete un teléfono en el email diario
  acumulado); **C**, quitar el texto de la lista (reabre el hueco de D26.1).
