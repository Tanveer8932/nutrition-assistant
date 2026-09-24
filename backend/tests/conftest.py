import os

# Keep tests independent of a developer's backend/.env (see app/__init__.py).
os.environ.setdefault("NUTRITION_ASSISTANT_NO_DOTENV", "1")
