from __future__ import annotations

from dataclasses import dataclass, field
import json
import re

from app.domain import SearchHit

REFUSAL = "根据当前知识库无法确定。请补充错误信息或相关官方文档。"


@dataclass
class GeneratedAnswer:
    status: str
    answer: str
    citation_ids: list[int] = field(default_factory=list)
    claims: list[dict[str, object]] = field(default_factory=list)
    verification: str = "not_applicable"
    reason: str = ""


def normalize_quote(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_grounded_answer(raw: str, hits: list[SearchHit]) -> GeneratedAnswer:
    """Validate structure and verbatim quotes, without pretending this proves entailment."""
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("expected object")
        if value.get("status") == "insufficient_evidence":
            return GeneratedAnswer(
                "insufficient_evidence", REFUSAL, reason="model_found_insufficient_evidence"
            )
        claims = value.get("claims")
        if (
            value.get("status") != "answered"
            or not isinstance(claims, list)
            or not claims
            or len(claims) > 12
        ):
            raise ValueError("invalid claims")
        used: set[int] = set()
        lines = []
        for claim in claims:
            if (
                not isinstance(claim, dict)
                or not isinstance(claim.get("text"), str)
                or not claim["text"].strip()
            ):
                raise ValueError("invalid claim")
            evidence = claim.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError("missing evidence")
            ids = set()
            for anchor in evidence:
                if not isinstance(anchor, dict):
                    raise ValueError("invalid evidence")
                number, quote = anchor.get("citation"), anchor.get("quote")
                if type(number) is not int or not 1 <= number <= len(hits):
                    raise ValueError("invalid citation")
                if not isinstance(quote, str) or len(normalize_quote(quote)) < 6:
                    raise ValueError("empty or trivial quote")
                if normalize_quote(quote) not in normalize_quote(hits[number - 1].chunk.text):
                    raise ValueError("quote not in evidence")
                ids.add(number)
                used.add(number)
            # Reference numbers come from validated evidence, never free-form model text.
            claim_text = re.sub(r"\[\d+\]", "", claim["text"]).strip()
            lines.append(claim_text + " " + "".join(f"[{number}]" for number in sorted(ids)))
        return GeneratedAnswer(
            "answered", "\n\n".join(lines), sorted(used), claims, "quotes_verified"
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return GeneratedAnswer(
            "insufficient_evidence", REFUSAL, reason="citation_validation_failed"
        )
