from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from agui_client import AGUIConversation, DEFAULT_INVOCATIONS_URL, azure_ai_auth_headers


HYPE_WORDS = {
    "awesome",
    "amazing",
    "fantastic",
    "incredible",
    "thrilled",
    "super excited",
    "game changer",
    "revolutionary",
    "boom",
}
COLD_PHRASES = {
    "as stated",
    "obviously",
    "simply impossible",
    "not my problem",
    "you failed",
    "that's wrong",
}
FRIENDLY_MARKERS = {
    "please",
    "thanks",
    "happy to",
    "we can",
    "let's",
    "i can",
    "a practical",
}


def load_cases(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def score_personality(response: str) -> dict:
    lowered = response.lower()
    words = re.findall(r"\b\w+\b", response)
    sentences = [s for s in re.split(r"[.!?]+", response) if s.strip()]
    hype_hits = sorted(term for term in HYPE_WORDS if term in lowered)
    cold_hits = sorted(term for term in COLD_PHRASES if term in lowered)
    friendly_hits = sorted(term for term in FRIENDLY_MARKERS if term in lowered)
    exclamations = response.count("!")
    all_caps = [word for word in words if len(word) > 3 and word.isupper()]

    has_response = bool(response.strip())
    professional = has_response and not cold_hits and not all_caps
    calm = exclamations <= 1 and not hype_hits
    friendly = bool(friendly_hits) or ("sorry" in lowered and "alternative" in lowered)
    concise = len(words) <= 140 and len(sentences) <= 8
    forbidden_style = bool(hype_hits or cold_hits or all_caps or exclamations > 1)

    checks = {
        "has_response": has_response,
        "professional": professional,
        "calm": calm,
        "friendly": friendly,
        "concise": concise,
        "no_forbidden_style": not forbidden_style,
    }
    score = sum(1 for value in checks.values() if value) / len(checks)
    return {
        "score": score,
        "passed": score >= 0.8,
        "checks": checks,
        "signals": {
            "hype_hits": hype_hits,
            "cold_hits": cold_hits,
            "friendly_hits": friendly_hits,
            "exclamations": exclamations,
            "all_caps": all_caps,
            "word_count": len(words),
            "sentence_count": len(sentences),
        },
    }


def generate_response(case: dict, url: str, headers: dict[str, str]) -> dict:
    conversation = AGUIConversation(url, headers=headers)
    response = "".join(
        event.get("delta", "")
        for event in conversation.run(case["query"])
        if event["type"] == "TEXT_MESSAGE_CONTENT"
    )
    return {
        **case,
        "response": response,
        "thread_id": conversation.thread_id,
        "run_id": conversation.last_run_id,
        "correlation_id": conversation.last_correlation_id,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_INVOCATIONS_URL)
    parser.add_argument("--cases", default="evals/personality_cases.jsonl")
    parser.add_argument("--generated")
    parser.add_argument("--out", default="evals/eval_results.json")
    parser.add_argument("--bearer-token")
    args = parser.parse_args()

    if args.generated:
        rows = load_cases(Path(args.generated))
    else:
        headers = {}
        if args.bearer_token:
            headers["Authorization"] = f"Bearer {args.bearer_token}"
        elif args.url.startswith("https://"):
            headers.update(azure_ai_auth_headers())
        rows = [
            generate_response(case, args.url, headers)
            for case in load_cases(Path(args.cases))
        ]

    results = []
    for row in rows:
        personality = score_personality(row["response"])
        results.append({**row, "personality_eval": personality})

    summary = {
        "total": len(results),
        "passed": sum(1 for row in results if row["personality_eval"]["passed"]),
        "min_score": min(row["personality_eval"]["score"] for row in results),
        "average_score": sum(row["personality_eval"]["score"] for row in results)
        / len(results),
    }
    report = {"summary": summary, "rows": results}

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if summary["passed"] != summary["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
