"""Load backend/.env before any submodule reads configuration.

llm.py and db.py read environment variables at import time, so this has to
run first; the package __init__ is the one place guaranteed to do that however
the app is started (uvicorn, tests, scripts). Real environment variables win
over .env (override=False). Tests set NUTRITION_ASSISTANT_NO_DOTENV=1 so a
developer's .env can't change their behaviour.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

if not os.environ.get("NUTRITION_ASSISTANT_NO_DOTENV"):
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
