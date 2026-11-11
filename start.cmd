@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
set "FitnessExitCode=%errorlevel%"
if not "%FitnessExitCode%"=="0" if not "%FITNESS_NONINTERACTIVE%"=="1" pause
exit /b %FitnessExitCode%
