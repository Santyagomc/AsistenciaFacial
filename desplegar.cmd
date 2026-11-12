@echo off
REM Lanza desplegar.ps1 sin cambiar la politica de ejecucion de PowerShell.
REM Uso: desplegar.cmd verificar ^| vps ^| firmware ^| ota ^| token ^| monitor ^| todo
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0desplegar.ps1" %*
