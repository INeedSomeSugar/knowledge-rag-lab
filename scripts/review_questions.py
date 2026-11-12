"""Serve or inspect the model-free local question review workspace.

三个子命令：

- ``serve``：仅监听 ``127.0.0.1`` 的本地工作台，逐题确认题面、参考答案与证据；
- ``status``：打印绑定信息与已确认数量，不加载页面；
- ``export``：导出当前划分的 JSONL，必须指定全新路径，且不得覆盖原始问题或语料。

整个过程不调用模型；``unreviewed`` 条目导出后仍是 ``unreviewed``，不会被“顺手”标成人工核验。
"""

import argparse
from pathlib import Path
import json

from app.question_review import QuestionReviewStore, create_review_app


def main():
    """解析子命令并复用同一个 ``QuestionReviewStore``，保证三种操作看到同一份状态。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "status", "export"])
    parser.add_argument(
        "--questions", type=Path, default=Path("evaluation/questions.support.candidate.jsonl")
    )
    parser.add_argument("--documents", type=Path, default=Path("evaluation/support_corpus"))
    parser.add_argument("--state-dir", type=Path, default=Path("work/question-review"))
    parser.add_argument("--split", choices=["development", "test"], default="development")
    parser.add_argument("--port", type=int, default=8013)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    store = QuestionReviewStore(args.questions, args.documents, args.state_dir, args.split)
    if args.command == "serve":
        import uvicorn

        uvicorn.run(create_review_app(store), host="127.0.0.1", port=args.port, workers=1)
    elif args.command == "status":
        state = store.snapshot()
        print(
            json.dumps(
                {key: value for key, value in state.items() if key != "cases"},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if not args.output:
            parser.error("export 要求 --output，拒绝覆盖原始文件或已有文件")
        data = store.export()
        target = args.output.resolve()
        if target == store.questions or target.is_relative_to(store.documents):
            raise ValueError("不能覆盖原始问题或语料")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            stream.write(data)
        print(f"导出当前 {args.split} 划分到 {target}；未审条目保持 unreviewed。")


if __name__ == "__main__":
    main()
