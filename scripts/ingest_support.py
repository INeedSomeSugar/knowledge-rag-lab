"""Ingest the manifest-verified support corpus as one atomic index update."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from app.config import Settings
from app.corpus import read_documents
from app.factory import create_service


def main() -> None:
    """把语料目录一次性导入索引。

    ``ingest_many`` 在内部完成“构建新快照 → 落盘 → 原子替换引用”，因此中途失败不会
    留下半份索引；``--demo`` 使用 Hashing + 抽取式 + 独立 demo 目录，不触碰正式数据。
    输出里打印缓存命中统计，可直接区分冷建库与复用缓存。
    """
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
