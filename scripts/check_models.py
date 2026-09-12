"""Run a bounded model connectivity and citation-contract check; never print secrets."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from app.config import Settings
from app.domain import Chunk, SearchHit
from app.factory import create_answer_generator, create_embedding_model
from app.vector_cache import validate_vectors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("evaluation/reports/model-connectivity.json")
    )
    args = parser.parse_args()
    settings = Settings()
    record: dict[str, object] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "scope": "connectivity and output contract only; not an effectiveness benchmark",
    }
    if settings.embedding_provider == "hashing" or settings.llm_provider == "extractive":
        record["status"] = "not_real_model_mode"
    elif (
        settings.embedding_provider == "openai_compatible"
        and not settings.embedding_api_key
        or settings.llm_provider == "openai_compatible"
        and not settings.llm_api_key
    ):
        record["status"] = "missing_credentials"
    else:
        try:
            model = create_embedding_model(settings)
            vectors = model.embed_documents(["CORS 源由协议、域名和端口组成。"])
            validate_vectors(vectors, 1)
            record["embedding_dimension"] = len(vectors[0])
            text = "源由协议、域名和端口组成。协议、域名或端口不同就属于不同的源。"
            hits = [
                SearchHit(
                    Chunk(
                        "connectivity-1",
                        "connectivity",
                        "connectivity-fixture.txt",
                        text,
                        0,
                        len(text),
                    ),
                    1.0,
                )
            ]
            answer = create_answer_generator(settings).generate("源由哪些部分组成？", hits)
            record["answer_status"] = answer.status
            record["verification"] = answer.verification
            record["status"] = "passed" if answer.status == "answered" else "contract_check_failed"
        except Exception as exc:
            record["status"] = "failed"
            record["error_type"] = type(exc).__name__
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))
    if record["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
