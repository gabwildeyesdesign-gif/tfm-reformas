# =============================================================================
# scripts/seg01_seg04_fase2.sh: pruebas de seguridad SEG-01 y SEG-04
# =============================================================================
# Qué hace:
#   SEG-01 (autenticación del backend). Arranca un uvicorn PROPIO en el puerto
#     8037 y llama a /leads y a /mcp/ sin credencial y con una credencial
#     errónea INVENTADA. Las cuatro peticiones deben responder 401. Una quinta,
#     opcional, prueba una ruta de administración con un X-Gate-Secret falso.
#   SEG-04 (RLS en Supabase). Con la conexión del backend (DATABASE_URL del
#     .env) y en una sesión de SOLO LECTURA, lista las tablas de "public" y si
#     tienen RLS activo. No escribe nada: una escritura daría error.
#
# Cuándo: solo en la FASE 2 (regla "Memoria en dos fases" de CLAUDE.md):
#   al menos 1,5 GB libres, sin Docker/n8n, Chrome ni Edge, enchufado y con la
#   tapa abierta.
#
# Reglas que respeta:
#   - Nunca el puerto 8000 (es el uvicorn de Gabi para n8n).
#   - uvicorn SIN --reload.
#   - El uvicorn propio se detiene SOLO por el PID de su log ("Started server
#     process [N]"), comprobado con netstat. El paso final se deja escrito a
#     mano a propósito: no se mata nada a ciegas.
#   - Ninguna credencial real: FALSA es un texto inventado.
#   - El log va a la carpeta temporal ($TEMP), no al repositorio (público).
#
# Uso (Git Bash, desde la raíz del repositorio), mejor paso a paso, copiando
# cada bloque, para leer cada resultado:
#   bash scripts/seg01_seg04_fase2.sh
# Resultado esperado: cinco líneas con 401 y, en SEG-04, "sin RLS: []".
# =============================================================================

# Dónde se guarda el log del uvicorn propio (fuera del repositorio).
LOG="${TEMP:-/tmp}/seg01_uvicorn.log"

# 0) Arrancar un uvicorn propio en el 8037, en segundo plano, con su log.
PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe -m uvicorn app.main:app --port 8037 > "$LOG" 2>&1 &
# Darle unos segundos para que abra el puerto.
sleep 8

# Una credencial errónea INVENTADA (nunca un valor del .env).
FALSA="credencial-falsa-seg01-no-es-real"

# --- SEG-01: /leads -----------------------------------------------------------
# a) Sin cabecera X-Webhook-Secret: se espera 401 (antes de validar el cuerpo).
curl -s -o /dev/null -w "leads sin credencial: %{http_code}\n" \
  -X POST http://127.0.0.1:8037/leads -H "Content-Type: application/json" -d '{}'
# b) Con un valor erróneo: se espera 401.
curl -s -o /dev/null -w "leads con credencial falsa: %{http_code}\n" \
  -X POST http://127.0.0.1:8037/leads -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: $FALSA" -d '{}'

# --- SEG-01: /mcp -------------------------------------------------------------
# Una petición MCP mínima (initialize de JSON-RPC).
MCP_INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"seg01","version":"0"}}}'
# c) Sin cabecera Authorization: se espera 401.
curl -s -o /dev/null -w "mcp sin credencial: %{http_code}\n" \
  -X POST http://127.0.0.1:8037/mcp/ -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" -d "$MCP_INIT"
# d) Con un token erróneo (esquema Bearer, valor inventado): se espera 401.
curl -s -o /dev/null -w "mcp con credencial falsa: %{http_code}\n" \
  -X POST http://127.0.0.1:8037/mcp/ -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $FALSA" -d "$MCP_INIT"

# e) (Opcional) una ruta de administración con X-Gate-Secret falso: se espera 401.
curl -s -o /dev/null -w "ficha con X-Gate-Secret falso: %{http_code}\n" \
  http://127.0.0.1:8037/oportunidades/1/ficha -H "X-Gate-Secret: $FALSA"

# --- Parar el uvicorn propio ----------------------------------------------------
# Leer el PID del log y compararlo con el que escucha en el 8037.
grep "Started server process" "$LOG"
netstat -ano | grep ":8037 .*LISTENING"
# Solo si los dos PID coinciden, a mano:
#   taskkill //PID <PID> //F

# --- SEG-04: RLS (SOLO LECTURA, con la conexión del backend) -------------------
# El código Python va en ASCII: un heredoc por stdin se lee en cp1252 (gotcha
# de CLAUDE.md) y rompería las tildes.
PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe - <<'EOF'
# DATABASE_URL sale del .env (app.config llama a load_dotenv).
import psycopg2
from app.config import DATABASE_URL
con = psycopg2.connect(DATABASE_URL)
# Sesion de solo lectura: cualquier escritura daria error.
con.set_session(readonly=True)
cur = con.cursor()
cur.execute("SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;")
filas = cur.fetchall()
# Una linea por tabla, y al final las que no tienen RLS (se espera la lista vacia).
for t, rls in filas:
    print(f"{t:30} RLS={'si' if rls else 'NO'}")
print(f"{len(filas)} tablas; sin RLS: {[t for t, r in filas if not r]}")
con.rollback()
con.close()
EOF
