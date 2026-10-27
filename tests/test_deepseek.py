from types import SimpleNamespace
import json

import pytest

from app.deepseek import DeepSeekGenerator
from app.generation import Completion
from tests.test_verification_evaluation import grounded, hits, model


class FakeClient:
    def __init__(self, *, models=("deepseek-flash",), **kwargs):
        self.models = SimpleNamespace(
            list=lambda: SimpleNamespace(data=[SimpleNamespace(id=name) for name in models])
        )
        self.closed = False

    def close(self):
        self.closed = True


def test_catalog_selects_actual_model_and_uses_deepseek_thinking_contract(monkeypatch):
    monkeypatch.setattr("openai.OpenAI", FakeClient)
    generator = DeepSeekGenerator(api_key="fixture-only")
    assert generator.model == "deepseek-flash"
    assert generator._request_options() == {
        "extra_body": {"thinking": {"type": "disabled"}},
        "response_format": {"type": "json_object"},
    }


def test_unknown_explicit_model_fails_without_silent_substitution(monkeypatch):
    monkeypatch.setattr("openai.OpenAI", FakeClient)
    with pytest.raises(ValueError, match="实际模型目录"):
        DeepSeekGenerator(api_key="fixture-only", model="missing")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.deepseek.com.evil.invalid",
        "http://api.deepseek.com",
        "https://api.deepseek.com/?key=fixture",
        "https://api.deepseek.com/other",
        "https://user:pass@api.deepseek.com",
        "https://api.deepseek.com:444",
    ],
)
def test_provider_key_cannot_be_redirected_to_another_endpoint(base_url):
    with pytest.raises(ValueError, match="官方 HTTPS"):
        DeepSeekGenerator(api_key="fixture-only", base_url=base_url)


def test_truncated_response_is_error_with_preserved_usage(monkeypatch):
    generator = model(verify_support=False)
    monkeypatch.setattr(
        generator,
        "_complete",
        lambda *_: Completion(grounded(), 10, 5, 15, "served-model", "length"),
    )
    answer = generator.generate("源是什么", hits())
    assert answer.status == "error"
    call = answer.model_trace["calls"][0]
    assert call["status"] == "error" and call["finish_reason"] == "length"
    assert call["total_tokens"] == 15 and call["response_model"] == "served-model"


@pytest.mark.parametrize(
    "split,ids",
    [
        ("test", ["heldout"]),
        ("development", ["heldout"]),
        ("development", ["dev", "dev"]),
    ],
)
def test_development_subset_guard_runs_before_any_model_access(tmp_path, monkeypatch, split, ids):
    from scripts import evaluate

    documents = tmp_path / "documents"
    documents.mkdir()
    (documents / "guide.md").write_text("配置参数的说明。", encoding="utf-8")
    questions = tmp_path / "questions.jsonl"
    rows = [
        {
            "id": name,
            "split": part,
            "group_id": name,
            "question": "配置参数是什么？",
            "relevant_sources": ["guide.md"],
            "should_answer": True,
        }
        for name, part in (("dev", "development"), ("heldout", "test"))
    ]
    questions.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    def forbidden(*args):
        raise AssertionError("guard must precede model construction")

    monkeypatch.setattr(evaluate, "build_searchers", forbidden)
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate",
            "--demo",
            "--documents",
            str(documents),
            "--questions",
            str(questions),
            "--split",
            split,
            "--case-ids",
            *ids,
        ],
    )
    with pytest.raises(ValueError, match="case-ids"):
        evaluate.main()
