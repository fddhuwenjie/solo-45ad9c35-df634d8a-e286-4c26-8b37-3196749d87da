# -*- coding: utf-8 -*-
"""页对照锚点：用户在并排预览中人工确认的「旧版页 ↔ 新版页」对照点。

启用的锚点按旧版页排序后必须形成**单调分段映射**：旧页严格递增、新页也严格
递增（不允许倒退、重复占用）。相邻锚点之间是一个分段区间，区间内旧页按线性
插值投影到新页（斜率小于 1 时允许多页折叠到同一新页，投影非递减即可）；
首锚之前 / 末锚之后沿用最近区间的斜率外推（无锚或仅有一锚时回退为斜率 1）。
**锚点页本身始终精确映射到人工指定的新页**，任何夹取/折叠都不得移动锚点。

本模块提供：
  * validate_anchor      —— 新建/启用单个锚点时的规则校验（倒退/重复/越界）；
  * anchor_rows          —— 取锚点行（active / all）；
  * build_ctx            —— 组装 matcher 使用的约束上下文（投影、区间、斜率）；
  * locator_bounds       —— 某个待确认定位号在锚点约束下的候选块范围；
  * segments / page_map  —— 分段与逐页映射（供前端与导出）；
  * anchor_conflicts     —— 停用锚点与现行映射的冲突、定位号漂移；
  * export_anchors_json  —— 含映射区间/备注/冲突的 JSON 对照表。
"""
import json


# ---- 读取 -----------------------------------------------------------------

def anchor_rows(conn, project_id, active_only=False):
    q = ("SELECT * FROM page_anchors WHERE project_id=?")
    if active_only:
        q += " AND status='active'"
    q += " ORDER BY old_page, id"
    return [dict(r) for r in conn.execute(q, (project_id,)).fetchall()]


def _page_counts(conn, project_id):
    ro = conn.execute(
        "SELECT MIN(page_no) lo, MAX(page_no) hi FROM pages "
        "WHERE project_id=? AND edition='old'", (project_id,)).fetchone()
    rn = conn.execute(
        "SELECT MIN(page_no) lo, MAX(page_no) hi FROM pages "
        "WHERE project_id=? AND edition='new'", (project_id,)).fetchone()
    return (
        (ro["lo"] if ro and ro["lo"] is not None else 1,
         ro["hi"] if ro and ro["hi"] is not None else 0),
        (rn["lo"] if rn and rn["lo"] is not None else 1,
         rn["hi"] if rn and rn["hi"] is not None else 0),
    )


# ---- 校验 -----------------------------------------------------------------

def validate_anchor(conn, project_id, old_page, new_page, ignore_id=None):
    """校验候选锚点（与其它**启用**锚点是否构成单调映射）。

    返回 None 表示通过；否则返回中文错误说明。
    """
    (olo, ohi), (nlo, nhi) = _page_counts(conn, project_id)
    if ohi and not (olo <= old_page <= ohi):
        return f"旧版页 {old_page} 超出旧版范围（{olo}–{ohi}）"
    if nhi and not (nlo <= new_page <= nhi):
        return f"新版页 {new_page} 超出新版范围（{nlo}–{nhi}）"

    others = [a for a in anchor_rows(conn, project_id, active_only=True)
              if a["id"] != ignore_id]
    for a in others:
        if a["old_page"] == old_page and a["new_page"] == new_page:
            return f"锚点 旧{old_page}→新{new_page} 已存在（重复占用）"
        if a["old_page"] == old_page:
            return f"旧版第 {old_page} 页已被锚点「旧{old_page}→新{a['new_page']}」占用"
        if a["new_page"] == new_page:
            return f"新版第 {new_page} 页已被锚点「旧{a['old_page']}→新{new_page}」占用"

    # 与前驱/后继锚点的单调性：旧页递增时新页也必须递增
    prev_a = max((a for a in others if a["old_page"] < old_page),
                 default=None, key=lambda a: a["old_page"])
    next_a = min((a for a in others if a["old_page"] > old_page),
                 default=None, key=lambda a: a["old_page"])
    if prev_a and new_page <= prev_a["new_page"]:
        return (f"映射倒退：前一锚点 旧{prev_a['old_page']}→新{prev_a['new_page']}，"
                f"旧页递增到 {old_page} 后新页 {new_page} 未大于 {prev_a['new_page']}")
    if next_a and new_page >= next_a["new_page"]:
        return (f"映射倒退：后一锚点 旧{next_a['old_page']}→新{next_a['new_page']}，"
                f"新页 {new_page} 必须小于 {next_a['new_page']}")
    return None


# ---- 分段映射 -------------------------------------------------------------

def _slope(a, b):
    do = b["old_page"] - a["old_page"]
    dn = b["new_page"] - a["new_page"]
    return dn / do if do else 1.0


def build_ctx(conn, project_id):
    """组装 matcher 约束上下文。无启用锚点时返回 None。"""
    act = anchor_rows(conn, project_id, active_only=True)
    if len(act) < 1:
        return None
    (olo, ohi), (nlo, nhi) = _page_counts(conn, project_id)
    slopes = {}
    for a, b in zip(act, act[1:]):
        slopes[a["old_page"]] = _slope(a, b)

    anchor_old = {a["old_page"]: a["new_page"] for a in act}

    def raw_project(op):
        """锚点页精确映射；其余页线性插值/外推（未取整、未 clamp）。"""
        if op in anchor_old:
            return float(anchor_old[op])
        if len(act) == 1:
            a = act[0]
            return a["new_page"] + (op - a["old_page"])
        if op < act[0]["old_page"]:
            a, b = act[0], act[1]
            return a["new_page"] + (op - a["old_page"]) * _slope(a, b)
        if op > act[-1]["old_page"]:
            a, b = act[-2], act[-1]
            return b["new_page"] + (op - b["old_page"]) * _slope(a, b)
        for a, b in zip(act, act[1:]):
            if a["old_page"] < op < b["old_page"]:
                return a["new_page"] + (op - a["old_page"]) * _slope(a, b)
        return float(anchor_old.get(op, op))

    def project(op):
        """对外投影：与逐页表一致（锚点页精确，非锚页四舍五入）。"""
        if op in anchor_old:
            return anchor_old[op]
        return _round_half_away(raw_project(op))

    # 逐页投影表（仅旧版实有页）。分段构造，关键不变量：
    #   1) 锚点页必须精确落在其人工指定的新页（压缩区间同样保留，如 旧10→新5）；
    #   2) 区间内允许非递减折叠（斜率 < 1 时多页可投影到同一新页），只做
    #      [前一值, 段末锚点新页] 夹取，绝不用 +1 顶开锚点；
    #   3) 首/尾外推段夹到全书新页范围。
    page_map = {}

    def fill_segment(pages, lo_bound, hi_bound):
        prev = None
        for op in pages:
            if op in anchor_old:
                np_ = anchor_old[op]            # 锚点页：精确值，不夹不取整
            else:
                np_ = _round_half_away(raw_project(op))
                if lo_bound is not None and np_ < lo_bound:
                    np_ = lo_bound
                if hi_bound is not None and np_ > hi_bound:
                    np_ = hi_bound
                if prev is not None and np_ < prev:
                    np_ = prev                   # 非递减（允许与前页相同）
            page_map[op] = np_
            prev = np_

    if len(act) == 1:
        a = act[0]
        # 单锚：锚点精确，两侧斜率 1 外推，夹到全书新页范围
        pages_before = [o for o in range(olo, a["old_page"])]
        fill_segment(pages_before, nlo, a["new_page"])
        fill_segment([a["old_page"]], None, None)
        fill_segment([o for o in range(a["old_page"] + 1, ohi + 1)],
                     a["new_page"], nhi)
    else:
        fill_segment([o for o in range(olo, act[0]["old_page"])],
                     nlo, act[0]["new_page"])
        fill_segment([act[0]["old_page"]], None, None)
        for a, b in zip(act, act[1:]):
            fill_segment([o for o in range(a["old_page"] + 1, b["old_page"])],
                         a["new_page"], b["new_page"])
            fill_segment([b["old_page"]], None, None)
        fill_segment([o for o in range(act[-1]["old_page"] + 1, ohi + 1)],
                     act[-1]["new_page"], nhi)

    return {
        "anchors": act,
        "slopes": slopes,
        "project": project,
        "page_map": page_map,
        "old_range": (olo, ohi),
        "new_range": (nlo, nhi),
    }


def _round_half_away(x):
    # 线性插值中 .5 稳定地远离 0，避免银行家舍入造成区间抖动
    import math
    return int(math.floor(x + 0.5)) if x >= 0 else int(math.ceil(x - 0.5))


def segments(conn, project_id, ctx=None):
    """返回映射区间列表（含首尾外推段），供面板/导出/前端着色。"""
    ctx = ctx or build_ctx(conn, project_id)
    if ctx is None:
        return []
    act = ctx["anchors"]
    olo, ohi = ctx["old_range"]
    nlo, nhi = ctx["new_range"]
    segs = []

    def push(idx, os_, oe, ns, ne, slope, kind):
        segs.append({
            "index": idx, "old_start": os_, "old_end": oe,
            "new_start": ns, "new_end": ne,
            "slope": None if slope is None else round(slope, 3),
            "kind": kind,  # head | interval | tail | single
        })

    if len(act) == 1:
        a = act[0]
        push(0, olo, ohi,
             ctx["page_map"].get(olo, a["new_page"]),
             ctx["page_map"].get(ohi, a["new_page"]),
             1.0, "single")
        return segs

    head_slope = _slope(act[0], act[1])
    push(0, olo, act[0]["old_page"], ctx["page_map"].get(olo), act[0]["new_page"],
         head_slope, "head")
    for i, (a, b) in enumerate(zip(act, act[1:]), start=1):
        push(i, a["old_page"], b["old_page"], a["new_page"], b["new_page"],
             _slope(a, b), "interval")
    tail_slope = _slope(act[-2], act[-1])
    push(len(act), act[-1]["old_page"], ohi, act[-1]["new_page"],
         ctx["page_map"].get(ohi), tail_slope, "tail")
    return segs


def _segment_for_old(ctx, op):
    """旧页归属的区间序号（与 segments() 输出的 index 对齐）。"""
    act = ctx["anchors"]
    if len(act) == 1:
        return 0
    if op <= act[0]["old_page"]:
        return 0
    for i, (a, b) in enumerate(zip(act, act[1:]), start=1):
        if a["old_page"] <= op <= b["old_page"]:
            return i
    return len(act)


# ---- 定位号约束 -----------------------------------------------------------

def locator_bounds(ctx, old_start, old_end):
    """旧范围 [old_start, old_end] 在锚点约束下允许的新版候选块边界。

    区间定义：候选块只能落在「范围两端各自相邻锚点」所夹的新版区间内；
    范围本身横跨锚点页时，块还必须覆盖这些锚点固定的新页。

    返回 dict：
      seg_lo/seg_hi  候选块允许覆盖的新页区间（相邻锚点边界，首尾段取全书边界）；
      pins           [(old_page, new_page)] 范围内被固定的锚点页；
      projected      (lo, hi) 逐页线性投影的新页范围（仅作提示，不强约束）；
      segment_index  主归属区间序号。
    """
    act = ctx["anchors"]
    pmap = ctx["page_map"]
    nlo, nhi = ctx["new_range"]
    old_end = old_end or old_start

    prev_a = next((a for a in reversed(act) if a["old_page"] <= old_start), None)
    next_a = next((a for a in act if a["old_page"] >= old_end), None)

    # 只有一个锚点且范围严格跨过它时，两侧都是外推段，仅用 pin 固定
    if len(act) == 1 and prev_a is next_a and not (
            old_start == act[0]["old_page"] or old_end == act[0]["old_page"]):
        seg_lo, seg_hi = nlo, nhi
    else:
        seg_lo = prev_a["new_page"] if prev_a is not None else nlo
        seg_hi = next_a["new_page"] if next_a is not None else nhi

    pins = [(a["old_page"], a["new_page"]) for a in act
            if old_start <= a["old_page"] <= old_end]

    ops = [op for op in range(old_start, old_end + 1) if op in pmap]
    projected = (min(pmap[op] for op in ops), max(pmap[op] for op in ops)) if ops else None

    # 所属区间：以范围内旧页最多的区间为准
    counts = {}
    for op in ops or [old_start]:
        i = _segment_for_old(ctx, op)
        counts[i] = counts.get(i, 0) + 1
    seg_idx = max(counts, key=counts.get)

    return {
        "seg_lo": seg_lo, "seg_hi": seg_hi,
        "pins": pins, "projected": projected, "segment_index": seg_idx,
    }


# ---- 冲突 -----------------------------------------------------------------

def _locator_drift(conn, project_id, ctx):
    """已确认/头名候选与锚点映射不一致的定位号（漂移）。"""
    rows = conn.execute(
        """SELECT l.id, l.old_start, l.old_end, l.new_start, l.new_end,
                  l.status, e.term, e.subterm,
                  c.new_start AS c_start, c.new_end AS c_end
             FROM locators l
             JOIN entries e ON e.id=l.entry_id
             LEFT JOIN candidates c ON c.locator_id=l.id AND c.rank=1
            WHERE e.project_id=?""", (project_id,)).fetchall()
    out = []
    for r in rows:
        s, e = r["old_start"], r["old_end"] or r["old_start"]
        ops = [op for op in range(s, e + 1) if op in ctx["page_map"]]
        if not ops:
            continue
        lo = min(ctx["page_map"][op] for op in ops)
        hi = max(ctx["page_map"][op] for op in ops)
        if r["status"] == "confirmed" and r["new_start"] is not None:
            ns, ne = r["new_start"], r["new_end"] or r["new_start"]
            if ns > hi or ne < lo:
                out.append({
                    "kind": "confirmed_drift",
                    "locator_id": r["id"],
                    "term": r["term"], "subterm": r["subterm"],
                    "old_range": [s, r["old_end"] or s],
                    "actual_range": [ns, ne],
                    "expected_range": [lo, hi],
                    "message": f"已确认的新版 {ns}-{ne} 落在锚点投影区间新{lo}–{hi} 之外",
                })
        elif r["c_start"] is not None:
            cs, ce = r["c_start"], r["c_end"]
            if cs > hi or ce < lo:
                out.append({
                    "kind": "candidate_drift",
                    "locator_id": r["id"],
                    "term": r["term"], "subterm": r["subterm"],
                    "old_range": [s, r["old_end"] or s],
                    "actual_range": [cs, ce],
                    "expected_range": [lo, hi],
                    "message": f"头名候选新版 {cs}-{ce} 偏离锚点投影区间新{lo}–{hi}，建议重匹配",
                })
    return out


def anchor_conflicts(conn, project_id, ctx=None):
    """停用/待校验锚点与现行启用映射的冲突，以及定位号漂移。"""
    ctx = ctx or build_ctx(conn, project_id)
    conflicts = []
    if ctx is not None:
        act = ctx["anchors"]
        active_old = {a["old_page"] for a in act}
        active_new = {a["new_page"] for a in act}
        for d in anchor_rows(conn, project_id):
            if d["status"] == "active":
                continue
            problems = []
            if d["old_page"] in active_old:
                other = next(a for a in act if a["old_page"] == d["old_page"])
                problems.append(f"旧版第 {d['old_page']} 页已被启用锚点占用（→新{other['new_page']}）")
            if d["new_page"] in active_new:
                other = next(a for a in act if a["new_page"] == d["new_page"])
                problems.append(f"新版第 {d['new_page']} 页已被启用锚点占用（旧{other['old_page']}→）")
            prev_a = max((a for a in act if a["old_page"] < d["old_page"]),
                         default=None, key=lambda a: a["old_page"])
            next_a = min((a for a in act if a["old_page"] > d["old_page"]),
                         default=None, key=lambda a: a["old_page"])
            if prev_a and d["new_page"] <= prev_a["new_page"]:
                problems.append(f"相对前锚点旧{prev_a['old_page']}→新{prev_a['new_page']} 倒退")
            if next_a and d["new_page"] >= next_a["new_page"]:
                problems.append(f"相对后锚点旧{next_a['old_page']}→新{next_a['new_page']} 倒退")
            # 与逐页投影偏差超过 2 页也视作分歧
            proj = ctx["page_map"].get(d["old_page"])
            if proj is not None and abs(proj - d["new_page"]) >= 3 and not problems:
                problems.append(f"与现行插值投影 新{proj} 偏差 {d['new_page'] - proj:+d} 页")
            if problems:
                conflicts.append({
                    "kind": "disabled_anchor",
                    "anchor_id": d["id"],
                    "old_page": d["old_page"], "new_page": d["new_page"],
                    "note": d["note"] or "",
                    "message": "；".join(problems),
                })
        conflicts.extend(_locator_drift(conn, project_id, ctx))
    return conflicts


# ---- 导出 -----------------------------------------------------------------

def export_anchors_json(conn, project_id):
    proj = conn.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone()
    ctx = build_ctx(conn, project_id)
    all_rows = anchor_rows(conn, project_id)
    segs = segments(conn, project_id, ctx) if ctx else []
    page_map = ctx["page_map"] if ctx else {}
    (olo, ohi), (nlo, nhi) = _page_counts(conn, project_id)

    # 每个定位号当前结果相对锚点区间的对照
    loc_rows = conn.execute(
        """SELECT l.id, l.old_start, l.old_end, l.new_start, l.new_end, l.status,
                  e.term, e.subterm, c.new_start AS c_start, c.new_end AS c_end,
                  c.method AS c_method, c.score AS c_score
             FROM locators l JOIN entries e ON e.id=l.entry_id
             LEFT JOIN candidates c ON c.locator_id=l.id AND c.rank=1
            WHERE e.project_id=? ORDER BY l.old_start, l.id""", (project_id,)).fetchall()
    locators = []
    for r in loc_rows:
        s, e = r["old_start"], r["old_end"] or r["old_start"]
        ops = [op for op in range(s, e + 1) if op in page_map]
        exp = [min(page_map[op] for op in ops), max(page_map[op] for op in ops)] if ops else None
        actual = None
        if r["status"] == "confirmed" and r["new_start"] is not None:
            actual = [r["new_start"], r["new_end"] or r["new_start"]]
        elif r["c_start"] is not None:
            actual = [r["c_start"], r["c_end"]]
        locators.append({
            "term": r["term"], "subterm": r["subterm"],
            "status": r["status"],
            "old_range": [s, r["old_end"] or s],
            "projected_new_range": exp,
            "actual_new_range": actual,
            "candidate_method": r["c_method"], "candidate_score": r["c_score"],
            "segment_index": _segment_for_old(ctx, s) if ctx else None,
            "in_sync": (exp is None or actual is None
                        or not (actual[0] > exp[1] or actual[1] < exp[0])),
        })

    payload = {
        "project": {"id": project_id, "name": proj["name"] if proj else ""},
        "generated_at": _now(),
        "page_counts": {"old": [olo, ohi], "new": [nlo, nhi]},
        "anchors": [
            {"id": a["id"], "old_page": a["old_page"], "new_page": a["new_page"],
             "status": a["status"], "note": a["note"] or "",
             "created_at": a["created_at"]}
            for a in all_rows
        ],
        "segments": segs,
        "page_map": {str(k): v for k, v in sorted(page_map.items())},
        "locators": locators,
        "conflicts": anchor_conflicts(conn, project_id, ctx),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
