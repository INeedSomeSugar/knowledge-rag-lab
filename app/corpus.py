"""语料读取入口：把磁盘目录变成 ``(source, text, metadata)`` 三元组流。

这是整条链路唯一的“数据准入”环节：评测脚本、MCP 启动导入、服务启动时的
``--bootstrap`` 都走这里，因此路径越界、重复来源、哈希被改写和空文档的判定
标准只有一份。上层（分块、索引、检索）不再做来源校验，也不应绕过本模块
直接读文件，否则同一份语料在评测和线上会解释成不同内容。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterator

from app.loaders import SUPPORTED_SUFFIXES, load_bytes


def read_documents(directory: Path) -> Iterator[tuple[str, str, dict[str, object]]]:
    """按清单读取语料，返回逐文档的文本与来源元数据。

    有 ``manifest.json`` 时只认清单里列出的文件，并逐份校验 SHA-256；没有清单时
    退化为扫描目录，按扩展名收候选。后者服务于临时实验目录，不享受哈希保护，
    所以正式语料一律带清单。

    返回的是生成器：语料可达数千分块，流式产出可以避免把所有解码后的正文同时
    留在内存里。任一文档不合法即抛 ``ValueError``，不做“跳过坏文件继续跑”的
    静默降级——缺一份文档会让证据区间和评测结果整体失真。
    """
    # 统一解析为绝对路径，后面判断“是否越界”时两侧才在同一种路径形态下比较。
    directory = directory.resolve()
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        # 清单模式：条目的 source 是相对语料根目录的 POSIX 路径，可跨平台复现。
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = manifest["documents"]
    else:
        # 无清单模式：排序保证同一目录多次运行产生相同顺序，便于复现与对比缓存。
        # 只收受支持的扩展名，manifest.json、LICENSE 之类不会误入语料。
        entries = [
            {"source": path.relative_to(directory).as_posix()}
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        ]
    # seen 用于拒绝同一 source 出现两次：重复条目会生成两份相同分块，
    # 索引里出现重复 ID，检索指标随之被重复候选污染。
    seen: set[str] = set()
    for entry in entries:
        source = entry["source"]
        # 先 resolve 再比较：清单或目录里若放了 "..\\..\\etc\\passwd" 或指向外部的
        # 符号链接，这一步会把真实位置暴露出来，越界即拒绝。
        path = (directory / source).resolve()
        if not path.is_relative_to(directory) or source in seen:
            raise ValueError(f"语料路径越界或重复：{source}")
        seen.add(source)
        # 只读一次字节，哈希与解码共用同一份内容：分两次读会在文件被并发修改时
        # 出现“校验的是 A、入库的是 B”的裂缝。
        data = path.read_bytes()
        # 无清单模式没有 sha256 字段，此时跳过校验；有清单就必须与记录一致。
        if entry.get("sha256") and hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"语料哈希不匹配：{source}")
        # 解析按文件名后缀分派（PDF 走 pypdf，文本走编码回退），见 app/loaders.py。
        text = load_bytes(path.name, data).strip()
        # 空文档不能进入分块：它会产生零个分块，却让清单计数与索引计数对不上，
        # 后续按 source 取证据时会表现为“文档存在但找不到”，很难定位。
        if not text:
            raise ValueError(f"文档解析为空：{source}")
        # 只透传白名单字段。清单里还可能有 included_files、upstream_path 等仅供
        # 构建期使用的键，全部塞进分块元数据会放大索引、也让检索侧难以判断字段来源。
        # 缺字段就跳过，保证旧清单（没有 source_revision 等）仍能读取。
        metadata = {key: entry[key] for key in (
            "product", "version", "language", "source_revision", "upstream_url", "sha256"
        ) if key in entry}
        yield source, text, metadata
