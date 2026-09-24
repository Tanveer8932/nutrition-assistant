# Nutrition Assistant: Milestone 1 prototype

A chatbot for food, nutrition, and food-safety questions. It has **no retrieval layer yet**: it
answers from the model's own memory, and every claim comes back with `source: null`.
Milestone 2 adds retrieval under the same interface, endpoints, and schema.

```
frontend/   Angular 22 chat UI: message list, input, sources panel (empty until M2)
backend/    FastAPI: /api/chat, SQLite storage, Claude call, scope guard
eval/       Fixed question set + runner that writes a raw report per run
FAILURE_LOG.md   Grouped, counted failures from the eval run
```

## Response contract

The model returns structured output (Claude structured outputs, `output_config.format` with a
JSON schema). The backend then validates it with Pydantic, strictly:

```json
{
  "answer": "string",
  "claims": [{ "text": "string", "source": null }]
}
```

- Unknown fields, missing fields, or a non-null `source` fail validation, and the API returns **502**.
  It doesn't retry until something parses.
- `source` is typed `null` in the schema sent to the model and checked again in
  `AssistantResponse.sources_must_be_null`. In Milestone 2 both checks loosen to `string | null`.

`POST /api/chat` wraps that contract with metadata:

```json
{ "conversation_id": "...", "message_id": 12, "response": { "answer": "...", "claims": [] },
  "declined": false, "decline_category": null, "guard_stage": null, "model": "claude-opus-5" }
```

Other endpoints: `GET /api/conversations/{id}`, `GET /api/claims/unsupported` (every claim with
no source, joined to the question that produced it), `GET /api/health`.

## Scope limits (in code, not only in the prompt)

The assistant won't give calorie/intake targets, weight recommendations, or medical advice.
`backend/app/scope_guard.py` enforces this in three layers:

1. **Input guard** runs before the model call. Deterministic patterns cover calorie targets, weight
   goals, conditions, medications, and treatment intent. If one matches, the model is **not called**
   and the reply is a fixed decline pointing to a dietitian or doctor.
2. **Conversation guard**: short follow-ups to a decline ("ok, just roughly?") and later
   references back to a declined request ("going back to earlier…") are declined too.
3. **Output guard** scans the model's answer and claims for per-day calorie figures, weight/BMI
   targets, and condition-specific advice. A match replaces the answer with the decline.

The system prompt also tells the model to decline, but the code checks run no matter what the model does.
The guard leans conservative: a false decline costs a rephrase, and a false answer can hurt
someone. Every decline is stored with the stage and pattern that triggered it.

## Running locally

Backend (Python 3.12):

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # or: source .venv/bin/activate
pip install -r requirements-dev.txt
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn app.main:app --reload --port 8000
pytest -q
```

Frontend:

```bash
cd frontend
npm install
npm start          # http://localhost:4200, /api is proxied to :8000
```

## Deploying

**Backend → Railway**
1. New project → Deploy from GitHub repo → set **Root Directory** to `backend`.
2. Variables: `ANTHROPIC_API_KEY`. Optional: `ANTHROPIC_MODEL`, `ANTHROPIC_EFFORT`.
3. SQLite is lost on each redeploy unless you add a Volume (mount at `/data`) and set
   `DB_PATH=/data/nutrition.db`.
4. Generate a public domain. Check `https://<backend>/api/health`.

**Frontend → Vercel**
1. Import the repo → set **Root Directory** to `frontend` (Framework: Angular).
2. In `frontend/vercel.json`, replace `RAILWAY_BACKEND_URL` with the Railway domain.
   Vercel rewrites `/api/*` to the backend, so the browser only talks to one origin and the API
   key stays on the server.

## Evaluation and the failure log

```bash
python eval/run_eval.py --base https://<your-vercel-app> --runs 3
```

The runner asks each of the 10 questions 3 times in fresh conversations, then replays the
scope-limit conversations (direct, rephrased, sideways, and re-raised after unrelated turns).
It writes `eval/runs/<timestamp>/report.md` with automatic candidate flags: number drift,
named authorities, and hedge phrases. A person reads the answers and records confirmed
failures in `FAILURE_LOG.md`.

**Re-run the full set after every change to `backend/app/prompts.py`** and bump `PROMPT_VERSION`.
Failures get recorded, not patched around. Milestone 2 runs the same set so the results can be compared.
