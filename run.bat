@echo off
rem Start the desktop pet without a console window.
rem Interpreter lookup order:
rem   1. .python-path next to this file (one line, full path to pythonw.exe)
rem   2. .venv\Scripts\pythonw.exe
rem   3. pythonw / pyw from PATH
rem Then it checks that PyQt6 and httpx are importable, and tells you if not.
cd /d "%~dp0"

set "PYW="

if exist "%~dp0.python-path" (
  set /p PYW=<"%~dp0.python-path"
)
if defined PYW if not exist "%PYW%" set "PYW="

if not defined PYW if exist "%~dp0.venv\Scripts\pythonw.exe" set "PYW=%~dp0.venv\Scripts\pythonw.exe"

if not defined PYW if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\pythonw.exe" set "PYW=%CONDA_PREFIX%\pythonw.exe"

if not defined PYW (
  where pythonw >nul 2>nul
  if not errorlevel 1 set "PYW=pythonw"
)

if not defined PYW (
  where pyw >nul 2>nul
  if not errorlevel 1 set "PYW=pyw"
)

if not defined PYW goto :nopython

rem Verify dependencies with a console interpreter (pythonw cannot report errors).
set "PYCHK=%PYW:pythonw.exe=python.exe%"
if not exist "%PYCHK%" set "PYCHK=python"
"%PYCHK%" -c "import PyQt6, httpx" >nul 2>nul
if errorlevel 1 goto :nodeps

start "" "%PYW%" "%~dp0main.py"
exit /b 0

:nodeps
echo The Python found at:
echo   %PYW%
echo does not have PyQt6 / httpx installed.
echo.
echo Install them with:
echo   "%PYCHK%" -m pip install -r requirements.txt
echo Or put the full path of a working pythonw.exe into a file named .python-path
echo next to this script.
pause
exit /b 1

:nopython
echo Python was not found.
echo Install Python 3.10+ and run: pip install -r requirements.txt
echo Or put the full path of pythonw.exe into a file named .python-path next to this script.
pause
exit /b 1
