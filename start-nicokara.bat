@echo off
setlocal
set "ROOT=%~dp0"

if not exist "%ROOT%python\python.exe" (
  echo [ERROR] Cannot find the bundled Python at "%ROOT%python\python.exe".
  pause
  exit /b 1
)
if not exist "%ROOT%frontend\node_modules" (
  echo [ERROR] Frontend dependencies are missing. Run npm install in frontend first.
  pause
  exit /b 1
)

echo Starting Nicokara Studio backend and frontend...
start "Nicokara Backend" /D "%ROOT%backend" cmd /k "..\python\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8100"
timeout /t 2 /nobreak >nul
start "Nicokara Frontend" /D "%ROOT%frontend" cmd /k "npm run dev -- --host 0.0.0.0"
echo.
echo Frontend: http://127.0.0.1:5173
echo Backend:  http://127.0.0.1:8100
echo Close the two terminal windows to stop the services.
endlocal
