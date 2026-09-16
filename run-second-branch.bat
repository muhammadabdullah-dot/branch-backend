@echo off
REM A second, completely separate branch server on this PC — for testing branch-to-branch transfers.
REM It has its own database (branch-second.db), its own pictures folder and its own port (4176).
REM Model Town's branch.db is never opened by this window.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up the virtual environment for the first time...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
)

set DB_URL=sqlite://./branch-second.db
set PORT=4176
set MEDIA_DIR=./media-second

echo Preparing the second branch's database (branch-second.db)...
".venv\Scripts\aerich.exe" upgrade

".venv\Scripts\python.exe" -m app.main

echo.
echo Second branch server stopped.
pause
