@echo off
rem Start the Trading Journal. First run creates a Python environment and installs packages.
setlocal
cd /d "%~dp0backend"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up for the first time...
    py -3.12 -m venv .venv 2>nul || python -m venv .venv 2>nul || py -3 -m venv .venv || goto :no_python
)
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt || goto :failed
".venv\Scripts\python.exe" -m tradejournal.ui %*
goto :eof

:no_python
echo Could not create the environment. Install Python 3.12 from python.org
echo and tick "Add python.exe to PATH" during setup, then run this again.
pause
exit /b 1

:failed
echo Installing packages failed. The first run needs an internet connection; later runs don't.
pause
exit /b 1
