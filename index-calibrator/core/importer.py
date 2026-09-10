# -*- coding: utf-8 -*-
"""旧/新版分页纯文本与索引 CSV 的导入。

分页文本支持两种分页标记（可混用）：
  1) 形如 ``=== PAGE 12 ===`` / ``--- 页码 12 ---`` 的独立行（括号内可写
     ``xii`` 之类的罗马数字标签，非数字时按序号生成页码）；
  2) 换页符 ``\\f``（form feed）。
不带任何标记的文本整体视为第 1 页。

CSV 列（表头大小写不敏感，支持别名）：
  term, subterm/sub, kind/type（term | see | seealso / "see also"）,
  target/see/ref（交叉引用目标）, pages/locators/ranges（页码，分号或逗号分隔，
  形如 7 或 7-10）
"""
import csv
import io
import re

from .utils import parse_int

PAGE_MARK = re.compile(r"^\s*(?:=|-|\*|#){2,}\s*(?:page|页码)?\s*([0-9ivxlcdmIVXLCDM]+)\s*(?:=|-|\*|#){0,}\s*$",
                       re.IGNORECASE)
RANGE_SPLIT = re.compile(r"[;；]")


def parse_paged_text(text):
    """返回 [(page_no:int, label:str|None, content:str), ...]，页码从 1 连续。"""
    pages = []
    label = None
    buf = []
    seq = 0

    def flush():
        nonlocal seq, buf, label
        content = "\n".join(buf).strip()
        if content or label is not None:
            seq += 1
            pages.append((seq, label, content))
        buf = []
        label = None

    for raw in text.split("\f"):
        for line in raw.splitlines():
            m = PAGE_MARK.match(line)
            if m:
                flush()
                token = m.group(1)
                label = token
            else:
                buf.append(line)
        flush()
    return pages


def _norm_header(h):
    return (h or "").strip().lstrip("﻿").lower().replace(" ", "_")


_ALIASES = {
    "term": "term", "main": "term", "main_entry": "term",
    "subterm": "subterm", "sub": "subterm", "sub_entry": "subterm",
    "kind": "kind", "type": "kind", "entry_type": "kind",
    "target": "target", "see": "target", "ref": "target", "reference": "target",
    "pages": "pages", "locators": "pages", "ranges": "pages", "page": "pages",
}


def parse_index_csv(text):
    """返回 [{term, subterm, kind, target, ranges:[(s,e|None,s_raw)]}]。

    解析宽松：非法页码段保留在 s_raw 中，由调用方决定是否记录问题。
    """
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for raw in reader:
        col = {}
        for k, v in raw.items():
            if k is None:
                continue
            key = _ALIASES.get(_norm_header(k))
            if key:
                col[key] = (v or "").strip()
        term = col.get("term", "")
        if not term:
            continue
        kind_raw = col.get("kind", "term").lower().replace(" ", "")
        if kind_raw in ("seealso", "seealso:", "参见", "互见"):
            kind = "seealso"
        elif kind_raw in ("see", "见"):
            kind = "see"
        else:
            kind = "term"
        target = col.get("target", "")
        ranges = []
        for chunk in RANGE_SPLIT.split(col.get("pages", "")):
            chunk = chunk.strip()
            if not chunk:
                continue
            m_range = re.match(r"^(\d+)\s*[-–—~至]\s*(\d+)$", chunk)
            if m_range:
                ranges.append((int(m_range.group(1)), int(m_range.group(2)), chunk))
                continue
            n = parse_int(chunk)
            if n is not None:
                ranges.append((n, None, chunk))
                continue
            # “see: Honey” 这类把交叉引用写进 pages 列的容错
            mm = re.match(r"^see\s*also\s*:\s*(.+)$", chunk, re.I)
            m1 = re.match(r"^see\s*:\s*(.+)$", chunk, re.I)
            if mm:
                kind, target = "seealso", mm.group(1).strip()
            elif m1:
                kind, target = "see", m1.group(1).strip()
            ranges.append((None, None, chunk))  # 保留原文，调用方按需记录坏段
        # 容错：坏段若是 see/seealso 则升级条目类型并剔除
        cleaned = []
        for s, e, raw_chunk in ranges:
            if s is not None:
                cleaned.append((s, e, raw_chunk))
            else:
                mm = re.match(r"^see\s*also\s*:\s*(.+)$", raw_chunk, re.I)
                m1 = re.match(r"^see\s*:\s*(.+)$", raw_chunk, re.I)
                if mm:
                    kind, target = "seealso", mm.group(1).strip()
                elif m1:
                    kind, target = "see", m1.group(1).strip()
        ranges = cleaned
        rows.append({
            "term": term,
            "subterm": col.get("subterm") or None,
            "kind": kind,
            "target": target or None,
            "ranges": ranges,
        })
    return rows
