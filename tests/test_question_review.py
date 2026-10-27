import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from app.evaluation import load_evaluation_cases
from app.question_review import (
    CHECKS,
    QuestionReviewStore,
    ReviewConflict,
    ReviewEdit,
    create_review_app,
)


TEXT = "源由协议、域名和端口组成。不同端口属于不同源。"


@pytest.fixture
def review_data(tmp_path):
    documents = tmp_path / "documents"
    documents.mkdir()
    for version in ("v1", "v2"):
        (documents / f"{version}.md").write_text(TEXT, encoding="utf-8")
    manifest = {
        "documents": [
            {"source": f"{version}.md", "product": "fastapi", "version": version}
            for version in ("v1", "v2")
        ]
    }
    (documents / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    base = {
        "id": "dev",
        "group_id": "cors",
        "category": "cors",
        "split": "development",
        "question": "源由什么组成？",
        "reference_answer": "协议、域名和端口。",
        "filters": {"product": "fastapi", "version": "v1"},
        "should_answer": True,
        "expected_status": "answered",
        "relevant_sources": ["v1.md"],
        "evidence": [{"source": "v1.md", "quote": TEXT, "start_char": 0, "end_char": len(TEXT)}],
        "review_status": "unreviewed",
    }
    heldout = {**deepcopy(base), "id": "heldout", "group_id": "heldout", "split": "test"}
    questions = tmp_path / "questions.jsonl"
    questions.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in (base, heldout)), encoding="utf-8"
    )
    return questions, documents, tmp_path / "state"


def store(data):
    return QuestionReviewStore(*data, "development")


def edit(**changes):
    value = {
        "revision": 0,
        "question": "源由什么组成？",
        "reference_answer": "协议、域名和端口。",
        "expected_status": "answered",
        "evidence": [{"source": "v1.md", "quote": TEXT}],
        "review_status": "unreviewed",
    }
    value.update(changes)
    return ReviewEdit(**value)


def approved(**changes):
    return edit(
        review_status="human_verified",
        reviewed_by="fixture reviewer",
        checks=dict.fromkeys(CHECKS, True),
        **changes,
    )


def test_default_session_hides_heldout_and_other_versions(review_data):
    workspace = store(review_data)
    snapshot = workspace.snapshot()
    assert snapshot["total"] == 1 and snapshot["human_verified"] == snapshot["model_calls"] == 0
    assert snapshot["binding"]["case_ids"] == ["dev"]
    assert workspace.sources("dev") == ["v1.md"]
    with pytest.raises(ValueError):
        workspace.document("dev", "v2.md")
    with pytest.raises(ValueError):
        workspace.document("heldout", "v1.md")


def test_draft_recomputes_unicode_span_and_exports_without_touching_original(review_data, tmp_path):
    original = review_data[0].read_bytes()
    workspace = store(review_data)
    quote = "不同端口属于不同源。"
    item = workspace.save("dev", edit(evidence=[{"source": "v1.md", "quote": quote}]))
    assert item["revision"] == 1
    assert item["row"]["evidence"][0]["start_char"] == TEXT.index(quote)
    assert item["row"]["review_status"] == "unreviewed"
    exported = tmp_path / "export.jsonl"
    exported.write_text(workspace.export(), encoding="utf-8")
    cases = load_evaluation_cases(exported)
    assert len(cases) == 1 and cases[0].split == "development"
    assert review_data[0].read_bytes() == original


@pytest.mark.parametrize(
    "changes",
    [
        {"reviewed_by": ""},
        {"checks": {}},
        {
            "checks": {
                "question_and_scope": True,
                "answer_and_behavior": True,
                "evidence_or_absence": False,
            }
        },
    ],
)
def test_cannot_mark_reviewed_without_identity_and_all_checks(review_data, changes):
    values = {
        "review_status": "human_verified",
        "reviewed_by": "fixture reviewer",
        "checks": dict.fromkeys(CHECKS, True),
        **changes,
    }
    workspace = store(review_data)
    with pytest.raises(ValueError):
        workspace.save("dev", edit(**values))
    assert not workspace.path.exists()


def test_real_human_action_persists_with_timezone_and_can_be_reopened(review_data):
    workspace = store(review_data)
    saved = workspace.save("dev", approved())
    assert saved["row"]["reviewed_at"].endswith("+00:00")
    reopened = store(review_data)
    assert reopened.snapshot()["human_verified"] == 1
    reopened.save("dev", edit(revision=1))
    assert store(review_data).snapshot()["human_verified"] == 0


@pytest.mark.parametrize(
    "evidence",
    [
        [{"source": "v1.md", "quote": "伪造的原文"}],
        [{"source": "v2.md", "quote": TEXT}],
        [{"source": "../outside.md", "quote": TEXT}],
    ],
)
def test_bad_quote_wrong_version_and_path_are_rejected(review_data, evidence):
    workspace = store(review_data)
    with pytest.raises(ValueError):
        workspace.save("dev", edit(evidence=evidence))
    assert workspace.snapshot()["human_verified"] == 0


def test_refusal_requires_reason_and_no_answer_evidence(review_data):
    workspace = store(review_data)
    changes = {"expected_status": "insufficient_evidence", "evidence": []}
    with pytest.raises(ValueError, match="说明判定依据"):
        workspace.save("dev", approved(**changes))
    item = workspace.save("dev", approved(**changes, notes="此题所需事实未收录于当前语料。"))
    assert not item["row"]["should_answer"] and item["row"]["relevant_sources"] == []


def test_old_page_and_competing_process_cannot_silently_overwrite(review_data):
    first, second = store(review_data), store(review_data)
    first.save("dev", edit())
    with pytest.raises(ReviewConflict):
        first.save("dev", edit())
    with pytest.raises(ReviewConflict, match="另一工作台"):
        second.save("dev", approved())


def test_changed_input_invalidates_existing_session_and_saved_state(review_data):
    workspace = store(review_data)
    workspace.save("dev", edit())
    path = review_data[1] / "v1.md"
    path.write_text(TEXT + "变更", encoding="utf-8")
    with pytest.raises(ReviewConflict, match="语料"):
        workspace.export()
    with pytest.raises(ReviewConflict):
        store(review_data)


def test_changed_questions_block_save(review_data):
    workspace = store(review_data)
    review_data[0].write_text(review_data[0].read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ReviewConflict, match="原始问题"):
        workspace.save("dev", edit())


def test_disk_failure_preserves_previous_review(review_data, monkeypatch):
    workspace = store(review_data)
    workspace.save("dev", edit())
    before = workspace.path.read_bytes()

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr("app.question_review.os.replace", fail)
    with pytest.raises(OSError):
        workspace.save("dev", approved(revision=1))
    assert workspace.path.read_bytes() == before
    assert workspace.snapshot()["human_verified"] == 0


def test_api_export_and_conflict_and_cross_origin_protection(review_data):
    workspace = store(review_data)

    async def exercise():
        transport = httpx.ASGITransport(app=create_review_app(workspace))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            assert (await client.get("/")).status_code == 200
            assert (await client.get("/api/session")).json()["total"] == 1
            payload = edit().model_dump()
            assert (
                await client.put(
                    "/api/cases/dev", json=payload, headers={"Origin": "https://outside.invalid"}
                )
            ).status_code == 403
            assert (await client.put("/api/cases/dev", content="{}")).status_code == 415
            assert (await client.put("/api/cases/dev", json=payload)).status_code == 200
            assert (await client.put("/api/cases/dev", json=payload)).status_code == 409
            exported = await client.get("/api/export")
            assert "heldout" not in exported.text
            assert json.loads(exported.text)["review_status"] == "unreviewed"
            assert exported.headers["cache-control"] == "no-store"

    asyncio.run(exercise())
