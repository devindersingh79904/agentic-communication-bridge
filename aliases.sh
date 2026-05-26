#!/bin/zsh

# Alias to activate the python virtual environment
alias suv="cd /Users/dsp/development/assignment/backend && source .venv/bin/activate"

# Alias to run the FastAPI backend with uvicorn reload
alias uvrun="cd /Users/dsp/development/assignment/backend && uv run uvicorn app.main:app --reload"
