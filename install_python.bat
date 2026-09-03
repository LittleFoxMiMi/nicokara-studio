@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem Install the latest Python 3.12 Windows installer published by Python.org for this project.
set "ROOT=%~dp0"
set "TARGET_DIR=%ROOT%python"
set "BACKEND_DIR=%ROOT%backend"
set "PYTHON_VERSION=3.12.10"
set "PYTHON_EXE=%TARGET_DIR%\python.exe"
set "INSTALLER=%TEMP%\python-%PYTHON_VERSION%-installer.exe"
set "PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-amd64.exe"
set "USTC_INDEX=https://pypi.mirrors.ustc.edu.cn/simple"

if not exist "%BACKEND_DIR%\pyproject.toml" (
  echo [ERROR] Cannot find "%BACKEND_DIR%\pyproject.toml".
  goto :failed
)

echo.
echo Nicokara Python %PYTHON_VERSION% installer
echo Target: "%TARGET_DIR%"
echo.

choice /C YN /N /M "Use the USTC PyPI mirror"
if errorlevel 2 (
  set "PIP_INDEX_URL=https://pypi.org/simple"
  echo Using the official PyPI index: https://pypi.org/simple
) else (
  set "PIP_INDEX_URL=%USTC_INDEX%"
  echo Using the USTC PyPI mirror: %USTC_INDEX%
)

if exist "%TARGET_DIR%\conda-meta" (
  echo.
  echo [ERROR] "%TARGET_DIR%" is an existing Conda environment.
  echo To install the standalone Python, rename or remove that folder first, then run this script again.
  goto :failed
)

if exist "%PYTHON_EXE%" (
  call :check_existing_python
  if errorlevel 1 goto :failed
  goto :install_dependencies
)

if exist "%TARGET_DIR%" (
  call :check_target_empty
  if errorlevel 1 goto :failed
)

if /I "%PROCESSOR_ARCHITECTURE%"=="x86" if not defined PROCESSOR_ARCHITEW6432 (
  set "PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%.exe"
)

echo.
echo Downloading Python %PYTHON_VERSION% from python.org...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '%PYTHON_URL%' -OutFile '%INSTALLER%'"
if errorlevel 1 (
  echo [ERROR] The Python installer download failed.
  goto :failed
)

echo Installing Python %PYTHON_VERSION% into the project folder...
"%INSTALLER%" /quiet InstallAllUsers=0 TargetDir="%TARGET_DIR%" PrependPath=0 Include_pip=1 Include_test=0 Include_launcher=0 AssociateFiles=0 Shortcuts=0
set "INSTALL_EXIT=%ERRORLEVEL%"
del /q "%INSTALLER%" >nul 2>&1
if not "%INSTALL_EXIT%"=="0" (
  echo [ERROR] Python installation failed with exit code %INSTALL_EXIT%.
  goto :failed
)

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Python was installed but "%PYTHON_EXE%" was not found.
  goto :failed
)

:install_dependencies
echo.
echo Configuring pip for this project Python...
"%PYTHON_EXE%" -m pip config --site set global.index-url "%PIP_INDEX_URL%"
if errorlevel 1 goto :pip_failed

echo Upgrading pip...
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 goto :pip_failed

echo Installing backend dependencies from backend\pyproject.toml...
"%PYTHON_EXE%" -m pip install "%BACKEND_DIR%[analysis]"
if errorlevel 1 goto :pip_failed

echo.
echo Python %PYTHON_VERSION% and backend dependencies are ready.
echo Installed interpreter: "%PYTHON_EXE%"
goto :done

:pip_failed
echo.
echo [ERROR] Python was installed, but dependency installation failed.
echo Check your network connection or run this script again.
goto :failed

:failed
echo.
pause
exit /b 1

:done
echo.
pause
exit /b 0

:check_existing_python
set "INSTALLED_VERSION="
for /f "tokens=2" %%V in ('"%PYTHON_EXE%" --version 2^>^&1') do set "INSTALLED_VERSION=%%V"
if /I "%INSTALLED_VERSION%"=="%PYTHON_VERSION%" goto :existing_python_valid
echo.
echo [ERROR] "%TARGET_DIR%" already contains Python %INSTALLED_VERSION%.
echo Rename or remove that folder before installing Python %PYTHON_VERSION%.
exit /b 1

:existing_python_valid
echo Python %PYTHON_VERSION% is already installed in the project folder.
exit /b 0

:check_target_empty
for /f "delims=" %%F in ('dir /a /b "%TARGET_DIR%" 2^>nul') do goto :target_not_empty
exit /b 0

:target_not_empty
echo.
echo [ERROR] "%TARGET_DIR%" already exists and is not empty.
echo Rename or remove that folder before installing Python %PYTHON_VERSION%.
exit /b 1
