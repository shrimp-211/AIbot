@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo ============================================
echo   QQ AI Agent - One-click Start (test env)
echo ============================================

REM 1. Check Python
python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.10+ and check "Add to PATH".
  pause
  exit /b 1
)

REM 2. Create venv
if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating virtual environment...
  python -m venv .venv
)

call ".venv\Scripts\activate.bat"

REM 3. Install deps
echo [2/3] Installing dependencies...
python -m pip install --upgrade pip -q
pip install -e . -q

REM 4. Config hint
if not exist ".env" (
  if exist ".env.example" (
    echo [HINT] First run: copy .env.example .env , then edit LLM_API_KEY.
  )
)

REM 5. Start
echo [3/3] Starting QQ AI Agent...
echo.
echo   WebUI : http://127.0.0.1:8080  (random password printed in log if unset)
echo   OneBot: ws://127.0.0.1:6199/ws (NapCat/Lagrange reverse-connect here)
echo   Ctrl+C to exit
echo ============================================
python -m src.main
echo.
echo [Exited] Press any key to close...
pause >nul
