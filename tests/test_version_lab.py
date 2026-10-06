from __future__ import annotations

import asyncio
import hashlib
import json

from fastapi.testclient import TestClient
import pytest

import app.main as main_module
import app.version_comparison as comparison_module
from app.version_comparison import VersionEvidenceCatalog
from scripts import fetch_version_corpus, run_version_lab
from scripts.version_cases import run_case


def corpus(tmp_path):
    texts = {
        "0.117.1": "# Guide\n\n## Cleanup\nBefore response.\n\n```python\n# fake\n```\n\n## Other\nStable.\n",
        "0.118.0": "# Guide\n\n## Cleanup\nAfter response.\nNew evidence.\n\n```python\n# fake\n```\n\n## Other\nStable.\n",
    }
    entries = []
    for version, text in texts.items():
        source = f"fastapi/{version}/guide.md"
        path = tmp_path / source
        path.parent.mkdir(parents=True)
        path.write_bytes(text.encode())
        entries.append({
            "source": source, "version": version, "product": "fastapi",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source_revision": "a" * 40,
            "upstream_url": "https://github.com/fastapi/fastapi/blob/" + "a" * 40,
        })
    (tmp_path / "manifest.json").write_text(json.dumps({"documents": entries}), encoding="utf-8")
    return texts


def test_exact_changed_evidence_roundtrips_to_both_versions(tmp_path):
    corpus(tmp_path)
    catalog = VersionEvidenceCatalog(tmp_path)
    result = catalog.compare("guide.md", "0.117.1", "0.118.0", "Cleanup")
    assert result["changed"]
    for change in result["changes"]:
        for key in ("before", "after"):
            anchor = change[key]
            source = catalog.documents[(anchor["version"], "guide.md")][1]
            assert source[anchor["start_char"]:anchor["end_char"]] == anchor["quote"]
            assert "Stable." not in anchor["quote"]
    unchanged = catalog.compare("guide.md", "0.117.1", "0.118.0", "Other")
    assert unchanged["changes"] == []
    assert unchanged["verification"] == "official_text_only"


@pytest.mark.parametrize("topic,before,after,section", [
    ("../guide.md", "0.117.1", "0.118.0", None),
    ("guide.md", "0.117.1", "0.117.1", None),
    ("guide.md", "0.117.1", "9.9", None),
    ("guide.md", "0.117.1", "0.118.0", "fake"),
])
def test_comparison_rejects_unknown_scope_and_code_comments(tmp_path, topic, before, after, section):
    corpus(tmp_path)
    with pytest.raises(ValueError):
        VersionEvidenceCatalog(tmp_path).compare(topic, before, after, section)


def test_tampered_snapshot_cannot_be_compared(tmp_path):
    corpus(tmp_path)
    (tmp_path / "fastapi/0.118.0/guide.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="哈希"):
        VersionEvidenceCatalog(tmp_path)


def test_behavior_record_is_explicitly_absent_or_stale_when_inputs_change(tmp_path):
    assert comparison_module.behavior_record(tmp_path)["status"] == "not_executed"
    case = tmp_path / "scripts/version_cases.py"
    manifest = tmp_path / "evaluation/version_corpus/manifest.json"
    report = tmp_path / "evaluation/reports/version-behavior-v09.json"
    for path in (case, manifest, report):
        path.parent.mkdir(parents=True, exist_ok=True)
    case.write_bytes(b"fixed_case")
    manifest.write_bytes(b"fixed_manifest")
    report.write_text(json.dumps({
        "case_script_sha256": hashlib.sha256(case.read_bytes()).hexdigest(),
        "corpus_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    assert comparison_module.behavior_record(tmp_path)["status"] == "recorded"
    case.write_bytes(b"changed_case")
    assert comparison_module.behavior_record(tmp_path)["status"] == "stale"


def test_api_is_read_only_comparison_and_missing_corpus_is_explicit(tmp_path, monkeypatch):
    corpus(tmp_path)
    monkeypatch.setattr(main_module, "load_version_catalog", lambda: VersionEvidenceCatalog(tmp_path))
    client = TestClient(main_module.app)
    assert client.get("/api/v1/versions/catalog").json()["versions"] == ["0.117.1", "0.118.0"]
    response = client.post("/api/v1/versions/compare", json={"topic": "guide.md"})
    assert response.status_code == 200
    assert response.json()["verification"] == "official_text_only"
    assert client.post("/api/v1/versions/compare", json={"topic": "../../.env"}).status_code == 400
    assert "版本故障" in client.get("/versions").text
    monkeypatch.undo()
    monkeypatch.setattr(comparison_module, "DEFAULT_CORPUS", tmp_path / "missing")
    assert client.get("/api/v1/versions/catalog").status_code == 503
    assert client.post("/api/v1/versions/compare", json={}).status_code == 503


def test_code_directives_keep_same_revision_and_inclusive_lines(monkeypatch):
    calls = []

    def fake_fetch(revision, path):
        calls.append((revision, path))
        if path.startswith("docs/en/"):
            return b"# Guide\n{* ../../docs_src/example.py ln[2:3] hl[2] *}\n"
        return b"first\nsecond\nthird\nfourth\n"
    monkeypatch.setattr(fetch_version_corpus, "fetch", fake_fetch)
    _, content, meta = fetch_version_corpus.get_document(("0.118.0", "guide.md"))
    assert b"```python\nsecond\nthird\n```" in content
    assert len({revision for revision, _ in calls}) == 1
    assert meta["included_files"]["docs_src/example.py"] == hashlib.sha256(
        b"first\nsecond\nthird\nfourth\n"
    ).hexdigest()


def test_owned_resource_is_released_after_real_asgi_case():
    for case_id, owner in (("stream_owned", "stream"), ("background_owned", "task")):
        case = asyncio.run(run_case(case_id))
        assert case["execution_status"] == "completed"
        assert case["events"].index(f"{owner}:read:open") < case["events"].index(f"{owner}:close")
        assert case["response_completed"]
    with pytest.raises(ValueError):
        asyncio.run(run_case("arbitrary_user_code"))


def test_runtime_error_is_not_mislabeled_as_expected_resource_failure():
    result = {
        "fastapi": "0.117.1",
        "cases": [{"case_id": case_id, "execution_status": "failed",
                   "error": {"type": "TimeoutError", "message": ""}}
                  for case_id in ("stream_borrowed", "background_borrowed",
                                  "stream_owned", "background_owned")],
    }
    assert not any(c["observation_matches_expectation"]
                   for c in run_version_lab.validate_observation(result, "0.117.1"))
    with pytest.raises(ValueError, match="wrong FastAPI"):
        run_version_lab.validate_observation(result, "0.118.0")


def test_cli_refuses_to_overwrite_raw_report_before_preparing_environments(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    output.write_bytes(b"historical")
    monkeypatch.setattr("sys.argv", ["run_version_lab", "--prepare-environments",
                                    "--output", str(output)])
    with pytest.raises(SystemExit):
        run_version_lab.main()
    assert output.read_bytes() == b"historical"
