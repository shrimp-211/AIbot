@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo ============================================
echo   QQ AI Agent - CI self-check (reproduces GitHub Actions)
echo ============================================

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating virtual environment...
  python -m venv .venv
)
call ".venv\Scripts\activate.bat"

echo [2/4] Installing deps (pip install -e .)...
python -m pip install --upgrade pip -q
pip install -e . -q

echo [3/4] Import self-check...
python -u -c "import src.main"
if errorlevel 1 goto :fail

echo [4/4] Running test suites...
python -u -m src.tests.core_tests
if errorlevel 1 goto :fail
python -u -m src.tests.tools_integration_test
if errorlevel 1 goto :fail
python -u -m src.tests.plugin_load_test
if errorlevel 1 goto :fail

echo.
echo === CI ALL PASSED (core/tools/plugin) ===
pause
exit /b 0

:fail
echo.
echo === CI FAILED, see output above ===
pause
exit /b 1
