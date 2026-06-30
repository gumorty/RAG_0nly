import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib import error, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.rag.eval_metrics import (  # noqa: E402
    citation_readability_rate,
    duplicate_line_count,
    keyword_answer_score,
    unsupported_claim_heuristic,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RAG answer-quality evaluation cases through the management API.")
    parser.add_argument("--evalset", default="evalsets/lab_weekly_rag_eval.json")
    parser.add_argument("--api-base", default=os.getenv("RAG_API_BASE", "http://localhost:18010/api"))
    parser.add_argument("--email", default=os.getenv("RAG_EVAL_EMAIL", "admin@example.com"))
    parser.add_argument("--password", default=os.getenv("RAG_EVAL_PASSWORD", "Admin@123456"))
    parser.add_argument("--out", default="data/eval/latest-rag-eval-report.json")
    args = parser.parse_args()

    evalset_path = Path(args.evalset)
    evalset = json.loads(evalset_path.read_text(encoding="utf-8"))
    token = login(args.api_base, args.email, args.password)
    collection = find_collection(args.api_base, token, evalset["collection_name"])
    if not collection:
        raise SystemExit(f"Collection not found: {evalset['collection_name']}")

    report = {
        "evalset": evalset.get("name"),
        "collection_id": collection["id"],
        "collection_name": collection["name"],
        "started_at": now(),
        "api_base": args.api_base,
        "cases": [],
    }

    for case in evalset.get("cases", []):
        result = run_case(args.api_base, token, collection["id"], evalset, case)
        report["cases"].append(result)
        mark = "PASS" if result["passed"] else "FAIL"
        print(f"{mark} {case['id']} evidence={result['evidence_score']:.3f} citations={result['citation_count']}")
        for reason in result["reasons"]:
            print(f"  - {reason}")

    passed = sum(1 for item in report["cases"] if item["passed"])
    total = len(report["cases"])
    report["finished_at"] = now()
    report["summary"] = {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": passed / total if total else 0,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {out.resolve()}")
    return 0 if passed == total else 1


def run_case(api_base: str, token: str, collection_id: str, evalset: dict, case: dict) -> dict:
    response = post(
        api_base,
        "/chat",
        token,
        {
            "collection_id": collection_id,
            "question": case["question"],
            "history": [],
        },
        timeout=180,
    )
    answer = response.get("answer") or ""
    citations = response.get("citations") or []
    reasons = []
    keyword_metrics = keyword_answer_score(answer, case.get("must_include", []), case.get("must_not_include", []))
    unsupported_metrics = unsupported_claim_heuristic(answer, citations)

    for keyword in keyword_metrics["missing_keywords"]:
        reasons.append(f"missing keyword in answer: {keyword}")
    for keyword in keyword_metrics["forbidden_keywords"]:
        reasons.append(f"forbidden text in answer: {keyword}")

    min_citations = int(case.get("min_citations", evalset.get("min_citations", 1)))
    if len(citations) < min_citations:
        reasons.append(f"citation count {len(citations)} < {min_citations}")

    min_score = float(case.get("min_evidence_score", evalset.get("min_evidence_score", 0)))
    evidence_score = float(response.get("evidence_score") or 0)
    if evidence_score < min_score:
        reasons.append(f"evidence score {evidence_score:.3f} < {min_score:.3f}")

    readability = citation_readability_rate(citations)
    if citations and readability < float(case.get("min_citation_readability_rate", 1.0)):
        reasons.append(f"citation readability {readability:.3f} below requirement")

    duplicate_lines = duplicate_line_count(answer)
    if duplicate_lines:
        reasons.append(f"duplicate answer lines: {duplicate_lines}")

    return {
        "id": case["id"],
        "question": case["question"],
        "passed": not reasons,
        "reasons": reasons,
        "answer_preview": answer[:1200],
        "answer_id": response.get("answer_id"),
        "trace_id": response.get("trace_id"),
        "model": response.get("model"),
        "evidence_score": evidence_score,
        "citation_count": len(citations),
        "duplicate_lines": duplicate_lines,
        "metrics": {
            "keyword": keyword_metrics,
            "citation_readability_rate": readability,
            "unsupported_claim": unsupported_metrics,
        },
        "citations": [
            {
                "title": c.get("title"),
                "score": c.get("score"),
                "preview": (c.get("preview") or c.get("content") or "")[:300],
            }
            for c in citations[:8]
        ],
    }


def readable_citation(citation: dict) -> bool:
    text = " ".join(str(citation.get(key) or "") for key in ("preview", "content")).strip()
    if len(text) < 24:
        return False
    if "引用片段不可读" in text:
        return False
    if "???" in text:
        return False
    return True


def count_duplicate_lines(text: str) -> int:
    seen = set()
    duplicates = 0
    for line in text.splitlines():
        normalized = " ".join(line.split())
        if not normalized:
            continue
        if normalized in seen:
            duplicates += 1
        seen.add(normalized)
    return duplicates


def login(api_base: str, email: str, password: str) -> str:
    body = post(api_base, "/auth/login", "", {"email": email, "password": password})
    return body["access_token"]


def find_collection(api_base: str, token: str, name: str) -> dict | None:
    for item in get(api_base, "/collections", token):
        if item.get("name") == name:
            return item
    return None


def get(api_base: str, path: str, token: str):
    return http_json("GET", f"{api_base}{path}", token=token)


def post(api_base: str, path: str, token: str, payload: dict, timeout: int = 60):
    return http_json("POST", f"{api_base}{path}", token=token, payload=payload, timeout=timeout)


def http_json(method: str, url: str, token: str = "", payload: dict | None = None, timeout: int = 60):
    data = None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code} {detail}") from exc


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


if __name__ == "__main__":
    sys.exit(main())
