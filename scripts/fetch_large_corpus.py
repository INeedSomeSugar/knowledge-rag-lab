"""Fetch a larger pinned FastAPI corpus: several versions x more pages.

This is an additive experiment helper. It never writes into
``evaluation/support_corpus``; the frozen 34-document corpus, its manifest and
the 47 reviewed questions keep their original bytes. Documents that are absent
at an old revision are skipped and recorded in the manifest instead of failing
the whole run, because the Chinese docs gained pages over time.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, getproxies, urlopen

REVISIONS = {
    "0.110.0": "e40747f10ae911910e9cb9a9684576f3b21304c9",
    "0.115.0": "40e33e492dbf4af6172997f4e3238a32e56cbe26",
    "0.117.1": "784f06cb9b7cc63f6a0cb2bc9cf238473eef93e2",
    "0.118.0": "333f1ba737be6507fc707278f6b69cf1f81efdc1",
}

PAGES = [
    # Baseline pages already used by the frozen 34-document corpus.
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
    # Additional pages to grow the corpus and the candidate pool.
    "tutorial/first-steps.md",
    "tutorial/path-params-numeric-validations.md",
    "tutorial/query-params-str-validations.md",
    "tutorial/body-multiple-params.md",
    "tutorial/body-fields.md",
    "tutorial/body-nested-models.md",
    "tutorial/response-status-code.md",
    "advanced/response-headers.md",
    "tutorial/security/first-steps.md",
    "tutorial/dependencies/classes-as-dependencies.md",
    "tutorial/bigger-applications.md",
    "advanced/response-directly.md",
    "deployment/https.md",
]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


NETWORK_FAILURES: list[dict] = []
TRANSPORT = "urllib"


def curl_fetch(url: str) -> bytes:
    executable = shutil.which("curl")
    if not executable:
        raise ValueError("curl transport requested but curl is missing")
    command = [
        executable,
        "--fail",
        "--silent",
        "--show-error",
        "--max-time",
        "40",
        "--proto",
        "=https",
        "--write-out",
        "\n%{http_code}",
    ]
    proxy = getproxies().get("https")
    if proxy:
        # Never place proxy credentials in a subprocess argument or error log.
        parsed = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
        if parsed.username or parsed.password:
            raise ValueError("Authenticated system proxy unsupported by curl transport")
        command += ["--proxy", proxy]
    result = subprocess.run([*command, url], capture_output=True, timeout=45, check=False)
    content, _, code = result.stdout.rpartition(b"\n")
    if code == b"404":
        raise HTTPError(url, 404, "Not Found", {}, None)
    if result.returncode or code != b"200":
        raise URLError(f"curl exit={result.returncode}, HTTP={code.decode(errors='replace')}")
    return content


def fetch(revision: str, path: str) -> bytes:
    parts = PurePosixPath(path).parts
    if (
        not re.fullmatch(r"[0-9a-f]{40}", revision)
        or ".." in parts
        or path.startswith("/")
        or "\\" in path
        or ":" in path
    ):
        raise ValueError(f"Unsafe upstream path: {path}")
    url = f"https://raw.githubusercontent.com/fastapi/fastapi/{revision}/{path}"
    for attempt in range(1, 4):
        try:
            if TRANSPORT == "curl":
                return curl_fetch(url)
            with urlopen(
                Request(url, headers={"User-Agent": "knowledge-rag-lab-corpus/large"}), timeout=40
            ) as response:
                return response.read()
        except (HTTPError, URLError, OSError, subprocess.TimeoutExpired) as error:
            NETWORK_FAILURES.append(
                {"revision": revision, "path": path, "attempt": attempt, "reason": str(error)}
            )
            if attempt == 3 or isinstance(error, HTTPError) and error.code < 500:
                raise
            sleep(attempt)
    raise RuntimeError("Unreachable fetch state")


def get_document(item: tuple[str, str]) -> dict[str, object]:
    version, page = item
    revision = REVISIONS[version]
    upstream_path = f"docs/zh/docs/{page}"
    try:
        raw = fetch(revision, upstream_path)
    except (HTTPError, URLError, OSError, subprocess.TimeoutExpired) as error:
        return {
            "status": "skipped",
            "version": version,
            "page": page,
            "reason": f"upstream fetch failed: {error}",
        }

    included: dict[str, str] = {}
    include_cache: dict[str, bytes] = {}

    def code_file(reference: str) -> bytes:
        index = reference.find("docs_src/")
        if index < 0:
            raise ValueError(f"Unsupported include: {reference}")
        path = reference[index:]
        if path not in include_cache:
            include_cache[path] = fetch(revision, path)
        content = include_cache[path]
        included[path] = digest(content)
        return content

    def expand(match: re.Match[str]) -> str:
        reference = match.group(1).strip()
        return code_file(reference).decode("utf-8").rstrip()

    def expand_star(match: re.Match[str]) -> str:
        reference, options = match.group(1), match.group(2)
        lines = code_file(reference).decode("utf-8").splitlines(keepends=True)
        selection = re.search(r"ln\[(\d+):(\d+)\]", options)
        if "ln[" in options:
            if not selection:
                raise ValueError(f"Unsupported include line range: {options}")
            start, end = map(int, selection.groups())
            if not 1 <= start <= end <= len(lines):
                raise ValueError("Invalid include line range")
            lines = lines[start - 1 : end]
        return "```python\n" + "".join(lines).rstrip() + "\n```"

    try:
        text = re.sub(r"\{!([^!]+)!\}", expand, raw.decode("utf-8"))
        text = re.sub(r"\{\*\s*(\S+)([^*]*)\*\}", expand_star, text)
        if any(marker in text for marker in ("{!", "!}", "{*", "*}")):
            raise ValueError("Unexpanded MkDocs include")
    except (HTTPError, URLError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {
            "status": "skipped",
            "version": version,
            "page": page,
            "reason": f"include expansion failed: {error}",
        }

    content = text.encode("utf-8")
    source = f"fastapi/{version}/{page}"
    return {
        "status": "ok",
        "source": source,
        "content": content,
        "metadata": {
            "source": source,
            "sha256": digest(content),
            "upstream_sha256": digest(raw),
            "product": "fastapi",
            "version": version,
            "language": "zh",
            "source_revision": revision,
            "included_files": included,
            "upstream_path": upstream_path,
            "upstream_url": f"https://raw.githubusercontent.com/fastapi/fastapi/{revision}/{upstream_path}",
            "license": "MIT",
            "acquisition": "fetched_from_pinned_raw",
        },
    }


def reuse_baseline(baseline: Path) -> list[dict[str, object]]:
    """Copy bytes and metadata without making any upstream requests for old sources."""
    manifest = json.loads((baseline / "manifest.json").read_text(encoding="utf-8"))
    expected = {f"fastapi/{v}/{p}" for v in ("0.110.0", "0.115.0") for p in PAGES[:17]}
    if {d["source"] for d in manifest["documents"]} != expected:
        raise ValueError("Frozen baseline must contain exactly the original 17 pages x 2 versions")
    documents = []
    for metadata in manifest["documents"]:
        content = (baseline / metadata["source"]).read_bytes()
        if digest(content) != metadata["sha256"]:
            raise ValueError(f"Frozen baseline hash mismatch: {metadata['source']}")
        if metadata["source_revision"] != REVISIONS[metadata["version"]]:
            raise ValueError(f"Frozen baseline revision mismatch: {metadata['source']}")
        documents.append(
            {
                "status": "ok",
                "source": metadata["source"],
                "content": content,
                "metadata": {**metadata, "acquisition": "byte_copy_from_frozen_baseline"},
            }
        )
    return documents


def build_corpus(output: Path, baseline: Path) -> dict:
    if output.exists():
        raise ValueError("Output must not exist; frozen and historical corpora are immutable")
    if output.resolve().is_relative_to(baseline.resolve()):
        raise ValueError("Output must be outside frozen baseline")
    started = datetime.now(timezone.utc).isoformat()
    reused = reuse_baseline(baseline)
    existing = {d["source"] for d in reused}
    NETWORK_FAILURES.clear()
    # A live pinned raw read is required before publication; no network-cache fallback.
    license_data = fetch(REVISIONS["0.118.0"], "LICENSE")
    pending = [(v, p) for v in REVISIONS for p in PAGES if f"fastapi/{v}/{p}" not in existing]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(get_document, pending))
    documents = reused + [item for item in results if item["status"] == "ok"]
    skipped = [item for item in results if item["status"] != "ok"]
    # Save only into a new directory, even if another process created it during fetching.
    output.mkdir(parents=True, exist_ok=False)
    for item in documents:
        path = output / str(item["source"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(item["content"])
    with (output / "LICENSE.fastapi.txt").open("xb") as stream:
        stream.write(license_data)
    manifest = {
        "schema_version": "1.1",
        "scenario": "developer-support-large",
        "created_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "upstream": "fastapi/fastapi",
        "transport": TRANSPORT,
        "revisions": REVISIONS,
        "requested_pages": PAGES,
        "requested_document_count": len(REVISIONS) * len(PAGES),
        "document_count": len(documents),
        "version_counts": dict(
            sorted(Counter(d["metadata"]["version"] for d in documents).items())
        ),
        "baseline": {
            "directory": baseline.as_posix(),
            "manifest_sha256": digest((baseline / "manifest.json").read_bytes()),
            "reused_document_count": len(reused),
        },
        "license_file": "LICENSE.fastapi.txt",
        "license_sha256": digest(license_data),
        "license_upstream_url": f"https://raw.githubusercontent.com/fastapi/fastapi/{REVISIONS['0.118.0']}/LICENSE",
        "transformations": [
            "Byte-copy all original sources and preserve original metadata",
            "New sources: expand MkDocs code includes using the same pinned commit",
            "Expand both {!...!} and {*...*}; honor inclusive ln[start:end], omit display highlighting",
        ],
        "documents": [item["metadata"] for item in documents],
        "skipped": [{key: item[key] for key in ("version", "page", "reason")} for item in skipped],
        "network_failures": sorted(
            NETWORK_FAILURES, key=lambda f: (f["revision"], f["path"], f["attempt"])
        ),
    }
    with (output / "manifest.json").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main() -> None:
    global TRANSPORT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/support_corpus_large"),
    )
    parser.add_argument("--baseline", type=Path, default=Path("evaluation/support_corpus"))
    parser.add_argument("--transport", choices=["urllib", "curl"], default="urllib")
    args = parser.parse_args()
    TRANSPORT = args.transport
    try:
        manifest = build_corpus(args.output, args.baseline)
    except (ValueError, OSError, URLError) as error:
        parser.exit(1, f"抓取失败（未使用网络缓存替代）：{error}\n")
    print(
        f"文档总数：{manifest['document_count']}；复用：{manifest['baseline']['reused_document_count']}"
    )
    for version, count in manifest["version_counts"].items():
        print(f"  {version}: {count}")
    print(f"跳过：{len(manifest['skipped'])}")
    for item in manifest["skipped"]:
        print(f"  {item['version']} {item['page']} -> {item['reason']}")
    if manifest["document_count"] < 100:
        raise SystemExit("文档不足100，已保存真实抓取和跳过清单；不进入评测")


if __name__ == "__main__":
    main()
