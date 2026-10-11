# Reformas Integrales Amedida — Decisiones N0
## D27 — WF4 (avisos técnicos), conexión con WF2, página de la ficha y primera exportación de los flujos

**Sesión:** 2026-10-10. **Cerrado el 2026-10-10** (decisiones aceptadas por Gabi: "las decisiones tomadas me parecen
correctas").
**Base:** D24.6 (avisos de error: pantalla al operador + WF3 como red + WF4 para técnicos), D24.10 (cierre de
sesión con export de JSON), D26.6 (el formulario del Gate se toca una sola vez, en 4b), D26.8 (página de la ficha).
**Defecto que cierra:** prueba 17 del registro (un fallo de envío de WF2 era silencioso).

---

## D27.1 — Qué se construye en n8n mientras Claude Code trabaja (Regla de Tres)

WF3 no puede ser (D26.4/D26.6: no antes de 4a; además abriría seguimientos sobre los 8 casos reservados, D25.16).
- **A. WF4 (avisos técnicos).** Arregla un defecto demostrado en un flujo publicado (prueba 17); no escribe en la
  base de datos; 0 RAM extra; observabilidad = requisito de una automatización empresarial. **Elegida.**
- B. Exportar los JSON saneados: obligatorio, pero sin valor para la empresa; se hace al cerrar la sesión.
- C. Mensaje del router para `perdida`: impacto bajo, en el chat (no publicado).

## D27.2 — Arquitectura de WF4: dos piezas (opción B)

Hecho de partida (documentación oficial de n8n): un flujo de errores empieza por el *Error Trigger*, que solo salta
con errores **no atrapados** y **no salta en ejecuciones manuales**. Los envíos de WF2 estaban atrapados
(*On Error → Continue*), así que el *Error Trigger* no los veía.
- A. Solo un *Error Trigger*: no ve el fallo que importa. Rechazada.
- **B. `WF4_aviso_tecnico`** (subflujo: *When Executed by Another Workflow* → `construir_aviso_tecnico` → Telegram +
  Gmail; único sitio que sabe cómo se escribe y adónde va un aviso técnico) **+ `WF4_errores_no_atrapados`**
  (*Error Trigger* → `normalizar_error` → llama a `WF4_aviso_tecnico`). Los flujos llaman a `WF4_aviso_tecnico` en
  sus ramas de error atrapado. **Elegida** ("un solo buzón de incidencias").
- C. Cada flujo manda su propio Telegram técnico: formato y credencial repetidos; cambiar de canal obliga a tocarlos
  todos. Rechazada.
Entradas de `WF4_aviso_tecnico`: `flujo`, `nodo`, `mensaje_error`, `oportunidad_id` (número, puede faltar),
`ejecucion_id`. **Sin datos personales del cliente** (nombre, teléfono, email): Telegram y Gmail son servicios
externos (RGPD, minimización). Todo valor pasa por `esc()` (Telegram en modo HTML, email en HTML).

## D27.3 — Canales: grupo técnico de Telegram + email (propuesta de Gabi)

- Telegram: supergrupo **"Reformas Amedida - Tecnico"** con el **mismo bot** (`ReformasAmedida_bot`), distinto del
  chat de administración. Administración no debe recibir avisos que no sabe resolver; separarlo demuestra D24.6.
  Al convertir el grupo en supergrupo, Telegram cambia su `chat.id` (`migrate_to_chat_id`): se usa el nuevo.
- Email técnico además del Telegram (Gabi). Motivo de peso: **dos proveedores que fallan por causas distintas**.
  Ejemplo documentado por Google: una app OAuth en modo *Testing* recibe *refresh tokens* que **caducan a los 7
  días**; si pasa, fallan a la vez el email de WF2 y el de WF4, y solo el Telegram técnico avisa.
- Pendiente menor: el nodo `enviar_email_tecnico` envía a la dirección de administración sin `+tecnico` (el tutor
  dio por hecho lo contrario y se corrigió). Cambiar a la dirección `+tecnico` y crear un filtro en Gmail.
- Riesgo residual (limitación de N0): si fallan a la vez Telegram y Gmail, nadie recibe el aviso.

## D27.4 — Dónde se conecta WF4

- **WF2, hoy.** `enviar_email_administracion` y `enviar_telegram_administracion` pasan de *Continue (regular
  output)* a **Continue (using error output)**; cada salida de error llama a `WF4_aviso_tecnico`
  (`avisar_fallo_email`, `avisar_fallo_telegram`, *Wait for sub-workflow completion* activado). En la salida de error,
  `error` llega como TEXTO; la expresión robusta es
  `{{ $json.error?.message ?? String($json.error ?? 'Error sin mensaje') }}`. WF2 tiene `WF4_errores_no_atrapados`
  como *Error workflow* (cubre, por ejemplo, `leer_aviso_gate` sin backend tras sus reintentos).
- **Formulario del Gate, en 4b** (no hoy): D26.6 decide tocarlo una sola vez. No falla en silencio: sus errores
  técnicos ya dicen en pantalla "avisa al responsable técnico".
- **Página de la ficha** (D26.8): llama a WF4 en los fallos técnicos (ver D27.6).
- Sin verificar: que el *Error Trigger* salte con WF2 en una ejecución automática real (exige una conversación de
  chat con uvicorn apagado: escribe un lead real y gasta OpenAI → responder antes las 4 preguntas del handoff).

## D27.5 — Primera exportación de los flujos al repositorio público (Regla de Tres)

- **A. Exportar con saneado por marcadores.** Script que sustituye dominio de ngrok, `chatId` de administración y del
  grupo técnico y email por marcadores; borra `webhookId`, `meta.instanceId` y `versionId`; `active=false`;
  comprueba `pinData` vacío y 0 restos. Las credenciales no viajan en el JSON. **Elegida:** cumple D24.10, el
  portfolio muestra los flujos reales y no expone nada que permita actuar sobre la instalación.
- B. No exportar (solo en el Proyecto): incumple D24.10 y el tribunal y los clientes no ven el trabajo de n8n.
- C. Hacer el repositorio privado: el portfolio pierde visibilidad, que es el objetivo del TFM.
Hecho el 2026-10-10: rama `docs/n8n-export-2026-10-10` (`a9cdc5b`, desde `main` = `ea96f39`), carpeta `n8n/` con
5 flujos y `README.md`. Pendiente de integrar (ver handoff: después de la rama del 4a-2).

## D27.6 — Errores de la página de la ficha

| Respuesta de `GET /oportunidades/{id}/ficha` | Página (código HTTP) | ¿Aviso técnico (WF4)? |
|---|---|---|
| Número del enlace no válido (no llega al backend) | "Enlace no válido" (400) | No |
| Sin conexión (uvicorn apagado o caído) | "Sistema no disponible" (503) | Sí |
| 404 | "Oportunidad no encontrada" (404) | No (es un error del enlace) |
| 401 | "Error de configuración" (500) | Sí (llave `X-Gate-Secret`) |
| Cualquier otro distinto de 200 | "Error inesperado" (500) | Sí |
Las páginas de error llevan la cabecera "Aviso" y el título concreto una sola vez en el cuerpo. El aviso técnico se
envía DESPUÉS de responder al navegador (administración no espera a Telegram ni a Gmail).

## D27.7 — Incidente: token del bot expuesto en el chat

El token de `ReformasAmedida_bot` se pegó en la conversación. **Cerrado:** revocado con BotFather (`/revoke`) y
credencial de Telegram actualizada en n8n. Regla (ya en vigor): ningún token, contraseña ni `.env` en el chat.

---

**[Nota 2026-10-11, tutor: evidencia recuperada de *Executions* de n8n]**
- D27.6, fila "Sin conexión": verificada por el camino DIRECTO el 2026-10-10 a las 23:16 (prueba 61 del registro):
  ficha ejecución 811 *Succeeded* (3,292 s), página "Sistema no disponible", `WF4_aviso_tecnico` ejecución 812
  (2,338 s), ninguna ejecución de `WF4_errores_no_atrapados`; aviso con nodo `leer_ficha` y "Oportunidad: 248".
  Los avisos de las 21:37, 21:38 y 21:40 (ejecuciones 800, 803 y 807) NO eran de esta fila: el nodo
  `construir_pagina_ficha` falló con `SyntaxError: Unexpected end of input` (código del nodo pegado incompleto) y
  los avisos llegaron por `WF4_errores_no_atrapados` (801/802, 804/805, 808/809), que pone "Oportunidad: —" porque
  el *Error Trigger* no conoce el número. La red de seguridad de D27.2 queda así demostrada en una ejecución
  automática real de la ficha (no de WF2: lo de D27.4 "sin verificar" sigue abierto para WF2).
- D27.5: la rama se rebasó sobre `main` = `c1ba356` el 2026-10-11 (después de integrar 4a-2) y lleva también este
  documento y la sección 5 de D26.
