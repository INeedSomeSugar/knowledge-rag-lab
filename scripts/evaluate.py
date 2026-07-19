from __future__ import annotations

import json
from pathlib import Path

from app.evaluation import EvaluationCase, evaluate_retrieval
from app.factory import create_service


def main() -> None:
    service = create_service()
    cases = []
    for line in Path("evaluation/questions.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        cases.append(
            EvaluationCase(
                question=value["question"],
                relevant_sources=frozenset(value["relevant_sources"]),
            )
        )
    result = evaluate_retrieval(cases, service.search, top_k=3)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
