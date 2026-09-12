"""Fetch a small, pinned, licensed FastAPI documentation corpus (no model calls)."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.request import Request, urlopen

REVISIONS = {
    "0.110.0": "e40747f10ae911910e9cb9a9684576f3b21304c9",
    "0.115.0": "40e33e492dbf4af6172997f4e3238a32e56cbe26",
}
PAGES = [
    "tutorial/cors.md",
    "tutorial/background-tasks.md",
    "tutorial/handling-errors.md",
    "tutorial/middleware.md",
    "tutorial/body.md",
    "tutorial/query-params.md",
    "tutorial/path-params.md",
    "tutorial/response-model.md",
    "tutorial/request-files.md",
    "tutorial/request-forms-and-files.md",
    "tutorial/static-files.md",
    "tutorial/dependencies/index.md",
    "tutorial/dependencies/dependencies-with-yield.md",
    "advanced/behind-a-proxy.md",
    "advanced/events.md",
    "deployment/docker.md",
    "deployment/server-workers.md",
]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(revision: str, path: str) -> bytes:
    parts = PurePosixPath(path).parts
    if ".." in parts or path.startswith("/"):
        raise ValueError("Unsafe upstream path")
    url = f"https://raw.githubusercontent.com/fastapi/fastapi/{revision}/{path}"
    with urlopen(
        Request(url, headers={"User-Agent": "knowledge-rag-lab-corpus/0.4"}), timeout=40
    ) as response:
        return response.read()


def get_document(item: tuple[str, str]) -> tuple[str, bytes, dict[str, object]]:
    version, page = item
    revision = REVISIONS[version]
    upstream_path = f"docs/zh/docs/{page}"
    raw = fetch(revision, upstream_path)
    included: dict[str, str] = {}

    def expand(match: re.Match[str]) -> str:
        reference = match.group(1).strip()
        # These pinned documents use the MkDocs include syntax, resolved from docs root.
        index = reference.find("docs_src/")
        if index < 0:
            raise ValueError(f"Unsupported include: {reference}")
        path = reference[index:]
        content = fetch(revision, path)
        included[path] = digest(content)
        return content.decode("utf-8").rstrip()

    text = re.sub(r"\{!([^!]+)!\}", expand, raw.decode("utf-8"))
    content = text.encode("utf-8")
    source = f"fastapi/{version}/{page}"
    return (
        source,
        content,
        {
            "source": source,
            "sha256": digest(content),
            "upstream_sha256": digest(raw),
            "product": "fastapi",
            "version": version,
            "language": "zh",
            "source_revision": revision,
            "included_files": included,
            "upstream_path": upstream_path,
            "upstream_url": f"https://github.com/fastapi/fastapi/blob/{revision}/{upstream_path}",
            "license": "MIT",
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("evaluation/support_corpus"))
    args = parser.parse_args()
    # Complete all remote reads before updating the active corpus manifest.
    with ThreadPoolExecutor(max_workers=4) as pool:
        documents = list(pool.map(get_document, [(v, p) for v in REVISIONS for p in PAGES]))
    license_data = fetch(REVISIONS["0.115.0"], "LICENSE")
    args.output.mkdir(parents=True, exist_ok=True)
    for source, content, _ in documents:
        path = args.output / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (args.output / "LICENSE.fastapi.txt").write_bytes(license_data)
    manifest = {
        "schema_version": "1.0",
        "scenario": "developer-support",
        "upstream": "fastapi/fastapi",
        "revisions": REVISIONS,
        "license_file": "LICENSE.fastapi.txt",
        "transformations": ["Expand MkDocs code includes using the same pinned commit"],
        "documents": [metadata for _, _, metadata in documents],
    }
    temporary = args.output / "manifest.tmp"
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(args.output / "manifest.json")
    print(f"Saved {len(documents)} pinned documents to {args.output}")


if __name__ == "__main__":
    main()
