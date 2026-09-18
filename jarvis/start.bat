@echo off
REM ===========================================================
REM  Start Jarvis.  Double-click this, or run it from a prompt.
REM
REM  Calls .venv\Scripts\python.exe directly rather than using
REM  "activate", so it does not care which terminal you are in
REM  and PowerShell's execution policy cannot block it.
REM ===========================================================

setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

REM --- find a usable Python -----------------------------------
set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    echo.
    echo Python is not on your PATH.
    echo Install Python 3.11 or newer from https://python.org/downloads
    echo and tick "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)

REM --- create the virtual environment on first run ------------
if not exist "%VENV_PY%" (
    echo Creating the virtual environment. This happens once...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo.
        echo Could not create the virtual environment.
        echo.
        pause
        exit /b 1
    )
)

REM --- install dependencies whenever requirements.txt changes ---
REM  Checking for one known package would miss anything added later, so this
REM  compares requirements.txt against a stamp written after the last
REM  successful install.
"%VENV_PY%" -c "import pathlib,sys; r=pathlib.Path('requirements.txt'); s=pathlib.Path('.venv/requirements.stamp'); sys.exit(0 if s.exists() and s.read_text().strip()==str(r.stat().st_mtime_ns) else 1)" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies. This takes a minute the first time...
    "%VENV_PY%" -m pip install --upgrade pip >nul
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Dependency installation failed. Scroll up for the reason.
        echo.
        pause
        exit /b 1
    )
    "%VENV_PY%" -c "import pathlib; pathlib.Path('.venv/requirements.stamp').write_text(str(pathlib.Path('requirements.txt').stat().st_mtime_ns))"
)

REM --- make sure there is a .env -------------------------------
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo A .env file has been created for you. Add your Anthropic API key
    echo to it, then run this again. Opening it now...
    echo.
    notepad ".env"
    pause
    exit /b 0
)

REM --- go -------------------------------------------------------
"%VENV_PY%" run.py

REM Keep the window open if Jarvis exited with an error, so a
REM double-click user can actually read it.
if errorlevel 1 pause
endlocal
