"""Ingest the manifest-verified support corpus as one atomic index update."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from app.config import Settings
from app.corpus import read_documents
from app.factory import create_service


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, default=Path("evaluation/support_corpus"))
    parser.add_argument(
        "--demo", action="store_true", help="使用 Hashing/抽取式模式，不调用模型 API"
    )
    args = parser.parse_args()
    settings = replace(Settings(), chunking_strategy="sections")
    if args.demo:
        settings = replace(
            settings,
            embedding_provider="hashing",
            llm_provider="extractive",
            retrieval_strategy="bm25",
            data_dir=Path("data/demo-index"),
        )
    service = create_service(settings)
    results = service.ingest_many(list(read_documents(args.documents)))
    print(
        f"Imported {len(results)} documents / {sum(item['chunk_count'] for item in results)} chunks"
    )
    print(f"Index: {service.index_revision}; products: {service.catalog()}")
    print(
        f"Embedding: {settings.embedding_provider}; cache: {service.retriever.dense.embedding_model.last_stats}"
    )


if __name__ == "__main__":
    main()
