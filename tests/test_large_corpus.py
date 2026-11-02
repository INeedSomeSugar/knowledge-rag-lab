"""Protect frozen bytes and verify page-local acquisition failures without network."""

import json
from urllib.error import HTTPError
from urllib.error import URLError

import pytest

from scripts import fetch_large_corpus as corpus


def baseline_fixture(tmp_path):
    root = tmp_path / "frozen"
    documents = []
    for version in ("0.110.0", "0.115.0"):
        for page in corpus.PAGES[:17]:
            source = f"fastapi/{version}/{page}"
            content = f"{source}\r\n保留字节\r\n".encode()
            path = root / source
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            documents.append(
                {
                    "source": source,
                    "version": version,
                    "sha256": corpus.digest(content),
                    "source_revision": corpus.REVISIONS[version],
                }
            )
    (root / "manifest.json").write_text(json.dumps({"documents": documents}))
    return root, documents


def test_old_sources_never_fetched_and_output_is_additive(tmp_path, monkeypatch):
    frozen, documents = baseline_fixture(tmp_path)
    requested = []

    def fake_document(item):
        version, page = item
        source = f"fastapi/{version}/{page}"
        requested.append(source)
        return {"status": "skipped", "version": version, "page": page, "reason": "404 fixture"}

    monkeypatch.setattr(corpus, "fetch", lambda *args: b"MIT fixture")
    monkeypatch.setattr(corpus, "get_document", fake_document)
    output = tmp_path / "large"
    manifest = corpus.build_corpus(output, frozen)
    assert len(corpus.PAGES) == len(set(corpus.PAGES)) == 30
    assert len(requested) == len(manifest["skipped"]) == 86
    assert not set(requested) & {d["source"] for d in documents}
    assert manifest["baseline"]["reused_document_count"] == 34
    for d in documents:
        assert (output / d["source"]).read_bytes() == (frozen / d["source"]).read_bytes()
    with pytest.raises(ValueError, match="must not exist"):
        corpus.build_corpus(output, frozen)


def test_corrupted_baseline_stops_before_network(tmp_path, monkeypatch):
    frozen, documents = baseline_fixture(tmp_path)
    (frozen / documents[0]["source"]).write_bytes(b"corruption")
    monkeypatch.setattr(corpus, "fetch", lambda *args: pytest.fail("must not fetch"))
    with pytest.raises(ValueError, match="hash mismatch"):
        corpus.build_corpus(tmp_path / "large", frozen)
    assert not (tmp_path / "large").exists()


@pytest.mark.parametrize("failure", ["page404", "include404", "unsupported", "timeout"])
def test_page_failures_are_recorded_and_next_page_succeeds(monkeypatch, failure):
    def fake_fetch(revision, path):
        if path.endswith("good.md"):
            return b"valid page"
        if failure == "timeout":
            raise TimeoutError("timed out")
        if failure == "unsupported":
            return b"{!missing/unknown.py!}"
        if failure == "include404" and path.startswith("docs/"):
            return b"{!../../docs_src/missing.py!}"
        raise HTTPError("https://raw.githubusercontent.com/fixture", 404, "Not Found", {}, None)

    monkeypatch.setattr(corpus, "fetch", fake_fetch)
    failed = corpus.get_document(("0.118.0", "bad.md"))
    assert failed["status"] == "skipped" and failed["reason"]
    assert corpus.get_document(("0.118.0", "good.md"))["status"] == "ok"


def test_transient_fetch_retries_but_404_does_not(monkeypatch):
    from io import BytesIO

    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise URLError("transient TLS fixture")
        return BytesIO(b"fresh response")

    monkeypatch.setattr(corpus, "urlopen", flaky)
    monkeypatch.setattr(corpus, "sleep", lambda _: None)
    assert corpus.fetch(corpus.REVISIONS["0.118.0"], "LICENSE") == b"fresh response"
    assert len(calls) == 3
    calls.clear()

    def missing(*args, **kwargs):
        calls.append(1)
        raise HTTPError("https://raw.githubusercontent.com/fixture", 404, "Not Found", {}, None)

    monkeypatch.setattr(corpus, "urlopen", missing)
    with pytest.raises(HTTPError):
        corpus.fetch(corpus.REVISIONS["0.118.0"], "missing.md")
    assert len(calls) == 1


def test_network_unavailable_does_not_publish_a_corpus(tmp_path, monkeypatch):
    frozen, _ = baseline_fixture(tmp_path)

    def offline(*args):
        raise URLError("network unavailable")

    monkeypatch.setattr(corpus, "fetch", offline)
    with pytest.raises(URLError):
        corpus.build_corpus(tmp_path / "large", frozen)
    assert not (tmp_path / "large").exists()


def test_both_include_syntaxes_expand_and_line_selection_is_honored(monkeypatch):
    def fake_fetch(revision, path):
        if path.startswith("docs/"):
            return b"{!../../docs_src/demo.py!}\n{* ../../docs_src/demo.py ln[2:3] hl[2] *}"
        return b"line1\nline2\nline3\nline4\n"

    monkeypatch.setattr(corpus, "fetch", fake_fetch)
    result = corpus.get_document(("0.118.0", "example.md"))
    assert result["status"] == "ok"
    assert result["content"] == b"line1\nline2\nline3\nline4\n```python\nline2\nline3\n```"
    assert result["metadata"]["included_files"] == {
        "docs_src/demo.py": corpus.digest(b"line1\nline2\nline3\nline4\n")
    }


def test_curl_uses_system_proxy_and_preserves_exact_bytes(monkeypatch):
    import subprocess

    commands = []
    monkeypatch.setattr(corpus.shutil, "which", lambda _: "curl.exe")
    monkeypatch.setattr(corpus, "getproxies", lambda: {"https": "http://127.0.0.1:7890"})

    def response(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, b"text\r\n\n200", b"")

    monkeypatch.setattr(corpus.subprocess, "run", response)
    assert corpus.curl_fetch("https://raw.githubusercontent.com/fixed") == b"text\r\n"
    assert commands[0][commands[0].index("--proxy") + 1] == "http://127.0.0.1:7890"
    monkeypatch.setattr(corpus, "getproxies", lambda: {"https": "http://user:secret@proxy"})
    with pytest.raises(ValueError, match="Authenticated"):
        corpus.curl_fetch("https://raw.githubusercontent.com/fixed")
    assert len(commands) == 1
