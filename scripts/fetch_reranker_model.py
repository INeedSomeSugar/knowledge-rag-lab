"""Download only pinned public BGE safetensors/tokenizer files; no model execution."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request

from app.cross_encoder import BGE_DIRECTORY, BGE_FILES, BGE_MODEL, BGE_REVISION
from app.local_embeddings import sha256_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=BGE_DIRECTORY)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/api/models/{BGE_MODEL}/revision/{BGE_REVISION}?blobs=true"
    with urllib.request.urlopen(url, timeout=60) as response:
        info = json.load(response)
    if info["sha"] != BGE_REVISION or info.get("cardData", {}).get("license") != "mit":
        raise ValueError("上游提交或许可证不符")
    files = {s["rfilename"]: s for s in info["siblings"]}
    manifest = {
        "model": BGE_MODEL,
        "revision": BGE_REVISION,
        "license": "mit",
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "files": {},
    }
    for name in BGE_FILES:
        target = args.directory / name
        expected = files[name].get("lfs", {}).get("sha256")
        if not target.exists() or (expected and sha256_file(target) != expected):
            print(f"Downloading {name}", flush=True)
            temporary = target.with_suffix(target.suffix + ".part")
            source = (
                f"https://huggingface.co/{BGE_MODEL}/resolve/{BGE_REVISION}/{name}?download=true"
            )
            with (
                urllib.request.urlopen(source, timeout=180) as response,
                temporary.open("wb") as out,
            ):
                while block := response.read(1024 * 1024):
                    out.write(block)
            if expected and sha256_file(temporary) != expected:
                raise ValueError(f"LFS SHA256不符：{name}")
            temporary.replace(target)
        manifest["files"][name] = {"sha256": sha256_file(target), "bytes": target.stat().st_size}
    (args.directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "model": BGE_MODEL,
                "revision": BGE_REVISION,
                "bytes": sum(f["bytes"] for f in manifest["files"].values()),
            }
        )
    )


if __name__ == "__main__":
    main()
