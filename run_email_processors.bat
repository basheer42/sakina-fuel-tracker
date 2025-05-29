@echo off
REM Batch script to run Django email processing management commands

REM Set the project directory (IMPORTANT: Make sure this is correct for your setup)
set PROJECT_DIR=C:\Users\USER\Desktop\my_fuel_tracker\

REM Set the path to your Python executable within the virtual environment
set PYTHON_EXE=%PROJECT_DIR%venv\Scripts\python.exe

REM Set the path to manage.py
set MANAGE_PY=%PROJECT_DIR%manage.py

REM Log file
set LOG_FILE=%PROJECT_DIR%email_processing.log

REM --- Activate Virtual Environment (usually not strictly needed if calling python.exe directly from venv) ---
REM However, some manage.py commands might behave better if the venv's PATH is fully active.
REM If direct python.exe call works, this can be skipped. For robustness, let's include it.
REM echo Activating virtual environment... >> %LOG_FILE%
REM call %PROJECT_DIR%venv\Scripts\activate.bat >> %LOG_FILE% 2>&1

echo --- Starting Email Processing Cycle at %date% %time% --- >> %LOG_FILE%

REM Run the status emails command
echo Running process_status_emails at %date% %time% >> %LOG_FILE%
%PYTHON_EXE% %MANAGE_PY% process_status_emails >> %LOG_FILE% 2>&1
echo Finished process_status_emails. Exit code: %ERRORLEVEL% >> %LOG_FILE%

REM Run the BoL emails command
echo Running process_bol_emails at %date% %time% >> %LOG_FILE%
%PYTHON_EXE% %MANAGE_PY% process_bol_emails >> %LOG_FILE% 2>&1
echo Finished process_bol_emails. Exit code: %ERRORLEVEL% >> %LOG_FILE%

REM --- Deactivate Virtual Environment (optional, script will end anyway) ---
REM echo Deactivating virtual environment... >> %LOG_FILE%
REM call %PROJECT_DIR%venv\Scripts\deactivate.bat >> %LOG_FILE% 2>&1

echo --- Email Processing Cycle Ended at %date% %time% --- >> %LOG_FILE%
echo. >> %LOG_FILE% 