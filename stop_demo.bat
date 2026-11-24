@echo off
REM ===================================================================
REM  LogGuard demo - one double-click shutdown (Windows).
REM
REM  Closes the four LogGuard windows opened by start_demo.bat (API,
REM  ingestion runner, RAG explainer, frontend) and stops the Docker
REM  containers.
REM ===================================================================

set "REPO=%~dp0"
cd /d "%REPO%"

echo [stop_demo] closing LogGuard windows...
REM Each window is a cmd.exe whose command line contains this folder's
REM path, so only this project's processes are stopped.
set "LG_ROOT=%REPO%"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='cmd.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($env:LG_ROOT) -and $_.CommandLine -match 'uvicorn|ingestion\.runner|rag\.explainer|npm run dev' } | ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }"

echo [stop_demo] stopping docker containers...
docker compose stop

echo.
echo ===================================================================
echo  Demo stopped. Start again with start_demo.bat
echo ===================================================================
echo.
pause
