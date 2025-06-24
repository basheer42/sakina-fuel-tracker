#!/bin/bash

# PythonAnywhere Email Processing Script
# Simplified script for PythonAnywhere scheduled tasks
# Runs every 5 minutes via PythonAnywhere's task scheduler

# === CONFIGURATION (Update these paths) ===
PROJECT_DIR="/home/Basheer42/sakina-fuel-tracker"
VENV_PATH="/home/Basheer42/.virtualenvs/mysite-virtualenv"

# === CHANGE TO PROJECT DIRECTORY ===
cd "$PROJECT_DIR" || exit 1

# === ACTIVATE VIRTUAL ENVIRONMENT AND RUN COMMAND ===
source "$VENV_PATH/bin/activate" && python manage.py process_all_emails

# Note: PythonAnywhere will automatically log output and errors
