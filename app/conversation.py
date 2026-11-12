"""A bounded one-turn conversational rewrite without external model calls.

为什么不用 LLM 改写：改写质量难以离线复现，且会把“检索问题”与“模型回答”耦合在
一起，评测时无法归因。这里只用规则处理三类高频情形：

1. 只回一个版本号（“0.115.0”）→ 继承上一轮问题；
2. 以“那/这个/它/还有/what about”开头的追问 → 拼接上一轮问题；
3. 本轮显式提到产品或版本 → 覆盖上一轮的作用域。

规则改写覆盖不了省略主语的长追问，这是已知限制；但它零延迟、可解释、可单测。
"""

import re


def rewrite_turn(question: str, filters: dict, previous: dict | None, catalog: dict) -> dict:
    """返回 ``{query, scope, trace}``，其中 ``query`` 才是真正送检索的文本。

    ``trace`` 保留原问题与改写结果，服务层会把它写入响应，便于用户核对
    “系统到底拿什么去检索了”。作用域继承只接受调用方显式传入的字段，
    不接受从模型回答文本里推断出的产品或版本。
    """
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
            # 版本必须与上一轮的产品同源；跨产品继承版本会指向不存在的组合。
            if key == "version" and scope.get("product") != previous.get("product"):
                continue
            scope[key] = value
            trace["inherited_fields"].append(key)
    products = [p for p in catalog if re.search(re.escape(p), original, flags=re.I)]
    if len(products) == 1 and "product" not in filters:
        if products[0] != scope.get("product") and "version" not in filters:
            # 产品变了，上一轮的版本不再适用，先清空再由版本门控决定是否追问。
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
        # 保留原问题再加一行“本轮追问”，避免规则拼接产生语义倒置。
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
