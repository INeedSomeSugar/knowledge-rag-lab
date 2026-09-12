from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterator

from app.loaders import SUPPORTED_SUFFIXES, load_bytes


def read_documents(directory: Path) -> Iterator[tuple[str, str, dict[str, object]]]:
    """Load only manifest-listed files when available, checking provenance before use."""
    directory = directory.resolve()
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = manifest["documents"]
    else:
        entries = [
            {"source": path.relative_to(directory).as_posix()}
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        ]
    seen: set[str] = set()
    for entry in entries:
        source = entry["source"]
        path = (directory / source).resolve()
        if not path.is_relative_to(directory) or source in seen:
            raise ValueError(f"语料路径越界或重复：{source}")
        seen.add(source)
        data = path.read_bytes()
        if entry.get("sha256") and hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"语料哈希不匹配：{source}")
        text = load_bytes(path.name, data).strip()
        if not text:
            raise ValueError(f"文档解析为空：{source}")
        metadata = {key: entry[key] for key in (
            "product", "version", "language", "source_revision", "upstream_url", "sha256"
        ) if key in entry}
        yield source, text, metadata
