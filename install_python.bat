@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem Extract the Python Software Foundation NuGet package as a project-local runtime.
set "ROOT=%~dp0"
set "TARGET_DIR=%ROOT%python"
set "BACKEND_DIR=%ROOT%backend"
set "PYTHON_VERSION=3.12.10"
set "PYTHON_EXE=%TARGET_DIR%\python.exe"
set "PYTHON_PACKAGE=python"
set "USTC_INDEX=https://pypi.mirrors.ustc.edu.cn/simple"

if not exist "%BACKEND_DIR%\pyproject.toml" (
  echo [ERROR] Cannot find "%BACKEND_DIR%\pyproject.toml".
  goto :failed
)

echo.
echo Nicokara project-local Python %PYTHON_VERSION% setup
echo Target: "%TARGET_DIR%"
echo.

echo.
echo Select the package download source:
echo   Y = Use the USTC PyPI mirror (recommended for users in mainland China)
echo   N = Use the official PyPI source
choice /C YN /N /M "Enter Y or N"
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
  set "PYTHON_PACKAGE=pythonx86"
)
set "PYTHON_URL=https://api.nuget.org/v3-flatcontainer/%PYTHON_PACKAGE%/%PYTHON_VERSION%/%PYTHON_PACKAGE%.%PYTHON_VERSION%.nupkg"

echo.
echo Downloading portable Python %PYTHON_VERSION% from NuGet...
powershell.exe -NoProfile -Command ^
  "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue';" ^
  "$target = [IO.Path]::GetFullPath($env:TARGET_DIR);" ^
  "$parent = [IO.Path]::GetDirectoryName($target);" ^
  "$stage = Join-Path $parent ('.python-install-' + [Guid]::NewGuid().ToString('N'));" ^
  "New-Item -ItemType Directory -Path $stage | Out-Null;" ^
  "try {" ^
  "  $archive = Join-Path $stage 'python.zip'; $expanded = Join-Path $stage 'expanded';" ^
  "  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12;" ^
  "  Invoke-WebRequest -UseBasicParsing -Uri $env:PYTHON_URL -OutFile $archive;" ^
  "  Write-Host 'Extracting and checking the project Python...';" ^
  "  Expand-Archive -LiteralPath $archive -DestinationPath $expanded;" ^
  "  $runtime = Join-Path $expanded 'tools';" ^
  "  & (Join-Path $runtime 'python.exe') -I -B -m pip --version;" ^
  "  if ($LASTEXITCODE -ne 0) { throw 'The extracted Python or pip could not start.' };" ^
  "  if ([IO.Directory]::Exists($target)) { [IO.Directory]::Delete($target) };" ^
  "  [IO.Directory]::Move($runtime, $target);" ^
  "} finally {" ^
  "  $stage = [IO.Path]::GetFullPath($stage);" ^
  "  if ([IO.Path]::GetDirectoryName($stage) -eq $parent) { Remove-Item -LiteralPath $stage -Recurse -Force };" ^
  "}"
if not "%ERRORLEVEL%"=="0" (
  echo [ERROR] Could not prepare the project-local Python. See the error above.
  goto :failed
)

:install_dependencies
echo.
echo Configuring pip for this project Python...
"%PYTHON_EXE%" -I -m pip config --site set global.index-url "%PIP_INDEX_URL%" || goto :pip_failed

echo Upgrading pip...
"%PYTHON_EXE%" -I -m pip install --no-user --upgrade pip || goto :pip_failed

echo Installing backend dependencies from backend\pyproject.toml...
"%PYTHON_EXE%" -I -m pip install --no-user "%BACKEND_DIR%[analysis]" || goto :pip_failed

echo.
echo Reinstalling ONNX Runtime DirectML after backend dependencies...
"%PYTHON_EXE%" -I -m pip uninstall --yes onnxruntime-directml || goto :pip_failed
"%PYTHON_EXE%" -I -m pip install --no-user --no-deps --force-reinstall "onnxruntime-directml>=1.24,<2" || goto :pip_failed

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
"%PYTHON_EXE%" -I -B -c "import sys; raise SystemExit('.'.join(map(str, sys.version_info[:3])) != '%PYTHON_VERSION%')"
if not errorlevel 1 (
  echo Using the existing Python %PYTHON_VERSION% in the project folder.
  exit /b 0
)
echo.
echo [ERROR] The existing Python in "%TARGET_DIR%" is not a working Python %PYTHON_VERSION%.
echo Rename or remove that folder before running the setup again.
exit /b 1

:check_target_empty
for /f "delims=" %%F in ('dir /a /b "%TARGET_DIR%" 2^>nul') do goto :target_not_empty
exit /b 0

:target_not_empty
echo.
echo [ERROR] "%TARGET_DIR%" already exists and is not empty.
echo Rename or remove that folder before installing Python %PYTHON_VERSION%.
exit /b 1
