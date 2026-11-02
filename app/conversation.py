"""A bounded one-turn conversational rewrite without external model calls."""

import re


def rewrite_turn(question: str, filters: dict, previous: dict | None, catalog: dict) -> dict:
    scope = dict(filters)
    original = question.strip()
    trace = {
        "provider": "one_turn_rules",
        "original": original,
        "rewritten": original,
        "used_previous": False,
        "inherited_fields": [],
    }
    if not previous:
        return {"query": original, "scope": scope, "trace": trace}
    # Only user fields are accepted. Assistant prose never establishes a product/version.
    for key in ("product", "version"):
        value = previous.get(key)
        if value and key not in scope:
            if key == "version" and scope.get("product") != previous.get("product"):
                continue
            scope[key] = value
            trace["inherited_fields"].append(key)
    products = [p for p in catalog if re.search(re.escape(p), original, flags=re.I)]
    if len(products) == 1 and "product" not in filters:
        if products[0] != scope.get("product") and "version" not in filters:
            scope.pop("version", None)
        scope["product"] = products[0]
    versions = re.findall(r"(?<![\w.])\d+\.\d+(?:\.\d+)?(?![\w.])", original)
    if versions and "version" not in filters:
        # Unknown or multiple versions must reach the normal clarification/refusal gate.
        scope["version"] = versions[0] if len(set(versions)) == 1 else "ambiguous_versions"
    version_only = bool(
        re.fullmatch(
            r"(?:我(?:用|使用)的?(?:是)?|版本(?:是)?|用|使用|v|version\s*)?\s*"
            r"\d+\.\d+(?:\.\d+)?[。.!！\s]*",
            original,
            re.I,
        )
    )
    followup = bool(
        re.search(
            r"^(那|那么|这个|这种|它|再|继续|还有|what about\b|and\b|how about\b)",
            original,
            re.I,
        )
    )
    prior = previous["question"].strip()
    if version_only:
        query = prior
    elif followup:
        query = f"{prior}\n本轮追问：{original}"
    else:
        query = original
    trace.update(
        rewritten=query,
        used_previous=version_only or followup,
        scope=scope,
        version_only=version_only,
    )
    return {"query": query, "scope": scope, "trace": trace}
