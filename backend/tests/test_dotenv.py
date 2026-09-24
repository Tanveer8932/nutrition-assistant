import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# Import the app package in a fresh interpreter with load_dotenv stubbed, so the
# real backend/.env (and its key) is never read by the test.
PROBE = (
    "import json, dotenv\n"
    "calls = []\n"
    "dotenv.load_dotenv = lambda path, override: calls.append([str(path), override])\n"
    "import app\n"
    "print(json.dumps(calls))\n"
)


def _probe(extra_env: dict) -> list:
    env = {k: v for k, v in os.environ.items() if k != "NUTRITION_ASSISTANT_NO_DOTENV"}
    env.update(extra_env)
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=BACKEND, env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_app_loads_backend_env_file_without_overriding_real_env():
    calls = _probe({})
    assert calls == [[str(BACKEND / ".env"), False]]


def test_dotenv_can_be_disabled():
    assert _probe({"NUTRITION_ASSISTANT_NO_DOTENV": "1"}) == []
