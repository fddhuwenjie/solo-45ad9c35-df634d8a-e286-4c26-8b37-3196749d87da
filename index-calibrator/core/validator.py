# -*- coding: utf-8 -*-
"""实时一致性检查。

issue code 一览：
  inverted_range          起始页大于结束页（旧/新范围都查）
  broken_range            范围引用了书中不存在的页
  out_of_bounds           单页超出新版总页数
  duplicate_locator       同一条目下重复的页码范围
  duplicate_see           重复的“参见/见”关系
  missing_target          交叉引用目标条目不存在
  subterm_contradiction   子条目的页码落在主条目所有范围之外
  cross_chapter           新范围跨越章节起始页（仅当规则禁止时报问题，否则只作提示）
  chapter_parity          章节起始页不符合 recto/even 规则
"""
import re

ISSUE_META = {
    "inverted_range": ("error", "倒置的页码范围"),
    "broken_range": ("error", "断裂的页码范围"),
    "out_of_bounds": ("error", "页码超出全书范围"),
    "duplicate_locator": ("warning", "重复的页码范围"),
    "duplicate_see": ("warning", "重复的交叉引用"),
    "missing_target": ("error", "缺失的交叉引用目标"),
    "subterm_contradiction": ("error", "主/子条目页码矛盾"),
    "cross_chapter": ("warning", "范围跨越章节边界"),
    "chapter_parity": ("warning", "章节起始页规则不符"),
}


def detect_chapter_starts(conn, project_id, edition):
    proj = conn.execute("SELECT heading_regex FROM projects WHERE id=?", (project_id,)).fetchone()
    try:
        rx = re.compile(proj["heading_regex"], re.I | re.M)
    except re.error:
        return []
    rows = conn.execute(
        "SELECT page_no, content FROM pages WHERE project_id=? AND edition=? ORDER BY page_no",
        (project_id, edition))
    starts = []
    for r in rows:
        first_line = next((ln for ln in (r["content"] or "").splitlines() if ln.strip()), "")
        if rx.search(first_line):
            starts.append(r["page_no"])
    return starts


def _entry_label(e):
    return f"“{e['term']}{' — ' + e['subterm'] if e['subterm'] else ''}”"


def _add(issues, code, entry_id, locator_id, message):
    sev, _ = ISSUE_META[code]
    issues.append({
        "code": code, "severity": sev, "entry_id": entry_id,
        "locator_id": locator_id, "message": message,
    })


def _check_ranges(issues, loc, label, max_page, chapter_starts, cross_allowed, policy):
    """对一个范围（旧或新）执行结构性检查。"""
    s, e, which = loc["new_start"], loc["new_end"], "新版"
    if s is None:
        s, e, which = loc["old_start"], loc["old_end"], "旧版"
    if s is None:
        return
    e = e if e is not None else s
    if s > e:
        _add(issues, "inverted_range", loc["entry_id"], loc["id"],
             f"{label} 的{which}范围 {s}-{e} 起始页大于结束页")
        return
    if max_page and s > max_page:
        _add(issues, "out_of_bounds", loc["entry_id"], loc["id"],
             f"{label} 的{which}范围起点 {s} 超出全书 {max_page} 页")
        return
    if e > max_page:
        _add(issues, "broken_range", loc["entry_id"], loc["id"],
             f"{label} 的{which}范围 {s}-{e} 超出全书 {max_page} 页，范围断裂")
    if which == "新版" and chapter_starts and not cross_allowed:
        crossed = [c for c in chapter_starts if s < c <= e]
        if crossed:
            _add(issues, "cross_chapter", loc["entry_id"], loc["id"],
                 f"{label} 的新版范围 {s}-{e} 跨越章节起始页 {crossed[0]}")
        if s in chapter_starts:
            if policy == "recto" and s % 2 == 0:
                _add(issues, "chapter_parity", loc["entry_id"], loc["id"],
                     f"{label} 从偶数页 {s} 起排，规则要求章节从右页（奇数页）开始")
            elif policy == "even" and s % 2 == 1:
                _add(issues, "chapter_parity", loc["entry_id"], loc["id"],
                     f"{label} 从奇数页 {s} 起排，规则要求章节从左页（偶数页）开始")


def validate(conn, project_id):
    proj = conn.execute(
        "SELECT chapter_policy, cross_chapter, heading_regex FROM projects WHERE id=?",
        (project_id,)).fetchone()
    entries = conn.execute(
        "SELECT * FROM entries WHERE project_id=? ORDER BY term, subterm", (project_id,)).fetchall()
    locators = conn.execute(
        """SELECT l.*, e.term, e.subterm FROM locators l JOIN entries e ON e.id=l.entry_id
           WHERE e.project_id=?""", (project_id,)).fetchall()
    new_max = conn.execute(
        "SELECT MAX(page_no) m FROM pages WHERE project_id=? AND edition='new'", (project_id,)
    ).fetchone()["m"] or 0
    old_max = conn.execute(
        "SELECT MAX(page_no) m FROM pages WHERE project_id=? AND edition='old'", (project_id,)
    ).fetchone()["m"] or 0
    ch_new = detect_chapter_starts(conn, project_id, "new")

    issues = []
    by_entry = {}
    for loc in locators:
        by_entry.setdefault(loc["entry_id"], []).append(loc)

    # 1) 每个定位号的结构检查（旧范围 + 新范围）
    for loc in locators:
        label = _entry_label(loc)
        # 旧范围
        s, e = loc["old_start"], (loc["old_end"] or loc["old_start"])
        if s > e:
            _add(issues, "inverted_range", loc["entry_id"], loc["id"],
                 f"{label} 的旧版范围 {s}-{e} 起始页大于结束页，疑似录入颠倒")
        elif e > old_max:
            _add(issues, "broken_range", loc["entry_id"], loc["id"],
                 f"{label} 的旧版范围 {s}-{e} 超出旧版 {old_max} 页")
        # 新范围
        if loc["new_start"] is not None:
            _check_ranges(issues, loc, label, new_max, ch_new,
                          bool(proj["cross_chapter"]), proj["chapter_policy"])
        # 同条目下重复
        for other in by_entry[loc["entry_id"]]:
            if other["id"] >= loc["id"]:
                continue
            oe = other["old_end"] or other["old_start"]
            if not (e < other["old_start"] or s > oe):
                _add(issues, "duplicate_locator", loc["entry_id"], loc["id"],
                     f"{label} 的旧版范围 {s}-{e} 与另一范围 {other['old_start']}-{oe} 重叠或重复")

    # 2) 交叉引用：缺失目标 & 重复参见
    term_index = {(e["term"], e["subterm"]) for e in entries}
    main_terms = {e["term"] for e in entries}
    seen_refs = {}
    for e in entries:
        if e["kind"] in ("see", "seealso"):
            key = (e["term"], e["kind"], (e["ref_target"] or "").strip().lower())
            label = _entry_label(e)
            arrow = "见" if e["kind"] == "see" else "参见"
            if key in seen_refs:
                _add(issues, "duplicate_see", e["id"], None,
                     f"“{e['term']}” 到“{e['ref_target']}”的“{arrow}”关系重复出现")
            seen_refs[key] = True
            target = (e["ref_target"] or "").strip()
            if target and target not in main_terms:
                _add(issues, "missing_target", e["id"], None,
                     f"{label} {arrow}“{target}”，但索引中不存在该目标条目")

    # 3) 主/子条目页码矛盾。
    #    必须在同一版本轴内比较：主条目已确认新版页时，不能与仍待确认的
    #    子条目旧版页跨版本混比（换版偏移/拆分/合并会造成假矛盾）。
    parents = {}
    for e in entries:
        if not e["subterm"]:
            parents[e["term"]] = e["id"]

    def _spans_by_edition(entry_id):
        """返回 {'新版': [(s,e)], '旧版': [(s,e)]}；已确认的只收集新版。"""
        out = {"新版": [], "旧版": []}
        for loc in by_entry.get(entry_id, []):
            if loc["new_start"] is not None:
                out["新版"].append((loc["new_start"], loc["new_end"] or loc["new_start"]))
            else:
                out["旧版"].append((loc["old_start"], loc["old_end"] or loc["old_start"]))
        return out

    for e in entries:
        if not e["subterm"] or e["term"] not in parents:
            continue
        parent_spans = _spans_by_edition(parents[e["term"]])
        child_spans = _spans_by_edition(e["id"])
        for which in ("新版", "旧版"):
            pspans, cspans = parent_spans[which], child_spans[which]
            if not pspans or not cspans:
                # 父子分处不同版本轴时无法直接比较，跳过避免跨版本误报
                continue
            for cs, ce in cspans:
                covered = any(ps <= cs and ce <= pe for ps, pe in pspans)
                if not covered:
                    _add(issues, "subterm_contradiction", e["id"], None,
                         f"{_entry_label(e)} 的{which}页 {cs}-{ce} 落在主条目“{e['term']}”"
                         f"全部{which}范围之外")

    # 4) 章节起始页本身（新版每个章节首页的奇偶规则）
    if proj["chapter_policy"] in ("recto", "even"):
        for c in ch_new:
            if proj["chapter_policy"] == "recto" and c % 2 == 0:
                _add(issues, "chapter_parity", None, None,
                     f"新版章节从第 {c} 页（偶数页）开始，规则要求右页（奇数页）")
            if proj["chapter_policy"] == "even" and c % 2 == 1:
                _add(issues, "chapter_parity", None, None,
                     f"新版章节从第 {c} 页（奇数页）开始，规则要求左页（偶数页）")

    # ---- 写库：已不存在的问题标记 resolved；重新出现的问题从 resolved 重开 ----
    existing = {
        (r["code"], r["entry_id"], r["locator_id"], r["message"]): r
        for r in conn.execute("SELECT * FROM issues WHERE project_id=?", (project_id,))
    }
    current_keys = set()
    for it in issues:
        key = (it["code"], it["entry_id"], it["locator_id"], it["message"])
        current_keys.add(key)
        row = existing.get(key)
        if row is None:
            conn.execute(
                "INSERT INTO issues (project_id, code, severity, entry_id, locator_id, message)"
                " VALUES (?,?,?,?,?,?)",
                (project_id, it["code"], it["severity"], it["entry_id"],
                 it["locator_id"], it["message"]))
        elif row["status"] != "open":
            # 同一问题此前已解决，条件再次满足（如规则改回 recto）时重新打开
            conn.execute(
                "UPDATE issues SET status='open', severity=? WHERE id=?",
                (it["severity"], row["id"]))
    for key, row in existing.items():
        if key not in current_keys and row["status"] == "open":
            conn.execute("UPDATE issues SET status='resolved' WHERE id=?", (row["id"],))
    conn.commit()
    return {"open": sum(1 for i in issues), "total_checked": len(locators)}
