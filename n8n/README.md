# Flujos de n8n (exportados y saneados)

Copia de los flujos **publicados** en la instalación local de n8n 2.16.1 (Docker), exportados con "…" → Download
(primera exportación el 2026-10-10; formulario de llamadas, WF5 y WF2 actualizados el 2026-10-15; chat con el
Agente 2, WF6, WF5 y formulario de llamadas actualizados el 2026-10-19; chat, WF2 y ficha actualizados el
2026-10-20 con el resumen de la solicitud, D28.1). El repositorio
es **público**, así que cada archivo pasa por un saneado antes de subirse (decisión D27.5, Regla de Tres en
`claude/Decisiones_D27_WF4_Avisos_Tecnicos_y_Pagina_Ficha.md` del Proyecto).

## Qué se cambia al sanear

| Dato real | Se sustituye por | Motivo |
|---|---|---|
| Dominio fijo de ngrok | `TU-DOMINIO.ngrok-free.dev` | Es la puerta pública de la instalación local |
| `chatId` del chat de administración | `CHAT_ID_ADMINISTRACION` | Identificador de una persona en Telegram |
| `chatId` del supergrupo técnico | `CHAT_ID_TECNICO` | Identificador de un grupo privado |
| Email de administración y técnico | `admin@example.com` | Dato personal |
| ID del calendario de Google (WF5) | `PEGA_AQUI_ID_DEL_CALENDARIO` | Identifica el calendario interno de visitas (D33). Si no se sustituye, WF5 para con el error "Falta el ID del calendario" sin tocar Google |
| `webhookId` de cada nodo | (se borra la clave) | n8n crea otro al importar |
| `meta.instanceId`, `versionId` | (se borran) | Identifican la instalación |
| `active` | `false` | Se importa desactivado |
| `pinData` (datos fijados 📌) | `{}` | Solo servían para probar en el editor |

Las **credenciales no van en el JSON** (n8n solo guarda su nombre e id interno): al importar hay que
asignarlas otra vez. Ningún secreto (`X-Gate-Secret`, token del bot, contraseña del formulario, OAuth de
Gmail o de Google Calendar) está en estos archivos.

## Flujos

| Archivo | Para qué | Estado a 2026-10-20 |
|---|---|---|
| `WF3_lista_diaria.json` | Lista diaria de llamadas (L-V 8:00 y manual): lee `GET /llamadas-del-dia`, abre cada seguimiento por abrir con `POST /create-followup-task` (paso 2; un 409 es una carrera normal, los fallos técnicos van a WF4 y el email sale igual), vuelve a leer la lista y envía a administración un email con "Última llamada" (7 días o más desde el presupuesto, D30/D36.1: cada caso aparece una sola vez) | Publicado (19/10) |
| `chat_tipo_reforma.json` | Chat público del cliente. Un router (`router_estado_sesion`, 5 salidas) decide según `GET /leads/session/{id}`: Agente 1 (captura de datos y presupuesto, `POST /leads` + `calculate_estimate` por MCP), Agente 2 (asesor, D34-D35) o mensajes fijos (Gate, solicitud cerrada, sin presupuesto). Prompts en `prompts/`. Desde el 20/10 el Agente 1 escribe además `resumen_solicitud` (D28.1): la tool `guardar_datos_reforma` construye el cuerpo con `JSON.stringify` (C-2: unas comillas en el nombre ya no rompen el JSON), con la regla de privacidad D37.8 (nunca salud, edad ni familia) y la corrección D37.9 (no repite el sí/no estructural, pero sí describe lo que el cliente quiere lograr); pruebas 112-113 | Publicado (20/10) |
| `WF6_solicitar_visita.json` | Subflujo-tool del Agente 2: `POST /visits` (solicitud del cliente), copia al calendario como pendiente 🟣 llamando a WF5 y devuelve al agente una respuesta mínima, sin ids ni datos del cliente | Publicado (subflujo) |
| `WF_formulario_llamadas.json` | Formulario de administración para registrar el resultado de TODAS las llamadas (Gate, seguimiento y confirmación de visita) en `POST /resultados-llamada`; agenda Día → Hora con `GET /huecos-libres` (D32); copia cada visita al calendario llamando a WF5 (D33). Ruta `/form/resultado-llamada` | Publicado (19/10: texto "actualizada" al confirmar la hora pedida, F-1) |
| `WF5_calendario_visita.json` | Subflujo que copia una visita al calendario "Visitas técnicas — Reformas Amedida" (crear, actualizar o borrar; nunca duplica; pendiente 🟣 morado `colorId 3`, confirmada ✅ verde `colorId 10`, D35.3; fallos a WF4) | Publicado (un subflujo tiene que estar publicado para que otro flujo lo llame) |
| `WF2_aviso_gate.json` | Aviso del Gate a administración (email + Telegram) con enlace al formulario de llamadas; fallos de envío a WF4. Desde el 20/10, sección "Resumen del asistente" en el EMAIL, nunca en Telegram (D37.10: minimización), etiquetada "según el asistente; confírmalo en la llamada" | Publicado (20/10; credencial de Gmail renovada) |
| `WF4_aviso_tecnico.json` | Subflujo único de avisos técnicos (Telegram + email, sin datos del cliente) | Publicado |
| `WF4_errores_no_atrapados.json` | *Error Trigger* → WF4_aviso_tecnico | Flujo de errores (no necesita publicarse) |
| `WF_ficha_oportunidad.json` | Página HTML de la ficha completa (`GET /webhook/ficha?oportunidad_id=`). Desde el 20/10, sección "Resumen del asistente" después de "Solicitud" ("Sin resumen del asistente." si es null: lead antiguo o resumen descartado por el filtro); prueba 114 | Publicado (20/10) |
| `WF_formulario_gate.json` | Formulario antiguo del Gate (Fase 1), sustituido por `WF_formulario_llamadas` | **Retirado** el 15/10 (despublicado, no borrado). Se conserva como histórico |


## Cómo importar

n8n → **Create Workflow** → "…" → **Import from File** → elegir el JSON → asignar las credenciales en los nodos
marcados en rojo → sustituir los marcadores de la tabla por los valores reales. Orden recomendado: WF4 →
WF5 → WF6 → WF2 → formulario → chat → WF3 (cada uno llama a los anteriores; al importarlos, revisar que los nodos *Execute Workflow*
apuntan al flujo correcto de la instalación nueva).

- **WF5:** credencial OAuth de Google Calendar en los 4 nodos HTTP; el ID del calendario en `preparar_evento`
  (`CALENDARIO_ID`) y el dominio en `FICHA_URL`. Publicarlo.
- **Formulario de llamadas:** credencial de autenticación básica en `On form submission` y "Header Auth"
  (`X-Gate-Secret`) en los nodos HTTP; la ruta del formulario es `resultado-llamada` (Options → Path).
- **WF3:** "Header Auth" (`X-Gate-Secret`) en los tres nodos HTTP, Gmail OAuth2 en el envío, el dominio en `DOMINIO`
  (`construir_email_lista`) y el email de administración en `enviar_lista_administracion`.
- **WF6:** "Header Auth" (`X-Webhook-Secret`) en `pedir_visita`. Publicarlo.
- **Chat:** credenciales de OpenAI (los dos modelos), "Bearer Auth" del servidor MCP (`MCP Client` y `reglas_y_huecos`) y
  `X-Webhook-Secret` (`guardar_datos_reforma`, `Consultar_estado_sesion`); en la tool `solicitar_visita`, elegir WF6.
  El system message del nodo `AI Agent` es `prompts/agente1_captura.md` (idéntico, más el salto de línea final); si se cambia en un sitio, se cambia en el otro.
- En `WF2_aviso_gate`, `WF_ficha_oportunidad`, `WF_formulario_llamadas`, `WF3`, `WF5`, `WF6` y `chat_tipo_reforma`, elegir `WF4_errores_no_atrapados` en
  Settings → Error workflow → Save → Publish.
