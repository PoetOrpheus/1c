@echo off
cd /d "%~dp0"

if exist "%~dp0portable\Raskroy.exe" (
    start "" "%~dp0portable\Raskroy.exe"
    exit /b 0
)

echo Application file not found:
echo "%~dp0portable\Raskroy.exe"
pause
