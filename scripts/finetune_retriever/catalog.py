"""Make a source paragraph inventory for assistant annotation; no retrieval scoring."""
import argparse
from pathlib import Path
import re

from app.chunking import TextChunker
from app.corpus import read_documents
from scripts.finetune_retriever.common import read_json, write_jsonl


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    manifest = read_json(args.root / "corpus/manifest.json")
    metadata = {d["source"]: d for d in manifest["documents"]}
    chunks, paragraphs = [], []
    skip = {"tutorial/body.md", "tutorial/query-params.md", "tutorial/path-params.md",
            "tutorial/response-model.md", "tutorial/dependencies/index.md",
            "tutorial/dependencies/dependencies-with-yield.md"}
    chunker = TextChunker(500, 80, strategy="sections")
    document_index = 0
    for source, text, md in read_documents(args.root / "corpus"):
        doc = metadata[source]
        for chunk in chunker.split(source, source, text):
            chunk.metadata.update(md)
            chunk.metadata.update(group=doc["group"], split=doc["split"])
            chunks.append(chunk.to_dict())
        if (doc["split"] == "diagnostic_only" or doc["version"] != "0.115.0"
                or "/".join(source.split("/")[2:]) in skip):
            continue
        document_index += 1
        fence, buffer, offset, start = False, [], 0, 0
        block_index = 0
        for line in (text + "\n\n").splitlines(keepends=True):
            if re.match(r"^\s*(```|~~~)", line):
                fence = not fence
                buffer = []
                offset += len(line)
                continue
            if fence:
                offset += len(line)
                continue
            if not line.strip():
                value = "".join(buffer).strip()
                if (len(re.findall(r"[\u4e00-\u9fff]", value)) >= 8
                        and 20 <= len(value) <= 600 and not value.startswith("#")):
                    block_index += 1
                    paragraphs.append({
                        "ref": f"D{document_index:02d}P{block_index:02d}",
                        "source": source, "group": doc["group"], "split": doc["split"],
                        "text": value, "start": text.find(value, start),
                    })
                buffer = []
            elif not buffer:
                start, buffer = offset, [line]
            else:
                buffer.append(line)
            offset += len(line)
    write_jsonl(args.root / "chunks.preview.jsonl", chunks)
    write_jsonl(args.root / "paragraphs.jsonl", paragraphs)
    print("chunks", len(chunks), "paragraphs", len(paragraphs))
    print("failures", manifest["failed_documents"])


if __name__ == "__main__":
    main()
