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
- El terminal real de trabajo es Git Bash sobre Windows, no PowerShell.
  Las rutas se escriben con barra normal (app/db/connection.py) y el
  intérprete siempre es ./venv/Scripts/python.exe. Cuando un comando
  lance un proceso hijo de Python y capture su salida, se le pone
  delante PYTHONIOENCODING=utf-8, o la primera tilde o eñe lo revienta
  (el porqué está en el gotcha de cp1252, más abajo). Esto no anula la
  nota sobre curl.exe y el alias curl: esa sigue valiendo para las
  pruebas que Gabi hace a mano en una consola de PowerShell.
- Un script de verificación que espera una excepción tiene que marcar
  FALLO explícito cuando NO sale ninguna. Se hace con la rama else: del
  try, con un mensaje del tipo "la excepción se ha tragado". El motivo
  es un caso real (D16): una guarda mal indentada dejó el raise dentro
  del if y la excepción se suprimía en silencio; la comprobación "no es
  InterfaceError" daba [OK] sobre ese fallo, porque si no sale nada,
  tampoco sale un InterfaceError. El fallo silencioso es peor que el
  error que se venía a arreglar, y solo esa rama else lo detecta.
- La prueba en negativo se ejecuta contra TODAS las versiones
  defectuosas conocidas, no solo contra la original. No basta con
  revertir el arreglo (git stash) y ver que falla: si durante el
  trabajo apareció otra versión rota —por ejemplo un intento previo mal
  indentado que ya no está en disco—, hay que recrearla en una copia,
  ejecutar el script contra ella y enseñar la salida literal, antes de
  restaurar la buena y confirmarlo con git diff. Cada versión rota
  demuestra que el script detecta un modo de fallo distinto.
- Cuando un arreglo depende de la ESTRUCTURA del código y no solo de su
  sintaxis, se verifica con ast.parse y se enseña la salida. py_compile
  no basta: devuelve exit code 0 sobre código que compila pero que anida
  las sentencias de forma equivocada. Es exactamente lo que ocurrió en
  D16, con el raise atrapado dentro del if porque Python ignora los
  comentarios al calcular los niveles de indentación.

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

## Dos puertas: REST vs MCP — IMPLEMENTADO SOLO PARCIALMENTE

Estado real del código a día de hoy: POST /leads y POST
/calculate-estimate están implementados y verificados por HTTP real
(dos include_router en main.py). calculate-estimate tiene además su
tool MCP calculate_estimate (list_tools() devuelve solo esa), con /mcp
autenticado por Bearer MCP_SECRET. gate-decisions, visits y
create-followup-task siguen siendo un docstring de una línea.
Lo que sigue es la especificación completa, no el estado actual.

calculate-estimate tiene las dos (MCP en producción, REST para
pruebas). get_business_rules() y request_missing_information() son
SOLO MCP. leads, gate-decisions, visits, create-followup-task son
SOLO REST.

## Gotchas ya resueltos (no los reinvestigues)

- Supavisor (Session pooler de Supabase) sustituye application_name
  por el suyo propio — filtrar por él en pg_stat_activity siempre da 0
  filas a través del pooler. No es un bug nuestro.
- Supavisor ignora TODOS los parámetros de arranque, no solo
  application_name: options="-c statement_timeout=..." tampoco llega
  (comprobado: seguía en '2min'). Por eso statement_timeout se fija con
  un SET + commit en ConexionConTimeout (connection_factory del pool,
  app/db/connection.py). Además, en modo Session reutiliza backends de
  Postgres entre clientes: el pid NO identifica a un cliente. Para
  aislar las conexiones de un proceso en pg_stat_activity, una lista
  blanca por pid previo no basta; hay que combinarla con la actividad
  posterior (state_change). Detalle en
  docs/TFM_Resumen_Sesion_Timeouts_DB_N0.txt, sección 6.
- Cada préstamo del pool (get_db_connection y
  get_transactional_connection) valida la conexión antes de entregarla:
  SELECT 1 + rollback, y si está muerta la descarta ([AVISO] en stderr)
  hasta encontrar una viva, con tope DB_POOL_MAX. Cuesta ~120-190 ms por
  préstamo según la red. Los tiempos límite (connect_timeout, keepalives,
  statement_timeout 30 s) son constantes DB_* de app/config.py,
  sobrescribibles por .env. OJO: DB_KEEPALIVES_COUNT no tiene efecto en
  Windows, y una conexión medio abierta puede seguir colgando el SELECT 1
  minutos (tcp_user_timeout no existe en Windows).
- El lifespan de FastAPI y el de fastmcp deben combinarse
  (combined_lifespan anidado), nunca uno reemplazar al otro — FastAPI
  solo acepta un lifespan.
- DB_POOL_MIN y DB_POOL_MAX NO están en .env: el .env contiene
  DATABASE_URL y WEBHOOK_SECRET. Los valores 2 y 10 son los por defecto
  escritos en app/config.py. Para cambiarlos sin tocar código, hay que
  añadir primero esas variables al .env.
- POST /leads EXIGE la cabecera X-Webhook-Secret con el valor de
  WEBHOOK_SECRET del .env. Sin ella, o con otro valor, responde 401
  (antes incluso de validar el cuerpo, así que un 401 no dice nada del
  JSON). Probarlo a mano en PowerShell (curl.exe, no curl: en Windows
  PowerShell 5.1 "curl" es un alias de Invoke-WebRequest):
      curl.exe -X POST http://127.0.0.1:8000/leads -H "Content-Type: application/json" -H "X-Webhook-Secret: <valor del .env>" -d "@cuerpo.json"
  En /docs, el botón "Authorize" permite pegar el secreto una vez. n8n
  debe enviarlo con una credencial "Header Auth" en el nodo HTTP.
- MCP_SECRET (distinto de WEBHOOK_SECRET) también es obligatorio para
  ARRANCAR: si falta o está vacío, uvicorn se detiene al importar
  app/mcp_server/server.py. /mcp exige "Authorization: Bearer
  <MCP_SECRET>" (401 sin él). Se usa una clase propia
  (VerificadorSecretoMCP, compare_digest), NO StaticTokenVerifier de
  fastmcp (comparación no constante, "no usar en producción").
  scripts/check_mcp_connection.py ya envía el token.
- El precio que se devuelve al cliente lleva IVA INCLUIDO (D14):
  EstimateResponse trae importe_min_con_iva / importe_max_con_iva, y esas
  son también las columnas de presupuestos (renombradas en paso7), junto
  con iva_pct_aplicado. Se guarda el importe YA con IVA para que un
  presupuesto no cambie de precio si alguien edita
  reglas_negocio.iva_estandar_pct (21 %). OJO: el Gate se evalua contra el
  importe SIN IVA, porque el umbral mide riesgo comercial y el IVA no se
  queda en la empresa; hay un TODO en el servicio con la pregunta abierta.
- oportunidades.estado admite OCHO valores desde paso7: se añadió
  'seguimiento_pendiente' (D13). La columna
  oportunidades.fecha_ultimo_contacto existe pero está NULL en todas las
  filas: la escribirá el barrido de seguimiento, que aún no existe.
- El umbral del Gate NO es un valor global: desde D9 (2026-09-20) vive en
  la tabla umbrales_gate, una fila por tipo_reforma (bano y cocina 13.000 €,
  integral_vivienda y parcial_acabados 10.000 €, los cuatro ya cerrados). La fila
  reglas_negocio.umbral_aprobacion_manual sigue existiendo pero está marcada
  como OBSOLETA y nadie la lee: editarla no tiene ningún efecto.
  El proyecto tiene por tanto 9 tablas, no 8.
- m2 tiene tope: más de 0 y hasta 500 (MAX_M2_LEAD en app/schemas/common.py,
  y CHECK chk_leads_m2_rango en leads, defensa doble como en D5). Es Decimal,
  no float. OJO: el CHECK va sobre la expresión (datos_estructurados ->>
  'm2')::numeric porque NO existe ninguna columna m2 (D3: el dato vive en el
  JSONB). Garantiza el RANGO, no la PRESENCIA: si la clave falta, ->> da NULL
  y un CHECK solo rechaza lo FALSO, así que pasa. La presencia la garantiza
  Pydantic en el único camino real de escritura, POST /leads.
- m2 y cualquier número dentro de un JSONB llegan a Python como float si
  se lee el JSONB entero. Para Decimal exacto, extraerlo en SQL:
  (datos_estructurados ->> 'm2')::numeric.
- En scripts que prueban varias violaciones de restricción dentro de UNA
  transacción, cada una va en su propio SAVEPOINT (ROLLBACK TO +
  RELEASE); si no, la primera aborta la transacción y las demás dan
  InFailedSqlTransaction. Patrón: scripts/check_migracion_calculate_estimate.py.
- WEBHOOK_SECRET es obligatorio para ARRANCAR el servidor: si falta o
  solo tiene espacios, uvicorn se detiene al importar
  app/api/security.py, antes de abrir el puerto. Los scripts que solo
  importan app.config no lo necesitan. Plantilla en .env.example.
- En Windows, leer como UTF-8 el stderr de un subproceso de Python
  revienta con UnicodeDecodeError en cuanto el hijo escribe una tilde
  o una ñ. Aparece en scripts que lanzan un subproceso (por ejemplo,
  uvicorn con subprocess.run) y capturan su salida con
  capture_output=True y encoding="utf-8": al ir a una tubería y no a
  una consola, el hijo escribe en la codificación del sistema, cp1252,
  y no en UTF-8 (reproducido de forma aislada: el hijo informa de
  sys.stderr.encoding = cp1252 y manda "á" como el byte 0xe1, justo el
  byte del error original en el caso 6 de
  scripts/check_webhook_auth_http.py). Solución: pasar
  PYTHONIOENCODING=utf-8 en el entorno del hijo, por ejemplo
  env=dict(os.environ, PYTHONIOENCODING="utf-8").

## Estado actual / pendiente

> **Integrado en `main` el 2026-09-19.** La rama `feat/n0-schemas-leads`
> (esquemas y POST /leads, autenticación del webhook, RLS, notas
> técnicas y .gitattributes) se revisó y se fusionó por fast-forward
> (`git merge --ff-only`, sin merge commit) tras pasar la verificación
> final: check_webhook_auth_http 15/15, check_leads_endpoint_http 12/12,
> check_schemas 34/34 y check_rls_estado sin errores.
>
> **Integrado en `main` el 2026-09-20.** La rama
> `feat/n0-calculate-estimate` (POST /calculate-estimate por REST y como
> tool MCP, autenticación de /mcp, migraciones paso3 a paso6, umbral del
> Gate por categoría y tope de m2) se fusionó por fast-forward
> (`git merge --ff-only`, sin merge commit) tras pasar la verificación
> final sobre `main`: check_schemas 37/37, check_webhook_auth_http 15/15,
> check_leads_endpoint_http 12/12, check_estimate_service 107/107,
> check_calculate_estimate_http 21/21, check_mcp_calculate_estimate
> 19/19, check_migracion_m2_leads 23/23, check_exception_handler 32/32 y
> check_rls_estado sin errores.
>
> **Integrado en `main` el 2026-09-20 (segundo merge del día).** La rama
> `feat/n0-iva-y-seguimiento` (D14: el precio mostrado lleva IVA
> incluido; D13: base de datos preparada para el seguimiento a 48 h) se
> fusionó por fast-forward tras pasar la verificación final sobre `main`:
> check_estimate_service 129/129, check_calculate_estimate_http 24/24,
> check_mcp_calculate_estimate 19/19, check_migracion_iva_y_seguimiento
> 17/17, y el resto de la suite sin fallos.
>
> **Bloque actual: el cierre determinista posterior a calculate_estimate
> en n8n** (D17: Code node, rama False, casos de Gate).
>
> **Después, el barrido de seguimiento de D13** (el flujo de n8n
> que consulta oportunidades.fecha_ultimo_contacto y marca
> 'seguimiento_pendiente'), todavía sin empezar. Luego, POST
> /gate-decisions. Se aplica la misma regla de siempre: **no hacer merge
> a `main` sin la confirmación explícita de Gabi**, y siempre con
> `--ff-only`.

Hecho y verificado con ejecución real: scaffolding, servidor MCP
montado, pool de Postgres, /health y /health/db, apagado ordenado,
esquemas Pydantic de leads, get_transactional_connection() (commit /
rollback automáticos), CHECK en oportunidades.tipo_reforma, y POST
/leads completo de punta a punta (HTTP real: 201 con datos válidos, 422
sin tocar la base de datos con datos inválidos).

POST /calculate-estimate (REST + tool MCP) implementado, verificado e
INTEGRADO EN main: migración paso3 (estado
'pendiente_aprobacion' + UNIQUE presupuestos.oportunidad_id, D8),
check_migracion 14/14, check_estimate_service 83/83,
check_calculate_estimate_http 20/20, check_mcp_calculate_estimate 19/19.
Detalle completo: docs/EndPoint_Calculate_estimate_final.txt.

Umbral del Gate por categoria (D9) implementado, verificado e integrado
el 2026-09-20: migracion paso4 (tabla umbrales_gate),
check_migracion_umbrales_gate 17/17, check_estimate_service 107/107,
check_calculate_estimate_http 21/21, check_mcp_calculate_estimate 19/19.
Detalle: docs/Umbral_Gate_por_Categoria_D9_final.txt. El umbral de
parcial_acabados (10.000 EUR) quedo CERRADO el 2026-09-20 (paso5): ya no
hay ningun umbral provisional. Defecto operativo abierto, anotado en
D10: nada impide tecnicamente un parcial_acabados de superficie grande
(70 m2 en nivel medio dan 22.540 EUR), que activaria el Gate.

Tope de superficie (2026-09-20): m2 acotado a 0 < m2 <= 500 con defensa
doble (Decimal con le=MAX_M2_LEAD en LeadCreate, y CHECK
chk_leads_m2_rango sobre la expresion JSONB, migracion paso6).
check_migracion_m2_leads 23/23. Antes, un lead de 60.000 m2 se aceptaba
y reventaba al calcular, porque importe_max no cabe en NUMERIC(10,2).

RESUELTO (2026-09-20): scripts/check_exception_handler.py daba 22/28
porque no enviaba X-Webhook-Secret (el script es anterior a la
autenticacion del webhook). Corregido: ahora 32/32. La causa no era el
manejador de excepciones, que funcionaba bien.

Guarda de conn.closed en get_transactional_connection() (D16),
integrada en main el 2026-09-24 por fast-forward (commit 9c4197f): el
rollback del bloque except ya no lanza InterfaceError encima del error
original cuando la conexión ya está cerrada, que es la misma guarda que
get_db_connection() tenía desde antes. Verificado con
scripts/check_rollback_conn_cerrada.py 17/17 (tres casos: conexión
cerrada + error, conexión viva + error, y sin error), y en negativo
contra las DOS versiones rotas conocidas, 14/17 cada una: la original
sin guarda (sale InterfaceError con el error real encadenado debajo) y
una con la guarda mal indentada (no sale nada, la excepción se traga).
Regresión sin cambios: check_create_lead_service 31/31 y
check_leads_endpoint_http 12/12. Queda abierto el caso OperationalError,
cuando psycopg2 todavía no ha detectado que la conexión murió y closed
sigue valiendo 0.

Tiempos límite y validación de conexiones del pool (rama
fix/n0-timeouts-db, commit c1c075b, 2026-09-25): arregla el
OperationalError en el primer execute tras un corte de red o del pooler
(4 veces en dos días, la última bloqueando POST /leads desde n8n).
Verificado con scripts/check_conexion_muerta.py 18/18 y con una prueba
real: uvicorn inactivo 8 min, sus 2 conexiones matadas con
pg_terminate_backend, POST /leads 201 con dos [AVISO] ... descartada, y
GET /health/db 200. En negativo: main FALLO y la versión con un solo
reemplazo 16/17. Suite completa 23/24 (el fallo es
check_tipo_reforma_constraint, ver Pendiente). Gabi verificó además una
conversación completa desde el chat de n8n con esta rama. Límites
abiertos: la conexión medio abierta en Windows y la muerte de la
conexión DURANTE una operación (el rollback del except puede tapar el
error original: el caso OperationalError de D16 sigue abierto).
Detalle: docs/TFM_Resumen_Sesion_Timeouts_DB_N0.txt.

Scripts de verificación frágiles (anotados, sin arreglar):
check_tipo_reforma_constraint exige que oportunidades esté vacía y hoy
tiene filas reales, así que siempre da "HAY FALLOS" (obsoleto desde el
paso 1; sus pruebas del CHECK sí pasan). Y hay tokens fijos compartidos
entre scripts: "tok-estr" lo usan check_calculate_estimate_http y
check_mcp_calculate_estimate, así que si uno se corta antes de limpiar,
el otro recibe el lead viejo (convendría un prefijo por script o
uuid4). Otras dos fragilidades, en
docs/TFM_Resumen_Sesion_Contacto_e_Idempotencia_N0.txt, sección 8.

Pendiente: gate-decisions, visits, create-followup-task,
get_business_rules y request_missing_information, la concurrencia
del Session pooler bajo carga, la tabla de excepciones de la Adenda
(hoy un error no controlado de services/ sale como 500 genérico), y la
idempotencia frente a reintentos de n8n (D7, limitación asumida en N0).

Pendiente hasta DESPUÉS de tener POST /calculate-estimate funcionando
(decisión de Gabi, 2026-09-18): suite de pytest (unitarias mockeadas
con TestClient + integración contra un Postgres efímero vía
testcontainers en Docker, que ya está disponible porque n8n corre en
Docker localmente) y un workflow mínimo de GitHub Actions. Hasta
entonces no se escribe ningún test ni se añade ninguna dependencia de
testing; la verificación sigue siendo con los scripts check_*.py.

## Para el razonamiento completo de cada decisión

Ver docs/ en este repo — este archivo es un resumen de referencia
rápida, no sustituye esa documentación. Contiene:

- Informe_Decisiones_N0_TFM_Reformas.txt — esquema de datos y reglas
  de negocio.
- Adenda_Decisiones_N0_Endpoints_y_Excepciones.txt — contrato de los 5
  endpoints y tabla de excepciones.
- TFM_Decisiones_Arquitectura_MCP_y_DB.txt — servidor MCP y capa de
  datos. El "plan de migración a async" citado en la sección de stack
  está aquí, en la sección 7.8.
- TFM_Resumen_Sesion_Backend_N0.txt — narrativa de la sesión de
  scaffolding.
- TFM_Decisiones_Modelo_Datos_Leads.txt — modelo de datos de leads y
  decisiones D1 a D7.
- Decision_RLS_N0.txt — qué protege RLS hoy (cierra la Data API
  pública a anon/authenticated; el backend se lo salta), por qué no hay
  políticas y cuándo pasan a ser obligatorias.
- Notas_Tecnicas_N0.txt — psycopg2-binary como decisión consciente de
  desarrollo local, y el orden de estados de oportunidades sin
  protección en la base de datos (a decidir con gate-decisions/visits).
- TFM_Resumen_Sesion_Autenticacion_Webhook_y_Notas_N0.txt — narrativa
  de la sesión de autenticación del webhook, RLS y notas N0, con las
  salidas reales de verificación.

## Esquema de la base de datos: cuál de los dos .sql manda

docs/schema_actual.sql es la ÚNICA fuente fiable del esquema actual. Lo
genera scripts/dump_schema.py leyendo information_schema de la base de
datos real, y está verificado por ejecución real: el SQL generado se
ejecutó contra un esquema de prueba y recreó las 8 tablas con sus 15
restricciones PK/UNIQUE/FK y sus 7 CHECK. Se regenera ejecutando:

    .\venv\Scripts\python.exe scripts\dump_schema.py

docs/schema_n0_v2.sql es HISTÓRICO. Se escribió a mano y divergió de la
realidad sin que nadie lo notara: le faltan la columna
presupuestos.motivo_gate, las tablas reglas_negocio y tarifas_base
enteras, y el CHECK chk_oportunidades_tipo_reforma. NO debe consultarse
para nada del desarrollo actual. Se conserva solo como registro de cómo
se diseñó el esquema al principio.
