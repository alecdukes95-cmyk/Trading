@echo off
REM EdgeLab launcher for Windows. Double-click this file.
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto nopython

if not exist ".venv\Scripts\python.exe" (
    echo First run: creating a private Python environment. This takes a few minutes, only once...
    %PY% -m venv .venv || goto nopython
)

REM Install or update packages only when requirements.txt has changed
fc /b requirements.txt .venv\installed-requirements.txt >nul 2>nul
if errorlevel 1 (
    echo Installing packages...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto failed
    copy /y requirements.txt .venv\installed-requirements.txt >nul
    echo Optional: MetaTrader 5 link - skipped quietly if your Python version isn't supported yet.
    ".venv\Scripts\python.exe" -m pip install MetaTrader5 >nul 2>nul
)

REM Skip Streamlit's one-time e-mail prompt
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    mkdir "%USERPROFILE%\.streamlit" 2>nul
    > "%USERPROFILE%\.streamlit\credentials.toml" echo [general]
    >> "%USERPROFILE%\.streamlit\credentials.toml" echo email = ""
)

echo.
echo Starting EdgeLab. Your browser will open at http://localhost:8501
echo Keep this window open while you use the app. Close it to stop.
echo.
".venv\Scripts\python.exe" -m streamlit run app.py
goto end

:nopython
echo.
echo Python was not found.
echo Install Python 3.11 or newer from https://www.python.org/downloads/
echo IMPORTANT: on the first installer screen, tick "Add python.exe to PATH".
echo Then double-click run.bat again.
pause
goto end

:failed
echo.
echo Installing packages failed. Check your internet connection and try again.
echo If it keeps failing, delete the .venv folder and run this file again.
pause

:end
