# Flujos de n8n (exportados y saneados)

Copia de los flujos **publicados** en la instalación local de n8n 2.16.1 (Docker), exportados con "…" → Download
(primera exportación el 2026-10-10; formulario de llamadas, WF5 y WF2 actualizados el 2026-10-15). El repositorio
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

Las **credenciales no van en el JSON** (n8n solo guarda su nombre e id interno): al importar hay que
asignarlas otra vez. Ningún secreto (`X-Gate-Secret`, token del bot, contraseña del formulario, OAuth de
Gmail o de Google Calendar) está en estos archivos.

## Flujos

| Archivo | Para qué | Estado a 2026-10-15 |
|---|---|---|
| `WF_formulario_llamadas.json` | Formulario de administración para registrar el resultado de TODAS las llamadas (Gate, seguimiento y confirmación de visita) en `POST /resultados-llamada`; agenda Día → Hora con `GET /huecos-libres` (D32); copia cada visita al calendario llamando a WF5 (D33). Ruta `/form/resultado-llamada` | Publicado (15/10) |
| `WF5_calendario_visita.json` | Subflujo que copia una visita al calendario "Visitas técnicas — Reformas Amedida" (crear, actualizar o borrar; nunca duplica; fallos a WF4) | Publicado (un subflujo tiene que estar publicado para que otro flujo lo llame) |
| `WF2_aviso_gate.json` | Aviso del Gate a administración (email + Telegram) con enlace al formulario de llamadas; fallos de envío a WF4 | Publicado (enlace cambiado a `/form/resultado-llamada` el 15/10) |
| `WF4_aviso_tecnico.json` | Subflujo único de avisos técnicos (Telegram + email, sin datos del cliente) | Publicado |
| `WF4_errores_no_atrapados.json` | *Error Trigger* → WF4_aviso_tecnico | Flujo de errores (no necesita publicarse) |
| `WF_ficha_oportunidad.json` | Página HTML de la ficha completa (`GET /webhook/ficha?oportunidad_id=`) | Publicado |
| `WF_formulario_gate.json` | Formulario antiguo del Gate (Fase 1), sustituido por `WF_formulario_llamadas` | **Retirado** el 15/10 (despublicado, no borrado). Se conserva como histórico |

`chat_tipo_reforma` (chat + Agente 1) y `WF3_lista_diaria` no se exportan todavía.

## Cómo importar

n8n → **Create Workflow** → "…" → **Import from File** → elegir el JSON → asignar las credenciales en los nodos
marcados en rojo → sustituir los marcadores de la tabla por los valores reales. Orden recomendado: WF4 →
WF5 → WF2 → formulario (cada uno llama a los anteriores; al importarlos, revisar que los nodos *Execute Workflow*
apuntan al flujo correcto de la instalación nueva).

- **WF5:** credencial OAuth de Google Calendar en los 4 nodos HTTP; el ID del calendario en `preparar_evento`
  (`CALENDARIO_ID`) y el dominio en `FICHA_URL`. Publicarlo.
- **Formulario de llamadas:** credencial de autenticación básica en `On form submission` y "Header Auth"
  (`X-Gate-Secret`) en los nodos HTTP; la ruta del formulario es `resultado-llamada` (Options → Path).
- En `WF2_aviso_gate`, `WF_ficha_oportunidad` y `WF_formulario_llamadas`, elegir `WF4_errores_no_atrapados` en
  Settings → Error workflow → Save → Publish.
