# -*- coding: utf-8 -*-
"""文本规范化、指纹（5-gram shingles）、分句等共享工具。"""
import html
import json
import re
import unicodedata

# 常见英语停用词；标题词命中时这些词不计入“强信号”
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "by", "as", "is", "are", "was", "were", "be", "been", "at", "from",
    "that", "this", "these", "those", "it", "its", "their", "his", "her",
    "into", "than", "then", "also", "may", "can", "will", "not", "but",
}


def normalize(text):
    """统一 unicode、小写、折叠空白与标点，供指纹比较。"""
    if text is None:
        return ""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^a-z0-9一-鿿\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def shingles(text, k=5):
    """字符 k-gram 集合，对段落内小幅增删具有鲁棒性。"""
    norm = normalize(text).replace(" ", "")
    if len(norm) < k:
        return {norm} if norm else set()
    return {norm[i:i + k] for i in range(len(norm) - k + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def containment(small, big):
    """small 的指纹有多少比例出现在 big 中（对拆分页更公平）。"""
    if not small:
        return 0.0
    return len(small & big) / len(small)


def content_terms(term):
    """从条目文字中提取强信号词（去停用词），另保留最长词作为弱信号。"""
    norm = normalize(term)
    words = [w for w in norm.split() if w and not w.isdigit()]
    strong = [w for w in words if w not in STOPWORDS and len(w) >= 3]
    return strong or words


def split_sentences(text):
    """按句末标点粗略分句，保留原句（去首尾空白）。"""
    parts = re.split(r"(?<=[.!?。！？])\s+|\n+", text)
    return [p.strip() for p in parts if p and p.strip()]


def short_title(page_content, max_len=80):
    """取页面中第一行非空文本作为标题（用于匹配的标题信号）。"""
    for line in page_content.splitlines():
        line = line.strip()
        if line:
            return line[:max_len]
    return ""


# ---- 人工决定的撤销日志 -------------------------------------------------
# 每条变更记录一条“反向 SQL”及其参数；撤销时顺序执行并删除该日志。

def log_action(conn, project_id, kind, detail, undo_sql, undo_params=()):
    """undo_sql 可以是单条 SQL 字符串，或 [(sql, params), ...] 列表。"""
    if isinstance(undo_sql, str):
        payload = {"sql": undo_sql, "params": list(undo_params)}
    else:
        payload = {"statements": [{"sql": s, "params": list(p)} for s, p in undo_sql]}
    conn.execute(
        "INSERT INTO action_log (project_id, kind, detail, undo_sql) VALUES (?,?,?,?)",
        (project_id, kind, json.dumps(detail, ensure_ascii=False),
         json.dumps(payload, ensure_ascii=False)),
    )


def undo_last(conn, project_id):
    row = conn.execute(
        "SELECT * FROM action_log WHERE project_id=? ORDER BY id DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if not row:
        return None
    payload = json.loads(row["undo_sql"])
    if "statements" in payload:
        for st in payload["statements"]:
            conn.execute(st["sql"], st["params"])
    else:
        conn.execute(payload["sql"], payload["params"])
    conn.execute("DELETE FROM action_log WHERE id=?", (row["id"],))
    return json.loads(row["detail"])


def page_esc(text):
    """供前端高亮前的安全转义。"""
    return html.escape(text or "")


def parse_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
