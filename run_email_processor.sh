#!/bin/bash

# Activate virtual environment
source /home/Basheer42/.virtualenvs/mysite-virtualenv/bin/activate

# Change to project directory
cd /home/Basheer42/sakina-fuel-tracker

# Run the email processor
python email_processor.py
