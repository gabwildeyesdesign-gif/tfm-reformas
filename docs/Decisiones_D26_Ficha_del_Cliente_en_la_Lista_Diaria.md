# Reformas Integrales Amedida — Decisiones N0
## D26 — Ficha del cliente en la lista diaria, visitas en un solo bloque y nuevo orden de bloques

**Sesión:** 2026-10-10. **Cerrado el 2026-10-10** (decisión de Gabi, opción C).
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
- **D26.3 — Visitas: un solo bloque para administración, sin cambio de backend.** En los datos ya están unificadas:
  con Gate o sin Gate van a la misma tabla y la oportunidad pasa a `visita_agendada`; solo cambia quién la crea (el
  cliente desde el chat, `solicitada`; administración por teléfono, `confirmada`). El endpoint mantiene los apartados
  d) y e) porque piden acciones distintas (confirmar la cita / recordarla); el email de WF3 los muestra en un único
  bloque "Visitas", con cada una marcada "por confirmar" o "mañana".
- **D26.4 — Nuevo orden de bloques (sustituye D25.12 desde el bloque 3):**
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
- `GET /gate-avisos` (renombrado en el bloque 3) deja de ser solo para casos con Gate en 4a. Lo usan dos workflows
  PUBLICADOS (WF2 y el formulario del Gate): el plan tiene que decir cómo se mantienen funcionando durante el cambio.
- Nada de lo integrado en `main` está mal: el hueco es de alcance, no un defecto del código.
