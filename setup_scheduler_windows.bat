@echo off
REM ============================================================
REM  JobRadar - Windows Task Scheduler Setup
REM  Run this ONCE as Administrator to schedule daily searches
REM ============================================================

SET SCRIPT_DIR=%~dp0
SET PYTHON_PATH=python
SET TASK_NAME=JobRadarDailySearch

echo Setting up JobRadar daily search...

REM Create scheduled task for 8 AM daily
schtasks /create /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON_PATH%\" \"%SCRIPT_DIR%main.py\" --run-once" ^
  /sc DAILY ^
  /st 08:00 ^
  /ru "%USERNAME%" ^
  /f

IF %ERRORLEVEL% EQU 0 (
    echo.
    echo [SUCCESS] Daily job search scheduled at 8:00 AM
    echo Task name: %TASK_NAME%
    echo.
    echo To change the time: schtasks /change /tn "%TASK_NAME%" /st HH:MM
    echo To delete: schtasks /delete /tn "%TASK_NAME%" /f
    echo To run now: schtasks /run /tn "%TASK_NAME%"
) ELSE (
    echo.
    echo [ERROR] Failed to create scheduled task.
    echo Try running as Administrator.
)

echo.
echo Starting dashboard server...
start "" "%PYTHON_PATH%" "%SCRIPT_DIR%main.py" --dashboard

pause
