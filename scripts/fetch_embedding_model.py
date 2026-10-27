"""Download only public E5 safetensors/tokenizer files at an immutable commit."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request

from app.local_embeddings import E5_DIRECTORY, E5_FILES, E5_MODEL, E5_REVISION, sha256_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=E5_DIRECTORY)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    api = f"https://huggingface.co/api/models/{E5_MODEL}/revision/{E5_REVISION}?blobs=true"
    with urllib.request.urlopen(api, timeout=60) as response:
        info = json.load(response)
    if info["sha"] != E5_REVISION or info.get("cardData", {}).get("license") != "mit":
        raise ValueError("上游模型提交或许可证与已核对约定不同")
    siblings = {item["rfilename"]: item for item in info["siblings"]}
    manifest = {
        "model": E5_MODEL,
        "revision": E5_REVISION,
        "license": "mit",
        "model_card": f"https://huggingface.co/{E5_MODEL}/blob/{E5_REVISION}/README.md",
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "files": {},
    }
    for name in E5_FILES:
        target = args.directory / name
        expected = siblings[name].get("lfs", {}).get("sha256")
        url = f"https://huggingface.co/{E5_MODEL}/resolve/{E5_REVISION}/{name}"
        if not target.exists() or (expected and sha256_file(target) != expected):
            temporary = target.with_suffix(target.suffix + ".part")
            print(f"Downloading {name}", flush=True)
            with urllib.request.urlopen(url, timeout=180) as response, temporary.open("wb") as out:
                while block := response.read(1024 * 1024):
                    out.write(block)
            if expected and sha256_file(temporary) != expected:
                raise ValueError(f"上游LFS SHA256校验失败：{name}")
            temporary.replace(target)
        manifest["files"][name] = {"sha256": sha256_file(target), "bytes": target.stat().st_size}
    (args.directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "model": E5_MODEL,
                "revision": E5_REVISION,
                "bytes": sum(f["bytes"] for f in manifest["files"].values()),
            }
        )
    )


if __name__ == "__main__":
    main()
