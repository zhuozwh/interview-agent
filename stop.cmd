@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop.ps1"
set "interviewAgentExitCode=%errorlevel%"
if not "%interviewAgentExitCode%"=="0" pause
exit /b %interviewAgentExitCode%
