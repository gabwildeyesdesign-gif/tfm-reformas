# Reformas Integrales Amedida — Contexto del proyecto (TFM)

Sistema de hiperautomatización para una empresa ficticia de reformas.
TFM del Máster en Agentes de IA e Hiperautomatización (EBIS). Alcance
actual: Nivel N0 (núcleo mínimo aprobable).

## Cómo trabajamos en este proyecto

- Bloques pequeños y revisables. No implementar de golpe varios
  endpoints ni varias tools a la vez.
- Explica tu razonamiento ANTES de escribir código, y cada bloque de
  código DESPUÉS, línea por línea — quien lee esto está aprendiendo
  Python desde cero.
- "Verificado" significa ejecución real (arrancar el servidor, hacer la
  petición real, ver la salida real) — nunca solo lectura de código.
- Si algo se contradice con lo que dice este archivo, dilo
  explícitamente antes de asumir nada; no lo reinterpretes en silencio.

## REGLA CRÍTICA: nunca arrancar uvicorn con --reload

En Windows, --reload cuelga el apagado del servidor cuando corre en un
proceso lanzado sin consola interactiva (confirmado leyendo el código
fuente de uvicorn/supervisors/basereload.py: el reloader manda
CTRL_C_EVENT, que solo llega a procesos con consola real). Arrancar
SIEMPRE así: uvicorn app.main:app (sin --reload).

## Stack y por qué

- n8n — orquestación (webhooks, sub-workflows, reintentos, Telegram
  para el Gate HITL)
- FastAPI + Python — lógica de negocio determinista
- fastmcp (paquete standalone, NO el SDK oficial "mcp" — desde agosto
  2026 el SDK oficial renombró FastMCP a MCPServer) — servidor MCP
  montado DENTRO del mismo proceso FastAPI (un solo proceso, no dos —
  decisión por RAM de 8GB y por no duplicar pools de conexión contra el
  Session pooler de Supabase)
- PostgreSQL / Supabase — Session pooler; acceso SÍNCRONO con
  psycopg2.pool.ThreadedConnectionPool (DB_POOL_MIN=2, DB_POOL_MAX=10)
  — decisión consciente de escala, no desconocimiento; plan de
  migración a async documentado si hiciera falta
- UiPath — OCR de facturas, solo en N2 (condicionado)

Versiones instaladas (verificadas en el venv): Python 3.12.10,
fastmcp==4.0.3, mcp==2.2.0 (dependencia de fastmcp), fastapi==0.141.1,
pydantic==2.13.5, starlette==1.6.0, psycopg2-binary==2.9.13.

## Estructura de carpetas (regla de disciplina)

app/main.py, app/api/, app/mcp_server/, app/services/, app/db/,
app/schemas/, app/config.py, scripts/ (verificación, no producción).

services/ NUNCA importa fastapi ni fastmcp — es la única capa con
lógica de negocio real. api/ y mcp_server/server.py son adaptadores
delgados que llaman siempre a las mismas funciones de services/.

## Dos puertas: REST vs MCP — DISEÑO PREVISTO, AÚN NO IMPLEMENTADO

Nada de esta sección existe todavía en el código: los archivos de
app/api/ y app/services/ contienen solo un docstring de una línea, y el
servidor MCP no tiene ninguna tool registrada (list_tools() devuelve []).
Es la especificación a implementar, no el estado actual.

calculate-estimate tiene las dos (MCP en producción, REST para
pruebas). get_business_rules() y request_missing_information() son
SOLO MCP. leads, gate-decisions, visits, create-followup-task son
SOLO REST.

## Gotchas ya resueltos (no los reinvestigues)

- Supavisor (Session pooler de Supabase) sustituye application_name
  por el suyo propio — filtrar por él en pg_stat_activity siempre da 0
  filas a través del pooler. No es un bug nuestro.
- El lifespan de FastAPI y el de fastmcp deben combinarse
  (combined_lifespan anidado), nunca uno reemplazar al otro — FastAPI
  solo acepta un lifespan.
- DB_POOL_MIN y DB_POOL_MAX NO están en .env: el .env solo contiene
  DATABASE_URL. Los valores 2 y 10 son los por defecto escritos en
  app/config.py. Para cambiarlos sin tocar código, hay que añadir
  primero esas variables al .env.

## Estado actual / pendiente

Hecho y verificado con ejecución real: scaffolding, servidor MCP
montado, pool de Postgres, /health y /health/db, apagado ordenado.
Pendiente: servicios de negocio (services/), endpoints reales,
concurrencia del Session pooler bajo carga.

## Para el razonamiento completo de cada decisión

Ver docs/ en este repo — este archivo es un resumen de referencia
rápida, no sustituye esa documentación.

PENDIENTE: la carpeta docs/ todavía no existe en el repositorio, y el
"plan de migración a async" citado en la sección de stack tampoco está
escrito en ningún sitio. Ambos están por crear.
