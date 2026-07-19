from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from tests.test_service import build_service


def test_text_ingest_search_and_chat_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_module, "service", build_service(tmp_path))
    client = TestClient(main_module.app)

    create_response = client.post(
        "/api/v1/documents/text",
        json={"source": "值班制度.md", "text": "严重故障需要在五分钟内响应。"},
    )
    search_response = client.post(
        "/api/v1/retrieval/search",
        json={"query": "严重故障多久响应", "top_k": 3},
    )
    chat_response = client.post(
        "/api/v1/chat",
        json={"question": "严重故障多久响应", "top_k": 3},
    )

    assert create_response.status_code == 201
    assert search_response.status_code == 200
    assert search_response.json()["hits"][0]["source"] == "值班制度.md"
    assert chat_response.status_code == 200
    assert chat_response.json()["citations"][0]["source"] == "值班制度.md"


def test_upload_rejects_unsupported_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_module, "service", build_service(tmp_path))
    client = TestClient(main_module.app)

    response = client.post(
        "/api/v1/documents/upload",
        files={"file": ("image.png", b"not-an-image", "image/png")},
    )

    assert response.status_code == 400
    assert "只支持" in response.json()["detail"]
