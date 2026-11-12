"""分块：把文档切成可检索、可回指的最小证据单元。

分块策略直接影响三件事，因此这里不是简单的定长切分：

1. **可引用性**：PDF 每页之间插入 ``\\f`` 并禁止跨页重叠，保证一个分块只对应一个页码。
2. **语义完整性**：优先在空行、换行、中文句号等自然边界收尾，减少把一句话切两半。
3. **评测可复现性**：分块边界由文本内容和参数唯一决定，同一输入重复切分结果一致。

位置一律用**字符偏移**而不是 token 数或行号，原因是评测标注、引用核验和前端高亮
都直接拿 ``start_char``/``end_char`` 去原文切片；只要换分词器或换模型，token 位置
就整体漂移，字符偏移不会。分块是 ``app/corpus.py`` 与索引之间的一层：上游给的是
整篇正文，下游（BM25 / 向量 / 重排）只认分块。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.domain import Chunk


class TextChunker:
    """按字符窗口切分，并尽量在中文标点或换行处收尾。

    两种策略：

    - ``window``：纯滑动窗口，仅在自然标点处提前收尾，适合无结构纯文本。
    - ``sections``：先把 Markdown 标题当作硬边界，再在章节内部窗口切分；标题层级与
      章节名写入元数据，供 ``neighbors`` 上下文策略判断“同章节邻块”。

    约束：``chunk_size >= 100``，``0 <= overlap < chunk_size``。上限保护是为了避免
    生成大量无信息碎片，下限保证重叠不会退化为“整块重复”。
    """

    def __init__(
        self, chunk_size: int = 500, overlap: int = 80, *, strategy: str = "window"
    ) -> None:
        # 参数在构造期一次性校验，避免跑到一半才发现配置非法、只产出一半分块。
        if chunk_size < 100:
            raise ValueError("chunk_size 不能小于 100")
        if not 0 <= overlap < chunk_size:
            raise ValueError("overlap 必须小于 chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap
        if strategy not in {"window", "sections"}:
            raise ValueError("strategy 必须为 window 或 sections")
        self.strategy = strategy

    def split(self, document_id: str, source: str, text: str) -> list[Chunk]:
        """把一篇文档切成分块列表，保持原文顺序与字符偏移。

        关键不变量：

        - ``chunk.text`` 等于 ``normalized[start_char:end_char]``（去掉首尾换行后的子串）；
        - 分块之间只在同一章节/同一页内重叠 ``overlap`` 个字符；
        - 空文本返回空列表，由调用方决定是否视为错误。

        实现是一个手写循环而不是正则 ``finditer``：每轮都要根据“换页符 > 章节边界 >
        自然标点”的优先级重新决定终点，且终点又反过来决定下一轮起点（是否重叠），
        这些状态用正则表达不清楚。
        """
        # 先做最小规范化（仅换行符），后续所有偏移都基于 normalized，不是原始 text。
        normalized = self._normalize(text)
        if not normalized:
            return []

        # 文档级元数据（标题）每个分块都会带上，供检索结果展示与按文档聚合。
        document_metadata = self._document_metadata(source, normalized)
        is_pdf = Path(source).suffix.lower() == ".pdf"
        # 只有 sections 策略才预扫描标题；window 策略跳过，省一次全文遍历。
        headings = self._markdown_headings(source, normalized)
        # 章节边界先于窗口生效；PDF 的换页符是更硬的边界，优先于章节。
        # 这里只取偏移量，标题文本留到最后按分块终点回查。
        boundaries = (
            [position for position, _, _ in headings] if self.strategy == "sections" else []
        )
        chunks: list[Chunk] = []
        start = 0
        index = 0
        # 循环不变量：start 始终是“下一块正文起点”，且绝不指向换页符；
        # 每轮结束时 start 必须严格增大，否则会死循环（见循环末尾三个分支）。
        while start < len(normalized):
            # 上一轮可能停在换页符前一位，这里跳过它，保证分块正文不含 \f。
            if normalized[start] == "\f":
                start += 1
                continue
            # 先按窗口给出“最长可切到哪”，再用章节/标点把终点往回收。
            hard_end = min(start + self.chunk_size, len(normalized))
            # 章节边界必须落在 (start, hard_end] 区间：等于 start 的边界已经生效过，
            # 超出窗口的边界留给后续轮次，不能在这里提前截断。
            section_end = next(
                (position for position in boundaries if start < position <= hard_end), None
            )
            if section_end is not None:
                hard_end = section_end
            # 右端传 hard_end + 1 是刻意的：恰好位于窗口末尾的换页符也要被识别，
            # 否则这一轮会把它当普通字符吞进正文，页码归属随之出错。
            page_break = normalized.find("\f", start, hard_end + 1)
            ends_at_page_break = page_break >= 0
            # 收尾优先级：换页符 > 章节边界 > 自然标点；都不满足才硬切。
            end = (
                page_break
                if ends_at_page_break
                else (
                    hard_end
                    if section_end is not None
                    else self._preferred_end(normalized, start, hard_end)
                )
            )
            # 防御性兜底：正常情况下上面三个分支都会让 end > start（换页符已在循环开头
            # 跳过、章节边界满足 start < position）。真出现零长度窗口时退化为硬切，
            # 至少保证有进展，不会卡在同一位置。
            if end <= start:
                end = hard_end
                ends_at_page_break = False
            # 只裁掉首尾换行，不碰空格：缩进（代码块）和行尾空格都可能属于原文证据。
            chunk_text = normalized[start:end].strip("\r\n")
            if chunk_text.strip():
                # strip 会移动起点，这里重新计算精确偏移，确保引用能逐字回指。
                # 等式：exact_start = start + 被 lstrip 去掉的字符数。
                exact_start = (
                    start + len(normalized[start:end]) - len(normalized[start:end].lstrip("\r\n"))
                )
                exact_end = exact_start + len(chunk_text)
                # 分块 ID 绑定文档、序号与原始窗口偏移：换 chunk_size、换策略或文档内容
                # 变化都会得到新 ID，不会让旧索引里的 ID 指向新内容。截断到 16 位十六
                # 进制（64 bit）只为本地去重与日志可读，不作为防篡改或安全哈希。
                chunk_id = hashlib.sha1(
                    f"{document_id}:{index}:{start}:{end}".encode("utf-8")
                ).hexdigest()[:16]
                # 文档级元数据在前、分块级字段在后，后者可覆盖同名键。
                metadata = {**document_metadata, "chunk_index": index}
                if is_pdf:
                    # 页码 = 当前位置之前的换页符数量 + 1，不做 PDF 解析器二次推断。
                    # 因为分块不跨页且起点跳过 \f，这里的计数与阅读器页码一一对应。
                    metadata["page_number"] = normalized.count("\f", 0, start) + 1
                # end 是开区间，所以取 end - 1 这个“最后一个属于本块的字符”回查标题：
                # 恰好落在 end 处的标题属于下一块，不能记到当前块头上。
                heading = self._heading_at(headings, end - 1)
                if heading is not None:
                    metadata["section"] = heading[1]
                    metadata["heading_level"] = heading[2]
                chunks.append(
                    Chunk(
                        id=chunk_id,
                        document_id=document_id,
                        source=source,
                        text=chunk_text,
                        start_char=exact_start,
                        end_char=exact_end,
                        metadata=metadata,
                    )
                )
                # 只为真正产出的分块递增序号，保证 chunk_index 从 0 连续、
                # 与落库顺序一致；空窗口不会在序号里留下空洞。
                index += 1
            if end >= len(normalized):
                break
            if section_end is not None and end == section_end:
                # 新章节从边界重新开始，不携带上一章节的尾部重叠。
                # 重叠的意义是补上下文，跨章节重叠只会把上一节结论塞进下一节证据。
                start = end
            elif ends_at_page_break:
                # Never overlap across physical PDF pages: a chunk should have
                # one unambiguous page number for citations.
                start = end + 1
            else:
                # 普通窗口推进：回退 overlap 个字符做重叠，但至少前进 1 个字符。
                # 这个 max 是整个循环的终止性保证。
                start = max(end - self.overlap, start + 1)
        return chunks

    @staticmethod
    def _normalize(text: str) -> str:
        """统一换行符，只做最小规范化。

        技术文档里的缩进、代码块与列表层级本身携带语义，不能像普通文章那样压缩空白，
        否则代码示例会失真，评测中的逐字证据也会对不上。
        """
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Technical documents contain executable indentation and meaningful whitespace.
        # Normalize line endings only; never flatten code or list indentation.
        # 首尾 strip 是安全的：它只影响文档最外层空白，不会改变内部任何偏移关系。
        return text.strip()

    def _preferred_end(self, text: str, start: int, hard_end: int) -> int:
        """在窗口后 40% 的范围内寻找最靠后的自然边界。

        只从 ``chunk_size * 0.6`` 之后开始搜索，避免为了句号把分块切得过短、破坏
        “每块大致等长”的前提；找不到任何边界时回退到硬切位置。
        """
        # 已经到文末就没有“下一块”，不必为了美观提前收尾，否则最后一块会异常短。
        if hard_end >= len(text):
            return hard_end
        # 搜索区间固定为 [start + 60%, hard_end)：前 60% 一律不看，保证分块长度下限。
        search_from = start + int(self.chunk_size * 0.6)
        window = text[search_from:hard_end]
        # 每种标记各取“最后一次出现”的位置；再取所有标记中的最大值，即最靠后的边界。
        # rfind 找不到返回 -1，max 会自然忽略它。
        candidates = [window.rfind(mark) for mark in ("\n\n", "\n", "。", "！", "？", "；")]
        best = max(candidates, default=-1)
        # +1 把边界字符（换行或句号）留在当前分块内，下一块从它后面开始，
        # 这样两块拼起来不会丢字符，也不会重复该标点。
        return search_from + best + 1 if best >= 0 else hard_end

    @staticmethod
    def _document_metadata(source: str, text: str) -> dict[str, object]:
        """文档级元数据：Markdown 取一级标题，其余用文件名（去扩展名）。"""
        # 默认值保证任何格式都有可展示的标题，不因缺 H1 而让前端显示空白。
        title = Path(source).stem
        if Path(source).suffix.lower() == ".md":
            # (?m) 让 ^/$ 匹配行首行尾；.+? 非贪婪配合 \s*$ 顺带去掉行尾空白。
            # 只认 H1：再往下的小标题属于章节级信息，由 _markdown_headings 处理。
            match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
            if match:
                title = match.group(1).strip()
        return {"title": title}

    @staticmethod
    def _markdown_headings(source: str, text: str) -> list[tuple[int, str, int]]:
        """扫描 Markdown 标题，返回 ``(字符偏移, 标题文本, 层级)``。

        代码围栏内的 ``# 注释`` 不是标题，因此要先跟踪 ``` / ~~~ 围栏状态；
        围栏判定沿用 CommonMark 的“同字符且长度不短于开启标记”规则。
        """
        # 非 Markdown 直接返回空列表，调用方无需再判断文件类型。
        if Path(source).suffix.lower() != ".md":
            return []
        headings = []
        # 手工累加行长度得到字符偏移：keepends=True 保证 len(line) 含换行符，
        # 累加结果才与 normalized 的字符坐标完全一致。
        offset = 0
        fence: str | None = None
        for line in text.splitlines(keepends=True):
            # 允许围栏前有空白；只要以 3 个及以上相同标记开头就视为围栏行。
            fence_match = re.match(r"^\s*(`{3,}|~{3,})", line)
            if fence_match:
                marker = fence_match.group(1)
                if fence is None:
                    # 进入围栏，记下开启标记（含长度），后续必须同字符且不短于它才能闭合。
                    fence = marker
                elif marker[0] == fence[0] and len(marker) >= len(fence):
                    # 闭合围栏：标记字符相同且长度足够。这是简化实现，
                    # 不校验闭合行尾部是否只含空白，也未处理超过 3 空格缩进的边缘情况。
                    fence = None
            elif fence is None:
                # 只在围栏外识别 ATX 标题（1~6 个 # 后必须跟空白）。
                # offset 指向行首而不是 # 之后：章节边界切在标题行之前，
                # 让标题与它的正文留在同一块里。
                match = re.match(r"^(#{1,6})[ \t]+(.+?)\s*$", line)
                if match:
                    headings.append((offset, match.group(2).strip(), len(match.group(1))))
            offset += len(line)
        return headings

    @staticmethod
    def _heading_at(
        headings: list[tuple[int, str, int]], position: int
    ) -> tuple[int, str, int] | None:
        """返回位置之前最近的标题，即该分块所属章节。

        用线性扫描而非二分，是因为标题数量通常只有几十个，可读性优先；
        若语料增长到数千标题，这里可以安全替换为 ``bisect``。
        """
        # 标题按出现顺序排列，遇到第一个超过 position 的即可停止。
        current = None
        for heading in headings:
            if heading[0] > position:
                break
            current = heading
        return current
