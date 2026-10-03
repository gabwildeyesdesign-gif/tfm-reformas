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
- Procesos: solo se terminan los procesos arrancados en la sesión, por el
  PID guardado al arrancarlos (por ejemplo, el que uvicorn escribe en su
  log con "Started server process [N]", comprobado contra el que escucha
  en el puerto). Nunca se mata "lo que escuche en un puerto". Si el
  puerto está ocupado por otro proceso, se para y se avisa. El 8000 no se
  usa para pruebas: es el uvicorn de Gabi para n8n. Motivo: el
  2026-09-25 un bucle de regresión mató así el servidor de Gabi.
- Borrados en scripts: un script solo borra las filas que ha creado él,
  identificadas por una marca propia (emails del dominio reservado
  @example.com, ids sacados de ellos, o un valor aleatorio de la
  ejecución), nunca por accion, ruta o estado a secas. Para leer lo que
  ha escrito, la marca se combina con id > id_base (el id máximo anotado
  al empezar), para que lo que escriba otro proceso no cambie los
  recuentos. Una prueba en negativo que provoque errores 500 deja logs del
  manejador global sin marca: se borran por id exacto al terminar, tras
  comprobar que son los esperados. Motivo: el 2026-09-26 se descubrió que
  check_exception_handler borraba con accion = 'error_no_controlado' los
  errores 500 REALES del uvicorn de n8n (75 filas en 39 ejecuciones, según
  pg_stat_statements); corregido en 6bde841 y verificado con un log
  centinela.
- Cuando un arreglo depende de la ESTRUCTURA del código y no solo de su
  sintaxis, se verifica con ast.parse y se enseña la salida. py_compile
  no basta: devuelve exit code 0 sobre código que compila pero que anida
  las sentencias de forma equivocada. Es exactamente lo que ocurrió en
  D16, con el raise atrapado dentro del if porque Python ignora los
  comentarios al calcular los niveles de indentación.
- Suite completa sin suspensiones (regla desde el 2026-10-03). Motivo: los
  tres cortes de conexión de la rama de /gate-decisions coincidieron con
  un Modern Standby de Windows con la red desconectada (ver "Scripts de
  verificación frágiles"); en este portátil, apagar la pantalla ya es
  entrar en esa suspensión. El lanzador que lo automatizaba
  (docs/Plan_Ejecutor_Suite.txt) quedó APLAZADO y lo sustituye esta regla.
  - Antes de empezar: portátil ENCHUFADO, con la pantalla y la suspensión
    en "Nunca" cuando está enchufado (ajuste ya hecho por Gabi en
    Windows), y la tapa abierta. Se anota la hora de inicio.
  - Al terminar: se comprueba en el registro de Windows si hubo algún
    Kernel-Power 506 (entra en Modern Standby) o 507 (sale) entre la hora
    de inicio y la de fin. Si los hubo, la pasada NO VALE: no cuenta ni
    como 32/32 ni como fallo, y se repite entera. Comando de PowerShell
    (probado el 2026-10-03: encuentra la suspensión de 12:29:14-12:29:57
    en 12:25-12:35 y ninguna en 13:30-13:45):
        # Pon aquí la hora de inicio y la de fin de la pasada.
        $inicio = '2026-10-03 12:25'; $fin = '2026-10-03 12:35'
        # Busca en el registro System los eventos 506/507 de Kernel-Power
        # en ese intervalo. -ErrorAction SilentlyContinue: si no hay
        # ninguno, Get-WinEvent da un error en vez de una lista vacía.
        $s = Get-WinEvent -FilterHashtable @{LogName='System'; `
               ProviderName='Microsoft-Windows-Kernel-Power'; Id=506,507; `
               StartTime=[datetime]$inicio; EndTime=[datetime]$fin} `
               -ErrorAction SilentlyContinue
        # Si encontró alguno, los enseña y avisa; si no, la pasada vale.
        if ($s) { $s | Select-Object TimeCreated, Id; 'LA PASADA NO VALE: hubo suspension' } else { 'ninguna suspension: la pasada vale' }
  - Limitación conocida (P4 del plan): check_foreign_keys,
    check_mcp_connection, check_pool_connections,
    check_pool_transaction_state y check_rls_estado no llaman nunca a
    sys.exit, así que terminan con código 0 aunque una comprobación salga
    mal (solo dan otro código si revientan con una excepción). Su salida
    hay que revisarla a mano: el código de salida no basta.

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
pydantic==2.13.5, starlette==1.6.0, psycopg2-binary==2.9.13,
tzdata==2026.4 (desde el 2026-09-26: en Windows, ZoneInfo("Europe/Madrid")
falla sin él con ZoneInfoNotFoundError).

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
autenticado por Bearer MCP_SECRET. Desde el 2026-09-25 existe también
GET /leads/session/{lead_token} (solo REST, en el router de leads, para
el router de n8n; no forma parte de los 5 endpoints originales).
Desde el 2026-09-26, POST /visits (solo REST, tercer include_router),
integrado en main (7934fa6). Desde el 2026-10-01, en la rama
feat/n0-gate-decisions (pendiente de merge), POST /gate-decisions (solo
REST, cuarto include_router), con su propia cabecera X-Gate-Secret.
create-followup-task sigue siendo un docstring de una línea.
Lo que sigue es la especificación completa, no el estado actual.

calculate-estimate tiene las dos (MCP en producción, REST para
pruebas). get_business_rules() y request_missing_information() son
SOLO MCP. leads, gate-decisions, visits, create-followup-task son
SOLO REST.

## Gotchas ya resueltos (no los reinvestigues)

- En Git Bash, cualquier argumento que empiece por "/" (por ejemplo
  /leads) se convierte en una ruta de Windows (C:/Program Files/Git/leads)
  antes de llegar al programa, salvo con MSYS_NO_PATHCONV=1 delante del
  comando. Pasó el 2026-09-26 en la verificación con centinelas: el log
  que debía imitar uno de /leads se guardó con la ruta convertida. Es el
  mismo motivo por el que Docker se maneja desde PowerShell.
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
  DATABASE_URL y WEBHOOK_SECRET (y, desde que se añadieron, MCP_SECRET y
  GATE_SECRET). Los valores 2 y 10 son los por defecto
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
- GATE_SECRET (tercer secreto, desde el 2026-09-30) también es
  obligatorio para ARRANCAR. app/api/security.py detiene uvicorn, antes de
  abrir el puerto, si GATE_SECRET falta o solo tiene espacios, si es igual
  a WEBHOOK_SECRET o si es igual a MCP_SECRET; y también si MCP_SECRET es
  igual a WEBHOOK_SECRET (P10: el mensaje de server.py ya lo prometía,
  pero nadie lo comprobaba). Los mensajes nombran las variables, nunca su
  valor. POST /gate-decisions exige la cabecera X-Gate-Secret (401 sin
  ella, o con el valor de WEBHOOK_SECRET en cualquiera de las dos
  cabeceras). Motivo: WEBHOOK_SECRET lo tiene la herramienta del Agente 2,
  y con el mismo valor el agente podría decidir su propio Gate. En n8n va
  en una credencial "Header Auth" PROPIA, solo para el formulario del
  Gate. Al integrar la rama, el uvicorn del 8000 no arranca sin
  GATE_SECRET en el .env (R4 del plan de /gate-decisions).
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
  El proyecto tiene por tanto 9 tablas, no 8. (Desde paso10, 2026-09-28,
  son 10: se añadió decisiones_gate.)
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
> **Integrado en `main` el 2026-09-26.** POST /visits (rama
> feat/n0-visits), último commit 7934fa6.
>
> **Bloque actual: POST /gate-decisions, implementado y verificado en la
> rama `feat/n0-gate-decisions`, pendiente de merge** (Bloques A a E
> cerrados; el corte de mitad de octubre de D18 se cumple). Antes del
> merge, Gabi tiene que añadir GATE_SECRET a su .env, o el uvicorn del
> 8000 no arrancará (R4). Se aplica la misma regla de siempre: **no hacer
> merge a `main` sin la confirmación explícita de Gabi**, y siempre con
> `--ff-only`.
>
> El router de n8n (D18.9: Chat Trigger → GET /leads/session/{lead_token}
> → Switch → Agente 1 o Agente 2) se construye en n8n, fuera de este
> repositorio; su endpoint está en main desde el 2026-09-25. Le falta el
> mensaje fijo para el estado 'perdida' (P6 del plan de /gate-decisions).
>
> **Numeración de los workflows de n8n (redefinida el 2026-09-28, P8 del
> plan de /gate-decisions; sustituye a la de D18):**
> - WF2 = aviso del Gate a administración, UNA sola vez, al aparecer el
>   Gate. Sin botones ni nodo Wait (D19).
> - WF3 = barridos periódicos, con dos consultas: el seguimiento de 48 h
>   (D13) y el recordatorio del Gate a administración cuando una
>   oportunidad lleva más de horas_recordatorio_gate (24) horas en
>   'pendiente_aprobacion'. Al registrarse la decisión, el estado cambia y
>   el recordatorio se detiene solo.
> - WF4 = errores (sin cambios respecto a D18).
>
> Trabajo posterior, fuera de esta rama (sección 11 del plan): el endpoint
> de solo lectura para el aviso del Gate (antes de WF2), WF2, WF3, el
> formulario de n8n con su credencial X-Gate-Secret, el mensaje del router
> para 'perdida' y la deuda técnica D3.

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

GET /leads/session/{lead_token} (rama feat/n0-endpoint-sesion, commit
5f0b92d, 2026-09-25): el endpoint del router de n8n. 200 siempre que la
petición sea válida (un token inexistente da existe=false), 401 sin
X-Webhook-Secret (antes que el 422), 422 con más de 100 caracteres, y
NUNCA importes (D18.5: la consulta no los lee y el esquema no los
tiene). Verificado con scripts/check_lead_session_http.py 24/24; prueba
en negativo en dos niveles: con los importes solo en services/, 24/24
(los quita el response_model); con los importes también en el esquema,
14/24. Suite completa 24/25 (el fallo es check_tipo_reforma_constraint).
Detalle: docs/Plan_Endpoint_Sesion_Lead.txt y la sección 5.3 de la
Adenda.

POST /visits (rama feat/n0-visits, 2026-09-26; commits 41c3420
migración, 6bde841 borrados de logs, 6bf9c1d código, 9ddf148 y d3df19a
verificación): registra la SOLICITUD de visita técnica que el Agente 2
pide desde el chat (no es una reserva). Entrada lead_token, fecha, hora
(Europe/Madrid) y texto_cliente, nunca oportunidad_id; franjas y duración
en reglas_negocio. Migración paso9 (estado 'solicitada', texto_cliente,
updated_at, fecha_propuesta NOT NULL, índice único parcial de una visita
activa por oportunidad, 5 reglas): check_migracion_visitas 28/28 (en
negativo, sin índice ni CHECK, 24/28). check_visits_service 16/16 (cambio
de hora 23/10 -> +02:00 y 26/10 -> +01:00; con un desfase fijo +02:00,
4/6; configuración incompleta -> 503 con el log conservado).
check_visits_http 55/55 (401, 404, 409 x4, 422 de formato y de negocio,
201 nueva, 200 repetición, 201 sustitución, concurrencia, 409
visita_confirmada, 0 importes, 201 <-> creado en todas, y "no toca nada
ajeno"). En negativo: sin la comprobación del Gate 51/54, sin la del final
de la franja 44/54, sin FOR UPDATE 50/54 (el índice parcial frena el
duplicado, pero con un 500). Suite completa 27/28 (el fallo es
check_tipo_reforma_constraint). Detalle: docs/Plan_Endpoint_Visits.txt y
la sección 5.4 de la Adenda (códigos y limitaciones: Gate permanente,
festivos, hora exacta, visita confirmada).

POST /gate-decisions (rama feat/n0-gate-decisions, 2026-09-28 a
2026-10-01, pendiente de merge; hashes por bloque en la sección 10 del
plan): administración registra el resultado de su llamada a un cliente con
Gate (D19): visita_acordada (fecha y hora; crea una visita 'confirmada' y
la oportunidad pasa a 'visita_agendada') o descartar (motivo; pasa a
'perdida'), con el informe siempre. Cabecera X-Gate-Secret propia. Tabla
nueva decisiones_gate (paso10 + paso10b, con RLS):
check_migracion_decisiones_gate 38/38 (en negativo, quitando cada
restricción, de 34/38 a 37/38). Reglas de fecha compartidas con /visits en
app/services/reglas_visita.py (Bloque C1, sin cambio de comportamiento:
check_visits_http 55/55, check_visits_service 16/16).
check_gate_decisions_http 121/121 (arranque con GATE_SECRET vacío o
repetido, 401 incluido el valor de WEBHOOK_SECRET, 12 x 422 de forma, 404,
409 x5 motivos, 422 de fecha, 201 con todos sus efectos, 200 sin escribir
nada, concurrencia, regresión con /visits y /leads/session, 0 importes);
check_gate_decisions_service 14/14 (503 con el log conservado y su
"origen" en los dos endpoints). En negativo, sobre copias: dependencia de
WEBHOOK_SECRET 54/121, sin FOR UPDATE 117/121 (dos 500 por
UniqueViolation), sin sin_gate 120/121, estado antes que repetición
112/121, informe en el log 118/121, repetición que escribe 117/121, y en
el servicio 13/14 y 12/14. Suite completa 31/32 (el fallo es
check_tipo_reforma_constraint). Detalle: docs/Plan_Endpoint_Gate_Decisions.txt
y la sección 5.5 de la Adenda (limitaciones: Gate permanente, decisión
definitiva, operador no identificado y P11, aceptada como limitación
documentada el 2026-09-28).

check_tipo_reforma_constraint arreglado (rama feat/n0-gate-decisions,
commit 145dcea, 2026-10-03): su comprobación final contaba TODAS las
oportunidades de la tabla y exigía 0, así que daba "HAY FALLOS" siempre
que hubiera oportunidades reales. Ahora cuenta solo las suyas: las que
cuelgan del cliente con su EMAIL_PRUEBA (@example.invalid, único por el
índice clientes_email_lower_key). Con la tabla real llena (20
oportunidades): CORRECTO por primera vez. En negativo, una copia que deja
guardada una oportunidad PROPIA antes de la comprobación da HAY FALLOS
("oportunidades descartables restantes: 1"); sus filas se borraron por id
exacto.

check_migracion_iva_y_seguimiento arreglado (2026-10-03, misma rama): su
comprobación "ninguna fila tiene valor todavía" exigía que
oportunidades.fecha_ultimo_contacto fuera NULL en TODA la tabla. Era el
estado de un momento concreto que esta rama dejó de hacer cierto (POST
/gate-decisions la escribe, P5), y la primera decisión real del Gate la
habría hecho fallar para siempre. Ahora el recuento solo se imprime como
[INFO], con una nota fechada en el código: 16/16. La prueba en negativo
es el fallo real de ese mismo día con la versión anterior (16/17, por las
oportunidades de prueba que dejó un corte de check_gate_decisions_http).

Scripts de verificación frágiles (anotados, sin arreglar): hay tokens
fijos compartidos entre scripts: "tok-estr" lo usan check_calculate_estimate_http y
check_mcp_calculate_estimate, así que si uno se corta antes de limpiar,
el otro recibe el lead viejo (convendría un prefijo por script o
uuid4). check_graceful_shutdown y check_mcp_connection tienen el puerto
8000 escrito en el código: deberían leerlo de una variable de entorno
(fue la causa del incidente del 2026-09-25; mientras tanto, se ejecutan
desde una copia con otro puerto). De las dos fragilidades anotadas en
docs/TFM_Resumen_Sesion_Contacto_e_Idempotencia_N0.txt, sección 8, la de
check_migracion_m2_leads (max(tipo) sobre todos los logs) quedó corregida
el 2026-09-26 (6bde841); en check_exception_handler sigue abierta la de
su prueba D, que solo mira el código 201.
Conexiones directas de los scripts sin protección frente a cortes: tres
cortes en esta rama, todos con OperationalError "server closed the
connection unexpectedly" en una conexión abierta con psycopg2.connect
directo, que no tiene las protecciones del pool (keepalives y validación
con SELECT 1). El primero, en check_visits_http el 2026-09-30 (Bloque C1,
anotado en el mensaje de 520aae1); el segundo, en la conexión de
observación de check_transactional_connection, caso 6, el 2026-10-03
(145dcea); el tercero, en la de check_gate_decisions_http, caso 8, el
mismo día (d029322), que dejó 10 oportunidades propias sin limpiar hasta
la siguiente ejecución. Repetidos aisladamente, los dos de hoy dieron OK.
Hipótesis SIN VERIFICAR: una conexión que
pasa un rato sin usarse durante una suite larga muere por un corte de red.
Solución propuesta para después de N0: una función compartida para las
conexiones de los scripts. No se arregla en esta rama (es la primera
tarea pendiente después del merge, ver "Pendiente").
(Nota 2026-10-03, rama fix/ejecutor-suite: la hipótesis de arriba queda
REFUTADA por lo medido. Experimento con dos conexiones directas, una a
secas y otra con los parámetros de init_pool, y 15 min sin usarse: SIN
suspensión de Windows (13:30-13:45, suspensión bloqueada y 0 eventos 506/
507 en el intervalo), las DOS sobrevivieron; CON suspensiones (12:54-13:31,
tres Modern Standby con la red desconectada), las DOS murieron con el
mismo OperationalError, también la protegida con keepalives. El corte de
check_gate_decisions_http coincide al segundo con una suspensión
(12:29:14-12:29:57; su log se cerró a las 12:29:57). Los cortes de
check_transactional_connection (03/10) y check_visits_http (30/09): causa
SIN DETERMINAR, no hay registros de la hora de esas pasadas. El arreglo
es impedir la suspensión durante la suite (docs/Plan_Ejecutor_Suite.txt);
conectar_script() queda solo como mejora menor, por paridad de
statement_timeout (2 min en las conexiones de los scripts frente a 30 s
en las del pool), no como arreglo de los cortes.)
(Nota 2026-10-03, posterior a la anterior: la causa queda DETERMINADA en
los tres cortes. Los TRES coinciden con un Modern Standby con la red
desconectada, según el registro de Windows de este portátil (eventos
Kernel-Power 506/507 y 172, y NetworkProfile 10001/10000): 30/09
14:26:07-14:28:47 (check_visits_http; el corte ocurrió entre 14:25:22 y
14:29:09, según el log de esa suite), 03/10 12:05:58-12:07:24
(check_transactional_connection; entre 12:05:51 y 12:07:25, según la
fecha de creación de los logs de la primera suite) y 03/10
12:29:14-12:29:57 (check_gate_decisions_http). Los eventos del Wi-Fi
(WLAN-AutoConfig) no muestran nada en ningún intervalo. Gabi había dicho
que en dos de ellos estaba usando el ordenador; tras ver el registro,
retira ese testimonio: no notó las suspensiones. Manda el registro.
Conclusión: la causa de los cortes es la suspensión del portátil (3 de 3,
más el experimento: 15 min sin usarse NO matan la conexión y la
suspensión sí). Ya no hay causa "sin determinar".)

Suite de verificación (2026-10-03): 32 scripts check_*.py. Ninguno falla
ya por su propia lógica: check_tipo_reforma_constraint, el fallo fijo
anterior, da CORRECTO desde 145dcea. Las dos pasadas de hoy NO dieron
32/32 de una vez: 31/32 (145dcea) y 30/32 (d029322), en los dos casos por
cortes del pooler en conexiones directas de los scripts (ver frágiles);
los scripts afectados, repetidos aisladamente, pasaron (11/11 sin fallos,
121/121 y 17/17). Antes (2026-10-01): 31/32, con
check_tipo_reforma_constraint como único fallo. Desde
la rama de /gate-decisions se suman check_migracion_decisiones_gate,
check_gate_decisions_http (puerto 8019, nunca el 8000),
check_gate_decisions_service y check_schema_actual_ejecutable (ejecuta
docs/schema_actual.sql en un esquema de prueba dentro de una transacción
que termina en ROLLBACK y lo compara con public: 10/10). Se ejecuta con el
procedimiento seguro: check_graceful_shutdown y check_mcp_connection desde
una copia con otro puerto, y solo se detiene el uvicorn propio, por su PID.

Pendiente, PRIMERA tarea después del merge de feat/n0-gate-decisions: una
función compartida para las conexiones directas de los scripts check_*.py
(hoy psycopg2.connect sin keepalives ni validación con SELECT 1; tres
cortes del pooler en esa rama, ver "Scripts de verificación frágiles").
(Nota 2026-10-03: sustituida por el lanzador de la suite que impide la
suspensión de Windows, docs/Plan_Ejecutor_Suite.txt, rama
fix/ejecutor-suite; el experimento refutó que la causa fuera el tiempo
sin usarse.)

Pendiente: create-followup-task,
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
- Decisiones_D16_Agente_Conversacional_Captura_n8n.md — D16: agente
  conversacional de captura en n8n (revisa D11).
- Decisiones_D17_Cierre_MCP_Client_Gate_Determinista.md — D17: cierre
  del nodo MCP Client y paso determinista posterior al cálculo.
- Decisiones_D18_Agente2_en_N0_Arquitectura_Post_Calculo.md — D18: el
  Agente 2 entra en N0 (revisa D16.5); router de chat (D18.9), sin
  importes para el agente (D18.5) y plazos.
- Plan_Endpoint_Sesion_Lead.txt — plan aprobado de GET
  /leads/session/{lead_token}.
- Plan_Endpoint_Visits.txt — plan de POST /visits (P1-P5).
- Plan_Endpoint_Gate_Decisions.txt — plan de POST /gate-decisions (D19,
  P1-P11, bloques con sus hashes y trabajo posterior). D19 no tiene todavía
  documento de decisiones propio: su contexto está en la sección 1.1.

## Esquema de la base de datos: cuál de los dos .sql manda

docs/schema_actual.sql es la ÚNICA fuente fiable del esquema actual. Lo
genera scripts/dump_schema.py leyendo information_schema de la base de
datos real, y está verificado por ejecución real: el SQL generado se
ejecutó contra un esquema de prueba y recreó las 8 tablas con sus 15
restricciones PK/UNIQUE/FK y sus 7 CHECK. Se regenera ejecutando:

    .\venv\Scripts\python.exe scripts\dump_schema.py

(Nota 2026-10-01: las cifras de arriba son las de entonces. Hoy son 10
tablas, y desde b505390 la comprobación la hace un script:
scripts/check_schema_actual_ejecutable.py, 10/10 con 10 claves primarias,
6 unique, 6 claves foráneas, 15 CHECK y 2 índices sueltos. dump_schema
escribe ahora bien las claves foráneas compuestas y ordena las tablas para
que el archivo se pueda ejecutar de principio a fin.)

docs/schema_n0_v2.sql es HISTÓRICO. Se escribió a mano y divergió de la
realidad sin que nadie lo notara: le faltan la columna
presupuestos.motivo_gate, las tablas reglas_negocio y tarifas_base
enteras, y el CHECK chk_oportunidades_tipo_reforma. NO debe consultarse
para nada del desarrollo actual. Se conserva solo como registro de cómo
se diseñó el esquema al principio.
