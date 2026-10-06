"""Download immutable official snapshots for the version behavior lab (no models)."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re

from scripts.fetch_support_corpus import digest, fetch

REVISIONS = {
    "0.117.1": "784f06cb9b7cc63f6a0cb2bc9cf238473eef93e2",
    "0.118.0": "333f1ba737be6507fc707278f6b69cf1f81efdc1",
}
PAGES = (
    "advanced/advanced-dependencies.md",
    "tutorial/dependencies/dependencies-with-yield.md",
    "release-notes.md",
)


def get_document(item: tuple[str, str]) -> tuple[str, bytes, dict]:
    version, page = item
    revision = REVISIONS[version]
    upstream = f"docs/en/docs/{page}"
    raw = fetch(revision, upstream)
    included: dict[str, str] = {}
    cache: dict[str, bytes] = {}

    def expand(match: re.Match) -> str:
        reference, options = match.group(1), match.group(2)
        if not reference.startswith("../../docs_src/") or ".." in reference[6:]:
            raise ValueError("Unsupported official code reference")
        path = reference[6:]
        if path not in cache:
            cache[path] = fetch(revision, path)
        content = cache[path]
        included[path] = digest(content)
        lines = content.decode("utf-8").splitlines(keepends=True)
        selection = re.search(r"ln\[(\d+):(\d+)\]", options)
        if selection:
            start, end = map(int, selection.groups())
            if not 1 <= start <= end <= len(lines):
                raise ValueError("Invalid code line range")
            lines = lines[start - 1:end]
        return "```python\n" + "".join(lines).rstrip() + "\n```"

    text = re.sub(r"\{\*\s*(\S+)([^*]*)\*\}", expand, raw.decode("utf-8"))
    if "{*" in text or "{!" in text:
        raise ValueError("Unresolved official include")
    source = f"fastapi/{version}/{page}"
    content = text.encode("utf-8")
    return source, content, {
        "source": source, "sha256": digest(content), "upstream_sha256": digest(raw),
        "product": "fastapi", "version": version, "language": "en",
        "source_revision": revision, "included_files": included,
        "upstream_path": upstream,
        "upstream_url": f"https://github.com/fastapi/fastapi/blob/{revision}/{upstream}",
        "license": "MIT",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("evaluation/version_corpus"))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; use a new directory to preserve snapshots")
    with ThreadPoolExecutor(max_workers=4) as pool:
        documents = list(pool.map(get_document, [(v, p) for v in REVISIONS for p in PAGES]))
    license_data = fetch(REVISIONS["0.118.0"], "LICENSE")
    # No files published until all downloads and include validation have succeeded.
    args.output.mkdir(parents=True)
    for source, content, _ in documents:
        path = args.output / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (args.output / "LICENSE.fastapi.txt").write_bytes(license_data)
    manifest = {
        "schema_version": "1.0", "scenario": "version-behavior-lab",
        "upstream": "fastapi/fastapi", "revisions": REVISIONS,
        "license_file": "LICENSE.fastapi.txt",
        "transformations": [
            "Expand code directives from the same commit as fenced Python",
            "Honor inclusive 1-based ln[start:end] ranges; omit display highlighting",
        ],
        "documents": [metadata for _, _, metadata in documents],
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Saved {len(documents)} pinned official documents to {args.output}")


if __name__ == "__main__":
    main()
