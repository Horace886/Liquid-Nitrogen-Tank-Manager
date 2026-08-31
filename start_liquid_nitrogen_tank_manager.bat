@echo off
chcp 65001 >nul
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%~dp0liquid_nitrogen_tank_manager.py"
) else (
    start "" pythonw "%~dp0liquid_nitrogen_tank_manager.py"
)
