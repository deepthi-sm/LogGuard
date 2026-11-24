@echo off
REM ===================================================================
REM  LogGuard demo - one double-click startup (Windows).
REM
REM  Brings up the infrastructure containers (Redis, Postgres, Ollama)
REM  and opens four windows: API, ingestion runner, RAG explainer and
REM  the frontend dev server.
REM
REM  Each session starts with an empty dashboard. Anomalies appear only
REM  for log files uploaded through the Upload page (or fetched through
REM  the Connect page).
REM
REM  Requirements: Docker Desktop running, backend\.venv created with
REM  requirements.txt installed, frontend dependencies installed
REM  (npm install), trained artifacts in backend\artifacts, and the
REM  LLaMA model pulled into Ollama (ollama pull llama3.2:1b).
REM ===================================================================

title LogGuard Demo
set "REPO=%~dp0"
cd /d "%REPO%"

set "DB_URL=postgresql://postgres:postgres@localhost:5432/logguard"
set "REDIS_URL=redis://localhost:6380"

echo [1/5] Starting Docker services (Redis, Postgres, Ollama)...
REM The local API window below serves port 8000, so the containerised
REM backend service is not started here.
docker compose stop backend >nul 2>&1
docker compose up -d redis postgres ollama

echo Waiting for Postgres to accept connections...
:wait_pg
docker compose exec -T postgres pg_isready -U postgres -d logguard >nul 2>&1
if errorlevel 1 (
    ping -n 3 127.0.0.1 >nul
    goto wait_pg
)

echo Resetting the dashboard for a new session...
docker compose exec -T postgres psql -U postgres -d logguard -c "TRUNCATE anomalies, drift_events RESTART IDENTITY CASCADE;" >nul 2>&1
docker compose exec -T redis redis-cli FLUSHALL >nul 2>&1

echo [2/5] Starting API...
start "LogGuard API" cmd /k "cd /d "%REPO%backend" && set "LOGGUARD_DB_URL=%DB_URL%" && set "LOGGUARD_REDIS_URL=%REDIS_URL%" && set "LOGGUARD_LLAMA_MODEL=llama3.2:1b" && .venv\Scripts\activate.bat && uvicorn api.main:app --host 0.0.0.0 --port 8000"

echo [3/5] Starting ingestion runner...
start "LogGuard Runner" cmd /k "cd /d "%REPO%backend" && set "LOGGUARD_DB_URL=%DB_URL%" && set "LOGGUARD_REDIS_URL=%REDIS_URL%" && set "KMP_DUPLICATE_LIB_OK=TRUE" && .venv\Scripts\activate.bat && python -m ingestion.runner"

echo [4/5] Starting RAG explainer...
start "LogGuard RAG" cmd /k "cd /d "%REPO%backend" && set "LOGGUARD_DB_URL=%DB_URL%" && set "LOGGUARD_REDIS_URL=%REDIS_URL%" && set "LOGGUARD_LLAMA_HOST=http://localhost:11434" && set "LOGGUARD_LLAMA_MODEL=llama3.2:1b" && set "KMP_DUPLICATE_LIB_OK=TRUE" && .venv\Scripts\activate.bat && python -m rag.explainer"

echo [5/5] Starting frontend...
start "LogGuard Frontend" cmd /k "cd /d "%REPO%frontend" && set "VITE_USE_MOCK=false" && npm run dev"

echo.
echo ===================================================================
echo  LogGuard is starting. Allow about 30 seconds, then open
echo     Frontend: http://localhost:5173/
echo     API:      http://localhost:8000/api/v1/health
echo.
echo  Upload a .log or .txt file on the Upload page to see anomalies.
echo  To stop everything, run stop_demo.bat
echo ===================================================================
echo.
pause
