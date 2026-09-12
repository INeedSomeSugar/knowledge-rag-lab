"""Start the local single-worker demo or configured API-backed service."""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.demo:
        os.environ["EMBEDDING_PROVIDER"] = "hashing"
        os.environ["LLM_PROVIDER"] = "extractive"
        os.environ["DATA_DIR"] = "data/demo-index"
        os.environ["RETRIEVAL_STRATEGY"] = "bm25"
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
