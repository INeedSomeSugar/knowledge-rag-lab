"""Add pinned public pages without changing historical corpora. No model API calls."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil

from scripts import fetch_large_corpus as upstream
from scripts.finetune_retriever.common import now, read_json, sha, write_json

# Chosen before querying any retrieval model. All versions inherit these groups.
GROUPS = {
    "request_parameters": ("train", [
        "tutorial/header-params.md", "tutorial/cookie-params.md",
        "tutorial/path-params-numeric-validations.md", "tutorial/query-params-str-validations.md",
        "tutorial/query-params.md", "tutorial/path-params.md",
    ]),
    "body_schema": ("train", [
        "tutorial/body-fields.md", "tutorial/body-multiple-params.md",
        "tutorial/body-nested-models.md", "tutorial/extra-data-types.md",
        "tutorial/extra-models.md", "tutorial/body.md",
    ]),
    "response_types": ("train", [
        "tutorial/response-status-code.md", "advanced/response-headers.md",
        "advanced/response-directly.md", "advanced/custom-response.md",
        "advanced/additional-responses.md", "advanced/response-cookies.md",
        "tutorial/response-model.md",
    ]),
    "dependency_graphs": ("train", [
        "tutorial/dependencies/classes-as-dependencies.md",
        "tutorial/dependencies/sub-dependencies.md",
        "tutorial/dependencies/dependencies-in-path-operation-decorators.md",
        "tutorial/dependencies/global-dependencies.md", "tutorial/dependencies/index.md",
        "tutorial/dependencies/dependencies-with-yield.md",
    ]),
    "security": ("train", [
        "tutorial/security/first-steps.md", "tutorial/security/simple-oauth2.md",
        "tutorial/security/oauth2-jwt.md", "advanced/security/http-basic.md",
    ]),
    "serialization_updates": ("validation", ["tutorial/encoder.md", "tutorial/body-updates.md"]),
    "application_modularity": ("validation", ["tutorial/bigger-applications.md"]),
    "testing": ("test", ["tutorial/testing.md", "advanced/async-tests.md", "advanced/testing-dependencies.md"]),
    "websockets": ("test", ["advanced/websockets.md"]),
    "settings": ("test", ["advanced/settings.md"]),
    "openapi": ("test", ["tutorial/metadata.md", "advanced/extending-openapi.md"]),
    "request_object": ("test", ["advanced/using-request-directly.md"]),
    "debugging": ("test", ["tutorial/debugging.md"]),
}


def group_for(source):
    page = "/".join(source.split("/")[2:])
    for group, (split, pages) in GROUPS.items():
        if page in pages:
            return group, split
    # Old static/proxy/events holdouts never participate in training or mining.
    return "historical_" + page.replace("/", "_"), "diagnostic_only"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    baseline = Path("evaluation/support_corpus_large")
    original = read_json(baseline / "manifest.json")
    docs = []
    for item in original["documents"]:
        source = item["source"]
        if sha(baseline / source) != item["sha256"]:
            raise ValueError(source)
        dst = args.output / source
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(baseline / source, dst)
        group, split = group_for(source)
        docs.append({**item, "group": group, "split": split, "acquisition": "frozen_byte_copy"})
    existing = {d["source"] for d in docs}
    pending = [("0.115.0", page) for _, pages in GROUPS.values() for page in pages
               if f"fastapi/0.115.0/{page}" not in existing]
    upstream.TRANSPORT = "curl"
    failures = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(upstream.get_document, pending):
            if result["status"] != "ok":
                failures.append(result)
                print("FAILED", result["page"], flush=True)
                continue
            source = result["source"]
            dst = args.output / source
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(result["content"])
            group, split = group_for(source)
            docs.append({**result["metadata"], "group": group, "split": split})
            print("SAVED", source, flush=True)
    shutil.copyfile(baseline / "LICENSE.fastapi.txt", args.output / "LICENSE.fastapi.txt")
    write_json(args.output / "manifest.json", {
        "version": "retrieval-ft-corpus-v1", "created_at": now(),
        "source": "fastapi/fastapi", "license": "MIT", "label_license": "not yet published",
        "baseline_manifest_sha256": sha(baseline / "manifest.json"),
        "license_sha256": sha(args.output / "LICENSE.fastapi.txt"),
        "documents": sorted(docs, key=lambda d: d["source"]),
        "group_assignment": GROUPS, "failed_documents": failures,
        "network_failures": upstream.NETWORK_FAILURES,
        "transformations": "byte-copy existing snapshots; expand code includes from fixed commit for new pages",
    })
    print("TOTAL", len(docs), "FAILED", len(failures), flush=True)


if __name__ == "__main__":
    main()
