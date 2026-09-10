# -*- coding: utf-8 -*-
"""候选页匹配引擎。

三路信号（在 :mod:`core.utils` 中实现基础算法）：
  1. 上下文指纹：旧页范围合并文本的 5-gram shingles，与每个新页比较
     Jaccard + containment（containment 对“旧一页 → 新两页”的拆分更公平）；
  2. 邻近语句：从旧窗口中挑选含条目词的关键句（anchor），在新页逐句比对；
  3. 标题词：新页首行标题中出现的条目实词。

候选生成三种方法（合并去重）：
  * mapped —— 旧窗口每页 argmax 新页的并集（可表现为拆分/合并）；
  * offset —— 全书最常见的换版偏移；
  * window —— 在长度 L-1/L/L+1 的连续新页窗口中取平均分最高者。
"""
import json
from collections import Counter

from .utils import (
    content_terms, jaccard, containment, normalize, shingles,
    short_title, split_sentences,
)

W_SIM, W_ANCHOR, W_TERM, W_TITLE = 0.50, 0.35, 0.10, 0.05
MEDIUM_LINE = 0.55


# ---- 页面索引 -----------------------------------------------------------

def _page_index(rows):
    pages = {}
    for r in rows:
        content = r["content"] or ""
        sents = split_sentences(content)
        pages[r["page_no"]] = {
            "page_no": r["page_no"],
            "label": r["label"],
            "content": content,
            "title": short_title(content),
            "norm": normalize(content),
            "sh": shingles(content),
            "sentences": [{
                "text": s,
                "norm": normalize(s),
                "sh": shingles(s),
            } for s in sents],
        }
    return pages


def _pick_anchors(old_pages, window_nos, terms):
    """从旧窗口挑 2 条最能代表条目的邻近语句。"""
    sentences = []
    for no in window_nos:
        for s in split_sentences(old_pages[no]["content"]):
            sentences.append(s)
    if not sentences:
        return []
    scored = []
    termset = set(terms)
    for s in sentences:
        n = normalize(s)
        words = set(n.split())
        hits = len(termset & words)
        if hits:
            scored.append((hits, -len(n), s))
    scored.sort(reverse=True)
    anchors = [t[2] for t in scored[:2]]
    if not anchors:
        # 条目词在旧页中找不到时，回退到窗口前两句，靠邻近语句指纹定位
        anchors = sentences[:2]
    return anchors


# ---- 单页打分 -----------------------------------------------------------

def _page_sim(old_pages, window_nos, newp):
    """旧窗口 vs 单个新页的指纹相似度。"""
    pair_scores = []
    for no in window_nos:
        op = old_pages[no]
        if op["sh"] and newp["sh"]:
            pair_scores.append(
                0.5 * jaccard(op["sh"], newp["sh"])
                + 0.5 * containment(op["sh"], newp["sh"])
            )
    mean_pair = sum(pair_scores) / len(pair_scores) if pair_scores else 0.0
    window_sh = set()
    for no in window_nos:
        window_sh |= old_pages[no]["sh"]
    cover = containment(window_sh, newp["sh"]) if window_sh else 0.0
    return 0.5 * mean_pair + 0.5 * cover, window_sh


def _anchor_score(anchors, newp, terms):
    vals = []
    for a in anchors:
        ash = shingles(a)
        anorm_words = set(normalize(a).split())
        best, best_sent = 0.0, None
        for s in newp["sentences"]:
            c = containment(ash, s["sh"])
            if c > best:
                best, best_sent = c, s
        if best_sent is not None:
            term_hit = len(set(terms) & set(best_sent["norm"].split()))
            term_frac = (term_hit / max(1, len(set(terms)))) if terms else 0.0
            vals.append(best * (0.75 + 0.25 * term_frac))
        else:
            vals.append(0.0)
    return sum(vals) / len(vals) if vals else 0.0


def _score_page(old_pages, window_nos, newp, anchors, terms):
    sim, window_sh = _page_sim(old_pages, window_nos, newp)
    anchor = _anchor_score(anchors, newp, terms)
    term_frac = (sum(1 for t in terms if t in newp["norm"]) / len(terms)) if terms else 0.0
    title_norm = normalize(newp["title"])
    title_frac = (sum(1 for t in terms if t in title_norm) / len(terms)) if terms else 0.0
    score = W_SIM * sim + W_ANCHOR * anchor + W_TERM * term_frac + W_TITLE * title_frac
    return {
        "score": score,
        "sim": sim,
        "anchor": anchor,
        "term": term_frac,
        "title": title_frac,
        "window_sh": window_sh,
    }


# ---- 旧→新映射与全局偏移 ------------------------------------------------

def _old_to_new(old, new, anchors_cache, terms_cache):
    """每个旧页独立 argmax，供 mapped 候选与拆分/合并判断。"""
    mapping, offsets = {}, []
    for no, op in old.items():
        best_no, best_score = None, 0.0
        anchors = _pick_anchors(old, [no], anchors_cache["_all_terms"])
        for nno, np in new.items():
            r = _score_page(old, [no], np, anchors, anchors_cache["_all_terms"])
            if r["score"] > best_score:
                best_no, best_score = nno, r["score"]
        if best_no is not None and best_score >= 0.22:
            mapping[no] = best_no
            offsets.append(best_no - no)
    mode = Counter(offsets).most_common(1)[0] if offsets else (None, 0)
    ratio = mode[1] / len(offsets) if offsets else 0.0
    return mapping, (mode[0], ratio)


# ---- 章节（用于“跨章”歧义原因） -----------------------------------------

def _chapter_starts(pages, starts):
    return [s for s in starts if s in pages]


def _crosses_chapter(new_start, new_end, chapter_starts):
    return any(new_start < c <= new_end for c in chapter_starts)


# ---- 主入口 -------------------------------------------------------------

def run_matching(conn, project_id, only_locator_ids=None, chapter_starts_new=None,
                 threshold=0.85):
    proj = conn.execute("SELECT heading_regex FROM projects WHERE id=?", (project_id,)).fetchone()
    old_rows = conn.execute(
        "SELECT * FROM pages WHERE project_id=? AND edition='old' ORDER BY page_no",
        (project_id,)).fetchall()
    new_rows = conn.execute(
        "SELECT * FROM pages WHERE project_id=? AND edition='new' ORDER BY page_no",
        (project_id,)).fetchall()
    old, new = _page_index(old_rows), _page_index(new_rows)
    if not old or not new:
        return

    chapter_starts_new = chapter_starts_new or []

    # 全书映射（用通用词表估计，不需要条目词）
    cache = {"_all_terms": []}
    mapping, (global_offset, offset_ratio) = _old_to_new(old, new, cache, cache)

    q = ("SELECT l.*, e.term, e.subterm FROM locators l JOIN entries e ON e.id=l.entry_id "
         "WHERE e.project_id=? AND l.status='pending'")
    args = [project_id]
    if only_locator_ids:
        q += " AND l.id IN (%s)" % ",".join("?" * len(only_locator_ids))
        args += list(only_locator_ids)
    locators = conn.execute(q, args).fetchall()

    new_nos = sorted(new)
    for loc in locators:
        s, e = loc["old_start"], (loc["old_end"] or loc["old_start"])
        window_nos = [n for n in range(s, e + 1) if n in old]
        if not window_nos:
            continue
        terms = content_terms((loc["subterm"] or "") + " " + loc["term"]) \
            if loc["subterm"] else content_terms(loc["term"])
        anchors = _pick_anchors(old, window_nos, terms)
        conn.execute("UPDATE locators SET anchor=? WHERE id=?",
                     (" | ".join(a[:120] for a in anchors), loc["id"]))

        length = e - s + 1
        window_sh = set()
        for ono in window_nos:
            window_sh |= old[ono]["sh"]

        def block_metrics(start, cand_len):
            """候选块的各项指标（相似度必须双向：recall=旧内容覆盖率，
            precision=块中指纹真正属于旧窗口的比例，惩罚吸入相邻无关页）。"""
            block = [n for n in range(start, start + cand_len) if n in new]
            if len(block) != cand_len:
                return None
            merged_sh = set()
            cover_anchor, term_pages, title_pages, pair_acc = 0, 0, 0, []
            for n in block:
                np = new[n]
                merged_sh |= np["sh"]
                cover_anchor = max(cover_anchor, _anchor_score(anchors, np, terms))
                if terms and any(t in np["norm"] for t in terms):
                    term_pages += 1
                tn = normalize(np["title"])
                if terms and any(t in tn for t in terms):
                    title_pages += 1
                for ono in window_nos:
                    pair_acc.append(0.5 * jaccard(old[ono]["sh"], np["sh"])
                                    + 0.5 * containment(old[ono]["sh"], np["sh"]))
            recall = containment(window_sh, merged_sh) if window_sh else 0.0
            precision = containment(merged_sh, window_sh) if merged_sh else 0.0
            f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
            mean_pair = sum(pair_acc) / len(pair_acc) if pair_acc else 0.0
            sim = 0.5 * f1 + 0.5 * mean_pair
            return {
                "recall": recall, "sim": sim, "anchor": cover_anchor,
                "term_frac": term_pages / cand_len,
                "title_frac": title_pages / cand_len,
                "cand_len": cand_len,
            }

        def quality(m, base_recall=None, gate_split=False):
            q = W_SIM * m["sim"] + W_ANCHOR * m["anchor"] \
                + W_TERM * m["term_frac"] + W_TITLE * m["title_frac"]
            extra = m["cand_len"] - length
            if extra > 0:
                # 真拆分判定：只有当“不扩张时召回本来就不全”（<0.8），
                # 且扩张带来显著边际召回（>=0.3）时，新增页面才确实承载了
                # 旧窗口的缺失内容；否则视为吸入相邻页，扩张候选直接不成立。
                marginal = m["recall"] - (base_recall if base_recall is not None else 0.0)
                is_split = base_recall is not None and base_recall < 0.8 and marginal >= 0.3
                if gate_split and not is_split:
                    return None
                if is_split:
                    q += 0.06
                else:
                    q -= 0.015 * extra
            return q

        # 各候选长度下的最优块与其召回率（供扩张候选计算边际召回）
        lens = sorted({max(1, length - 1), length, min(len(new), length + 1)})
        best_by_len = {}
        for cand_len in lens:
            best_start, best_m, best_q = None, None, -1.0
            for start in new_nos:
                m = block_metrics(start, cand_len)
                if m is None:
                    continue
                q = quality(m)
                if q > best_q:
                    best_start, best_m, best_q = start, m, q
            if best_start is not None:
                best_by_len[cand_len] = (best_start, best_m, best_q)
        base_recall = best_by_len[length][1]["recall"] if length in best_by_len else None

        # --- 三种候选方法 ---
        raw_cands = []

        mapped_targets = sorted({mapping.get(n) for n in window_nos} - {None})
        if mapped_targets:
            ms, me = min(mapped_targets), max(mapped_targets)
            mm = block_metrics(ms, me - ms + 1)
            if mm is not None:
                qq = quality(mm, base_recall, gate_split=(mm["cand_len"] > length))
                if qq is not None:
                    raw_cands.append((ms, me, "mapped", qq))

        if global_offset is not None and offset_ratio >= 0.5:
            os_, oe = s + global_offset, e + global_offset
            if os_ in new and oe in new:
                om = block_metrics(os_, oe - os_ + 1)
                if om is not None:
                    qq = quality(om, base_recall, gate_split=(om["cand_len"] > length))
                    if qq is not None:
                        raw_cands.append((os_, oe, "offset", qq))

        # window 候选：非扩张长度直接取最优；扩张长度仅在真拆分时成立
        for cand_len in lens:
            best_start, best_m, _ = best_by_len[cand_len]
            if cand_len > length:
                qq = quality(best_m, base_recall, gate_split=True)
                if qq is None:
                    continue
            else:
                qq = quality(best_m, base_recall)
            raw_cands.append((best_start, best_start + cand_len - 1, "window", qq))

        # 去重并计算范围分（合并方法标签）
        seen_methods = {}
        for cs, ce, method, _q in raw_cands:
            seen_methods.setdefault((cs, ce), []).append(method)

        merged = []
        for (cs, ce), methods in seen_methods.items():
            m = block_metrics(cs, ce - cs + 1)
            rscore = quality(m, base_recall, gate_split=(m["cand_len"] > length))
            if rscore is None:
                continue
            rscore = min(1.0, rscore)
            method = "combined" if len(set(methods)) > 1 else methods[0]
            merged.append({"start": cs, "end": ce, "method": method,
                           "score": round(rscore, 4), "coverage": round(m["recall"], 3)})

        merged.sort(key=lambda c: c["score"], reverse=True)
        cands = merged[:4]

        # --- 头名候选的歧义原因 ---
        top = cands[0]
        reasons = []
        second_score = cands[1]["score"] if len(cands) > 1 else 0.0
        old_len, new_len = length, top["end"] - top["start"] + 1
        mapped_span = (max(mapped_targets) - min(mapped_targets) + 1) if mapped_targets else old_len

        if new_len > old_len or mapped_span > old_len:
            reasons.append(["split", "旧版内容在新版跨了更多页（段落拆分/排版流动）"])
        if new_len < old_len:
            reasons.append(["merged", "旧版多页内容在新版合并到同一页（跨页合并）"])
        if global_offset is None or offset_ratio < 0.75:
            reasons.append(["offset_inconsistent",
                            f"全书换版偏移不统一（最常见偏移占比 {offset_ratio:.0%}），不能整体平移"])
        elif top["method"] != "offset" and (top["start"] - s) != global_offset:
            reasons.append(["outlier",
                            f"本处偏移 {top['start'] - s:+d} 与全书主偏移 {global_offset:+d} 不一致"])
        if top["score"] - second_score < 0.06 and second_score > 0:
            reasons.append(["tie", f"头名与次候选仅差 {top['score'] - second_score:.2f}，存在竞争候选页"])
        top_text_norm = " ".join(new[n]["norm"] for n in range(top["start"], top["end"] + 1) if n in new)
        if terms and not any(t in top_text_norm for t in terms):
            reasons.append(["term_absent", "候选页中未出现条目词，仅靠上下文指纹命中"])
        if top["score"] < 0.40 or top.get("coverage", 1) < 0.35:
            reasons.append(["low_coverage", "指纹与邻近语句覆盖率偏低，建议人工核对原页"])
        if _crosses_chapter(top["start"], top["end"], chapter_starts_new):
            reasons.append(["cross_chapter", "候选范围跨越新版章节起始页"])

        # offset_inconsistent 是全书性提示，不单独降低单条目的置信度；
        # 真正拦截批量接受的是竞争候选/词条缺失/覆盖过低/离群等条目级信号。
        ambiguous_codes = {"tie", "term_absent", "low_coverage", "outlier"}
        if top["score"] >= threshold and not {r[0] for r in reasons} & ambiguous_codes:
            conf = "high"
        elif top["score"] >= MEDIUM_LINE:
            conf = "medium"
        else:
            conf = "low"

        # 候选页中可高亮的命中句（供并排视图）
        highlights = {}
        for c in cands:
            for nno in range(c["start"], c["end"] + 1):
                np = new.get(nno)
                if not np or not anchors:
                    continue
                hits = []
                for sent in np["sentences"]:
                    best = max((containment(shingles(a), sent["sh"]) for a in anchors), default=0.0)
                    if best >= 0.30:
                        hits.append(sent["text"])
                if hits:
                    highlights[str(nno)] = hits[:3]

        conn.execute("DELETE FROM candidates WHERE locator_id=?", (loc["id"],))
        for rank, c in enumerate(cands, 1):
            reason_blob = reasons if rank == 1 else []
            conn.execute(
                "INSERT INTO candidates (locator_id, rank, new_start, new_end, method, score, reasons)"
                " VALUES (?,?,?,?,?,?,?)",
                (loc["id"], rank, c["start"], c["end"], c["method"], c["score"],
                 json.dumps({"reasons": reason_blob, "confidence": conf,
                             "highlights": highlights}, ensure_ascii=False)),
            )
    conn.commit()
    return {"global_offset": global_offset, "offset_ratio": round(offset_ratio, 3),
            "mapping": {str(k): v for k, v in mapping.items()}}
