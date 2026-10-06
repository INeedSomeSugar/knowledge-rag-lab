"""Start the local single-worker demo or configured API-backed service."""

from __future__ import annotations

import argparse
import os
import logging
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", choices=["127.0.0.1", "0.0.0.0", "::1"], default="127.0.0.1")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--bootstrap", action="store_true", help="服务启动前导入固定版本官方语料")
    parser.add_argument("--documents", type=Path, default=Path("evaluation/support_corpus"))
    parser.add_argument("--context-policy", choices=["neighbors", "merged_neighbors"])
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port 必须在 1 到 65535 之间")
    if args.demo:
        os.environ["EMBEDDING_PROVIDER"] = "hashing"
        os.environ["LLM_PROVIDER"] = "extractive"
        os.environ["DATA_DIR"] = "data/demo-index"
        os.environ["RETRIEVAL_STRATEGY"] = "bm25"
        os.environ["CHUNKING_STRATEGY"] = "sections"
    if args.data_dir is not None:
        os.environ["DATA_DIR"] = str(args.data_dir)
    if args.context_policy is not None:
        os.environ["CONTEXT_POLICY"] = args.context_policy
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import uvicorn
    from app.main import app, service

    if args.bootstrap:
        from app.corpus import read_documents

        result = service.ingest_many(list(read_documents(args.documents)))
        logging.getLogger("rag.startup").info("Imported %d source documents", len(result))

    uvicorn.run(app, host=args.host, port=args.port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
