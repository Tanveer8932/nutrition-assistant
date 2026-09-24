"""Run the fixed question set against the live API and write a raw report.

Usage:
    python eval/run_eval.py                       # against http://localhost:8000
    python eval/run_eval.py --base https://<app>  # against a deployment
    python eval/run_eval.py --runs 3

Each question is asked --runs times, each in a fresh conversation, so runs are
independent. Scope tests replay multi-turn conversations.

Output: eval/runs/<timestamp>/results.json and report.md.
The report auto-flags *candidates* (number drift, named authorities, hedging).
A human then reads every answer and fills FAILURE_LOG.md. Nothing here fixes
anything; it only records.
Stdlib only, so it runs with any Python 3.10+.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent

NUMBER_RX = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*(?:-|–|to)\s*\d+(?:[.,]\d+)?)?)\s*"
    r"(%|°\s?[cf]|degrees|g|grams?|mg|mcg|µg|iu|kg|lbs?|ml|l|hours?|hrs?|days?|minutes?|mins?|weeks?|months?|servings?|eggs?|times?|x)?\b",
    re.IGNORECASE,
)
AUTHORITY_RX = re.compile(
    r"\b(WHO|World Health Organi[sz]ation|USDA|FDA|NHS|CDC|EFSA|FSA|Food Standards Agency|NIH|National Academ\w+|"
    r"Institute of Medicine|IOM|Harvard|Mayo Clinic|American Heart Association|AHA|Dietary Guidelines|"
    r"RDA|DRI|according to|studies (show|suggest|have shown)|research (shows|suggests|has shown)|"
    r"a (\d{4} )?study|meta-analys\w+|journal|guidelines? (say|state|recommend))\b",
    re.IGNORECASE,
)
HEDGE_RX = re.compile(
    r"\b(it depends|depends on|varies|may|might|could|some (people|experts|research)|not (entirely )?clear|"
    r"mixed evidence|evidence is mixed|consult|talk to|individual|more research)\b",
    re.IGNORECASE,
)


def post(base: str, message: str, conversation_id: str | None) -> dict:
    body = json.dumps({"message": message, "conversation_id": conversation_id}).encode()
    req = urllib.request.Request(f"{base}/api/chat", data=body, headers={"Content-Type": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read())
            data["_status"] = r.status
    except urllib.error.HTTPError as e:
        data = {"_status": e.code, "_error": e.read().decode(errors="replace")}
    except Exception as e:  # network errors are results too
        data = {"_status": None, "_error": repr(e)}
    data["_latency_s"] = round(time.time() - started, 2)
    return data


def numbers(text: str) -> list[str]:
    out = []
    for value, unit in NUMBER_RX.findall(text):
        v = value.replace(" ", "").replace("–", "-").replace("to", "-")
        out.append(f"{v}{(unit or '').lower().replace(' ', '')}")
    return sorted(set(out))


def run(base: str, runs: int) -> dict:
    spec = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
    results = {"base": base, "runs": runs, "started": datetime.now().isoformat(), "questions": [], "scope_tests": []}

    for q in spec["questions"]:
        entry = {**q, "runs": []}
        for i in range(runs):
            print(f"[{q['id']}] run {i + 1}/{runs}", file=sys.stderr)
            entry["runs"].append(post(base, q["text"], None))
        results["questions"].append(entry)

    for t in spec["scope_tests"]:
        print(f"[{t['id']}] {t['kind']}", file=sys.stderr)
        cid = None
        turns = []
        for msg in t["turns"]:
            r = post(base, msg, cid)
            cid = r.get("conversation_id", cid)
            turns.append({"message": msg, **r})
        final = turns[-1]
        declined = bool(final.get("declined"))
        passed = declined if t["expect"] == "decline" else (not declined and final.get("_status") == 200)
        results["scope_tests"].append({**t, "result": turns, "passed": passed})

    results["finished"] = datetime.now().isoformat()
    return results


def analyse(results: dict) -> dict:
    """Automatic candidate flags. Humans confirm them in FAILURE_LOG.md."""
    for q in results["questions"]:
        ok_runs = [r for r in q["runs"] if r.get("_status") == 200]
        per_run_numbers = []
        for r in ok_runs:
            text = r["response"]["answer"] + " " + " ".join(c["text"] for c in r["response"]["claims"])
            per_run_numbers.append(numbers(text))
        distinct = {tuple(n) for n in per_run_numbers}
        all_text = [r["response"]["answer"] + " " + " ".join(c["text"] for c in r["response"]["claims"]) for r in ok_runs]
        q["flags"] = {
            "errors": len(q["runs"]) - len(ok_runs),
            "declined_runs": sum(1 for r in ok_runs if r.get("declined")),
            "unsourced_claims_per_run": [len(r["response"]["claims"]) for r in ok_runs],
            "numbers_per_run": per_run_numbers,
            "number_drift": len(distinct) > 1,
            "authorities_named": sorted({m.group(0) for t in all_text for m in AUTHORITY_RX.finditer(t)}),
            "hedge_hits_per_run": [len(HEDGE_RX.findall(r["response"]["answer"])) for r in ok_runs],
        }
    return results


def report_md(results: dict) -> str:
    lines = [
        f"# Eval run {results['started']}",
        "",
        f"- Target: `{results['base']}`  ",
        f"- Runs per question: {results['runs']}",
        "",
        "## Auto-flag summary (candidates only; confirm by reading the answers)",
        "",
        "| Q | Category | Unsourced claims/run | Number drift | Authorities named | Hedge hits/run | Declined | Errors |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for q in results["questions"]:
        f = q["flags"]
        lines.append(
            f"| {q['id']} | {q['category']} | {f['unsourced_claims_per_run']} | {'**yes**' if f['number_drift'] else 'no'} | "
            f"{', '.join(f['authorities_named']) or '-'} | {f['hedge_hits_per_run']} | {f['declined_runs']} | {f['errors']} |"
        )

    passed = sum(1 for t in results["scope_tests"] if t["passed"])
    lines += ["", f"## Scope tests: {passed}/{len(results['scope_tests'])} passed", ""]
    lines += ["| Test | Kind | Expect | Final turn declined | Guard stage | Pass |", "|---|---|---|---|---|---|"]
    for t in results["scope_tests"]:
        final = t["result"][-1]
        lines.append(
            f"| {t['id']} | {t['kind']} | {t['expect']} | {final.get('declined')} | {final.get('guard_stage') or '-'} | "
            f"{'PASS' if t['passed'] else '**FAIL**'} |"
        )

    lines += ["", "## Answers", ""]
    for q in results["questions"]:
        lines += [f"### {q['id']} ({q['category']}): {q['text']}", ""]
        for i, r in enumerate(q["runs"], 1):
            if r.get("_status") != 200:
                lines += [f"**Run {i}: ERROR {r.get('_status')}** `{r.get('_error', '')[:300]}`", ""]
                continue
            tag = " (declined)" if r.get("declined") else ""
            lines += [f"**Run {i}{tag}:** {r['response']['answer']}", ""]
            for c in r["response"]["claims"]:
                lines.append(f"- {c['text']}  _(source: {c['source']})_")
            lines.append(f"- numbers: `{q['flags']['numbers_per_run'][i - 1] if i - 1 < len(q['flags']['numbers_per_run']) else []}`")
            lines.append("")
    lines += ["## Scope test transcripts", ""]
    for t in results["scope_tests"]:
        lines += [f"### {t['id']} {t['kind']} ({'PASS' if t['passed'] else 'FAIL'})", ""]
        for turn in t["result"]:
            ans = turn.get("response", {}).get("answer", turn.get("_error", ""))
            lines += [f"> **User:** {turn['message']}", ">", f"> **Assistant{' [declined]' if turn.get('declined') else ''}:** {ans}", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    results = analyse(run(args.base.rstrip("/"), args.runs))
    out = HERE / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "report.md").write_text(report_md(results), encoding="utf-8")
    print(f"Wrote {out / 'report.md'}")


if __name__ == "__main__":
    main()
