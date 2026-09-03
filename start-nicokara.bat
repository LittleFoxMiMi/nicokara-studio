@echo off
setlocal
set "ROOT=%~dp0"

if not exist "%ROOT%python\python.exe" (
  echo [ERROR] Cannot find the bundled Python at "%ROOT%python\python.exe".
  pause
  exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js is required to start the frontend.
  echo Please install the LTS version of Node.js from https://nodejs.org/ and run this script again.
  pause
  exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm was not found. Please reinstall Node.js from https://nodejs.org/.
  pause
  exit /b 1
)

if not exist "%ROOT%frontend\node_modules" (
  echo Frontend dependencies are missing. Installing them now...
  pushd "%ROOT%frontend"
  call npm install
  if errorlevel 1 (
    popd
    echo [ERROR] Failed to install frontend dependencies.
    pause
    exit /b 1
  )
  popd
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
