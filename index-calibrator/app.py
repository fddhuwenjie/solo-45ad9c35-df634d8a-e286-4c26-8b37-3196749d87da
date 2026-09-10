# -*- coding: utf-8 -*-
"""Flask 入口：项目、导入、匹配、逐条/批量决定、撤销、快照、锚点、导出。"""
import json
import sqlite3

from flask import Flask, Response, jsonify, render_template, request

from core.anchors import (
    anchor_conflicts, anchor_rows, build_ctx, export_anchors_json,
    segments as anchor_segments, validate_anchor,
)
from core.db import get_db, init_db
from core.exporter import export_csv, export_issues, export_proof_html
from core.importer import parse_index_csv, parse_paged_text
from core.matcher import run_matching
from core.sample_data import load_sample
from core.state import all_candidates, build_state
from core.utils import log_action, undo_last
from core.validator import detect_chapter_starts, validate

app = Flask(__name__)


def _refresh(conn, pid, rematch_entry=None):
    """决定/批量操作后：重算该条目下仍待确认的定位号，然后重新校验。"""
    ids = None
    if rematch_entry is not None:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM locators WHERE entry_id=? AND status='pending'",
            (rematch_entry,)).fetchall()]
    ch_new = detect_chapter_starts(conn, pid, "new")
    threshold = conn.execute(
        "SELECT batch_threshold FROM projects WHERE id=?", (pid,)).fetchone()[0]
    if ids is None or ids:
        info = run_matching(conn, pid, only_locator_ids=ids,
                            chapter_starts_new=ch_new, threshold=threshold)
        if info and ids is None:
            conn.execute(
                "INSERT INTO meta (project_id,key,value) VALUES (?,?,?) "
                "ON CONFLICT(project_id,key) DO UPDATE SET value=excluded.value",
                (pid, "last_match", json.dumps(info, ensure_ascii=False)))
    validate(conn, pid)


# ---- 页面与项目 ---------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/projects", methods=["GET"])
def list_projects():
    conn = get_db()
    rows = conn.execute(
        """SELECT p.id, p.name, p.created_at,
              (SELECT COUNT(*) FROM entries WHERE project_id=p.id) AS entry_count
           FROM projects p ORDER BY p.id DESC""").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/projects", methods=["POST"])
def create_project():
    data = request.get_json(force=True)
    conn = get_db()
    cur = conn.execute("INSERT INTO projects (name) VALUES (?)",
                       (data.get("name") or "未命名项目",))
    conn.commit()
    return jsonify({"id": cur.lastrowid})


@app.route("/api/projects/<int:pid>", methods=["DELETE"])
def delete_project(pid):
    conn = get_db()
    conn.execute("DELETE FROM projects WHERE id=?", (pid,))
    conn.commit()
    return jsonify({"ok": True})


@app.route("/api/sample", methods=["POST"])
def create_sample():
    conn = get_db()
    pid = load_sample(conn)
    conn.commit()
    return jsonify({"id": pid})


@app.route("/api/state/<int:pid>")
def state(pid):
    conn = get_db()
    st = build_state(conn, pid)
    if st is None:
        return jsonify({"error": "project not found"}), 404
    return jsonify(st)


# ---- 导入 ---------------------------------------------------------------

@app.route("/api/projects/<int:pid>/import", methods=["POST"])
def import_payload(pid):
    """JSON: {edition: 'old'|'new'|null(不覆盖), text, csv, mode: 'replace'|'append'}。"""
    data = request.get_json(force=True)
    conn = get_db()
    mode = data.get("mode", "replace")

    if data.get("text") and data.get("edition") in ("old", "new"):
        if mode == "replace":
            conn.execute("DELETE FROM pages WHERE project_id=? AND edition=?",
                         (pid, data["edition"]))
        pages = parse_paged_text(data["text"])
        for page_no, label, content in pages:
            conn.execute(
                "INSERT INTO pages (project_id, edition, page_no, label, content)"
                " VALUES (?,?,?,?,?)",
                (pid, data["edition"], page_no, label, content))

    if data.get("csv"):
        if mode == "replace":
            conn.execute("DELETE FROM entries WHERE project_id=?", (pid,))
        for i, row in enumerate(parse_index_csv(data["csv"])):
            parent_id = None
            if row["subterm"] and row["kind"] == "term":
                prow = conn.execute(
                    "SELECT id FROM entries WHERE project_id=? AND term=? AND subterm IS NULL",
                    (pid, row["term"])).fetchone()
                parent_id = prow["id"] if prow else None
            cur = conn.execute(
                "INSERT INTO entries (project_id, term, subterm, kind, ref_target, parent_id, sort_order)"
                " VALUES (?,?,?,?,?,?,?)",
                (pid, row["term"], row["subterm"], row["kind"], row["target"],
                 parent_id, i))
            entry_id = cur.lastrowid
            for s, e, _raw in row["ranges"]:
                if s is None:
                    continue
                conn.execute(
                    "INSERT INTO locators (entry_id, old_start, old_end) VALUES (?,?,?)",
                    (entry_id, s, e))

    conn.commit()
    validate(conn, pid)
    return jsonify({"ok": True})


# ---- 匹配 ---------------------------------------------------------------

@app.route("/api/projects/<int:pid>/match", methods=["POST"])
def match_all(pid):
    conn = get_db()
    ch_new = detect_chapter_starts(conn, pid, "new")
    threshold = conn.execute(
        "SELECT batch_threshold FROM projects WHERE id=?", (pid,)).fetchone()[0]
    info = run_matching(conn, pid, chapter_starts_new=ch_new, threshold=threshold)
    conn.execute(
        "INSERT INTO meta (project_id,key,value) VALUES (?,?,?) "
        "ON CONFLICT(project_id,key) DO UPDATE SET value=excluded.value",
        (pid, "last_match", json.dumps(info, ensure_ascii=False)))
    validate(conn, pid)
    return jsonify({"ok": True, "info": info})


# ---- 逐条决定 -----------------------------------------------------------

@app.route("/api/locators/<int:lid>/decision", methods=["POST"])
def decision(lid):
    """{action: confirm|reject|rebind|reset, start?, end?}"""
    data = request.get_json(force=True)
    action = data.get("action")
    conn = get_db()
    loc = conn.execute("SELECT * FROM locators WHERE id=?", (lid,)).fetchone()
    if not loc:
        return jsonify({"error": "not found"}), 404
    entry = conn.execute("SELECT * FROM entries WHERE id=?", (loc["entry_id"],)).fetchone()
    pid = entry["project_id"]

    old_vals = (loc["new_start"], loc["new_end"], loc["status"])

    def undo_sql():
        return (
            "UPDATE locators SET new_start=?, new_end=?, status=? WHERE id=?",
            [old_vals[0], old_vals[1], old_vals[2], lid],
        )

    label = f"{entry['term']}" + (f" — {entry['subterm']}" if entry["subterm"] else "")

    if action == "confirm":
        row = conn.execute(
            "SELECT new_start, new_end FROM candidates WHERE locator_id=? AND rank=1",
            (lid,)).fetchone()
        if not row:
            return jsonify({"error": "没有可用候选"}), 400
        conn.execute(
            "UPDATE locators SET new_start=?, new_end=?, status='confirmed' WHERE id=?",
            (row["new_start"], row["new_end"], lid))
        log_action(conn, pid, "confirm",
                   {"label": label, "locator": lid,
                    "new": [row["new_start"], row["new_end"]],
                    "old": [loc["old_start"], loc["old_end"]]},
                   *undo_sql())
    elif action == "reject":
        conn.execute(
            "UPDATE locators SET status='rejected', new_start=NULL, new_end=NULL WHERE id=?",
            (lid,))
        log_action(conn, pid, "reject",
                   {"label": label, "locator": lid,
                    "old": [loc["old_start"], loc["old_end"]]},
                   *undo_sql())
    elif action == "rebind":
        start, end = int(data["start"]), int(data.get("end") or data["start"])
        conn.execute(
            "UPDATE locators SET new_start=?, new_end=?, status='confirmed' WHERE id=?",
            (start, end, lid))
        log_action(conn, pid, "rebind",
                   {"label": label, "locator": lid, "new": [start, end],
                    "old": [loc["old_start"], loc["old_end"]]},
                   *undo_sql())
    elif action == "reset":
        conn.execute(
            "UPDATE locators SET status='pending', new_start=NULL, new_end=NULL WHERE id=?",
            (lid,))
        log_action(conn, pid, "reset",
                   {"label": label, "locator": lid,
                    "old": [loc["old_start"], loc["old_end"]]},
                   *undo_sql())
    else:
        return jsonify({"error": "unknown action"}), 400

    conn.commit()
    _refresh(conn, pid, rematch_entry=entry["id"])
    return jsonify({"ok": True})


@app.route("/api/locators/<int:lid>/candidates")
def candidates(lid):
    conn = get_db()
    return jsonify(all_candidates(conn, lid))


# ---- 批量接受高置信 -----------------------------------------------------

@app.route("/api/projects/<int:pid>/batch-accept", methods=["POST"])
def batch_accept(pid):
    data = request.get_json(silent=True) or {}
    threshold = float(data.get("threshold") or 0.85)
    conn = get_db()
    rows = conn.execute(
        """SELECT l.id, l.entry_id, c.new_start, c.new_end, c.reasons
           FROM locators l
           JOIN candidates c ON c.locator_id=l.id AND c.rank=1
           JOIN entries e ON e.id=l.entry_id
           WHERE e.project_id=? AND l.status='pending' AND c.score>=?""",
        (pid, threshold)).fetchall()
    accepted, skipped = [], []
    blocking = {"tie", "term_absent", "low_coverage", "outlier"}
    undo_statements = []
    for r in rows:
        blob = json.loads(r["reasons"] or "{}")
        codes = {x[0] for x in blob.get("reasons", [])}
        # 拆分/合并/跨章/全书偏移不统一属于提示信息；条目级歧义信号存在时不自动接受
        if codes & blocking:
            skipped.append({"locator": r["id"], "reasons": blob["reasons"]})
            continue
        prev = conn.execute(
            "SELECT new_start, new_end, status FROM locators WHERE id=?", (r["id"],)).fetchone()
        undo_statements.append((
            "UPDATE locators SET new_start=?, new_end=?, status=? WHERE id=?",
            [prev["new_start"], prev["new_end"], prev["status"], r["id"]]))
        conn.execute(
            "UPDATE locators SET new_start=?, new_end=?, status='confirmed' WHERE id=?",
            (r["new_start"], r["new_end"], r["id"]))
        accepted.append(r["id"])
    if accepted:
        log_action(conn, pid, "batch_accept",
                   {"accepted": accepted, "skipped": skipped, "threshold": threshold},
                   undo_statements)
    conn.commit()
    validate(conn, pid)
    return jsonify({"accepted": len(accepted), "skipped": skipped})


# ---- 页对照锚点 ---------------------------------------------------------

def _anchor_payload(conn, pid):
    ctx = build_ctx(conn, pid)
    return {
        "anchors": anchor_rows(conn, pid),
        "segments": anchor_segments(conn, pid, ctx) if ctx else [],
        "page_map": {str(k): v for k, v in
                     sorted((ctx["page_map"] if ctx else {}).items())},
        "conflicts": anchor_conflicts(conn, pid, ctx),
    }


@app.route("/api/projects/<int:pid>/anchors", methods=["GET"])
def list_anchors(pid):
    conn = get_db()
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
        return jsonify({"error": "project not found"}), 404
    return jsonify(_anchor_payload(conn, pid))


@app.route("/api/projects/<int:pid>/anchors", methods=["POST"])
def create_anchor(pid):
    """{old_page, new_page, note?, active?} —— 校验单调分段映射后写入。"""
    data = request.get_json(force=True) or {}
    try:
        old_page, new_page = int(data["old_page"]), int(data["new_page"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "请填写有效的旧版页与新版页（整数）"}), 400
    note = (data.get("note") or "").strip()
    status = "active" if data.get("active", True) else "disabled"
    conn = get_db()
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
        return jsonify({"error": "project not found"}), 404

    if status == "active":
        problem = validate_anchor(conn, pid, old_page, new_page)
        if problem:
            return jsonify({"error": problem, "code": "anchor_conflict"}), 409

    try:
        cur = conn.execute(
            "INSERT INTO page_anchors (project_id, old_page, new_page, status, note)"
            " VALUES (?,?,?,?,?)",
            (pid, old_page, new_page, status, note))
    except sqlite3.IntegrityError:
        return jsonify({"error": f"锚点 旧{old_page}→新{new_page} 已存在（重复占用）",
                        "code": "anchor_conflict"}), 409
    aid = cur.lastrowid
    log_action(conn, pid, "anchor_add",
               {"old_page": old_page, "new_page": new_page, "note": note,
                "status": status, "anchor": aid},
               "DELETE FROM page_anchors WHERE id=?", [aid])
    conn.commit()
    return jsonify({"ok": True, "id": aid, **_anchor_payload(conn, pid)})


@app.route("/api/anchors/<int:aid>/toggle", methods=["POST"])
def toggle_anchor(aid):
    """启用 / 停用锚点；启用时重新校验单调性。"""
    conn = get_db()
    row = conn.execute("SELECT * FROM page_anchors WHERE id=?", (aid,)).fetchone()
    if not row:
        return jsonify({"error": "锚点不存在"}), 404
    pid = row["project_id"]
    a = dict(row)
    if a["status"] == "active":
        new_status = "disabled"
    else:
        problem = validate_anchor(conn, pid, a["old_page"], a["new_page"], ignore_id=aid)
        if problem:
            return jsonify({"error": problem, "code": "anchor_conflict"}), 409
        new_status = "active"
    conn.execute("UPDATE page_anchors SET status=? WHERE id=?", (new_status, aid))
    log_action(conn, pid, "anchor_toggle",
               {"anchor": aid, "old_page": a["old_page"], "new_page": a["new_page"],
                "from": a["status"], "to": new_status},
               "UPDATE page_anchors SET status=? WHERE id=?", [a["status"], aid])
    conn.commit()
    return jsonify({"ok": True, "status": new_status, **_anchor_payload(conn, pid)})


@app.route("/api/anchors/<int:aid>/note", methods=["POST"])
def note_anchor(aid):
    data = request.get_json(force=True) or {}
    note = (data.get("note") or "").strip()
    conn = get_db()
    row = conn.execute("SELECT * FROM page_anchors WHERE id=?", (aid,)).fetchone()
    if not row:
        return jsonify({"error": "锚点不存在"}), 404
    pid, old_note = row["project_id"], row["note"]
    conn.execute("UPDATE page_anchors SET note=? WHERE id=?", (note, aid))
    log_action(conn, pid, "anchor_note",
               {"anchor": aid, "from": old_note, "to": note},
               "UPDATE page_anchors SET note=? WHERE id=?", [old_note, aid])
    conn.commit()
    return jsonify({"ok": True, **_anchor_payload(conn, pid)})


@app.route("/api/anchors/<int:aid>", methods=["DELETE"])
def delete_anchor(aid):
    conn = get_db()
    row = conn.execute("SELECT * FROM page_anchors WHERE id=?", (aid,)).fetchone()
    if not row:
        return jsonify({"error": "锚点不存在"}), 404
    pid = dict(row)["project_id"]
    conn.execute("DELETE FROM page_anchors WHERE id=?", (aid,))
    log_action(conn, pid, "anchor_delete",
               {"anchor": aid, "old_page": row["old_page"], "new_page": row["new_page"]},
               "INSERT INTO page_anchors (id, project_id, old_page, new_page, status, note)"
               " VALUES (?,?,?,?,?,?)",
               [aid, pid, row["old_page"], row["new_page"], row["status"], row["note"]])
    conn.commit()
    return jsonify({"ok": True, **_anchor_payload(conn, pid)})


# ---- 锚点重匹配（预览 + 执行） -------------------------------------------

def _snapshot_to_memory(conn):
    """把当前库备份到内存连接，供重匹配 dry-run 使用（不触碰正式数据）。"""
    mem = sqlite3.connect(":memory:")
    conn.backup(mem)
    mem.row_factory = sqlite3.Row
    mem.execute("PRAGMA foreign_keys = ON")
    return mem


def _rematch_changes(conn, pid):
    """在内存副本上按锚点重算全部待确认定位号，返回新旧头名候选对照。"""
    ch_new = detect_chapter_starts(conn, pid, "new")
    threshold = conn.execute(
        "SELECT batch_threshold FROM projects WHERE id=?", (pid,)).fetchone()[0]
    before = {r["id"]: dict(r) for r in conn.execute(
        """SELECT c.locator_id AS id, c.new_start, c.new_end, c.method, c.score
             FROM candidates c JOIN locators l ON l.id=c.locator_id
             JOIN entries e ON e.id=l.entry_id
            WHERE e.project_id=? AND c.rank=1 AND l.status='pending'""", (pid,))}

    mem = _snapshot_to_memory(conn)
    try:
        run_matching(mem, pid, chapter_starts_new=ch_new, threshold=threshold)
        after = {r["id"]: dict(r) for r in mem.execute(
            """SELECT c.locator_id AS id, c.new_start, c.new_end, c.method, c.score
                 FROM candidates c JOIN locators l ON l.id=c.locator_id
                 JOIN entries e ON e.id=l.entry_id
                WHERE e.project_id=? AND c.rank=1 AND l.status='pending'""", (pid,))}
    finally:
        mem.close()

    labels = {r["id"]: (r["term"], r["subterm"], r["old_start"], r["old_end"])
              for r in conn.execute(
        """SELECT l.id, e.term, e.subterm, l.old_start, l.old_end
             FROM locators l JOIN entries e ON e.id=l.entry_id
            WHERE e.project_id=? AND l.status='pending'""", (pid,))}

    changes, added, dropped = [], [], []
    for lid in sorted(set(before) | set(after)):
        b, a = before.get(lid), after.get(lid)
        term, sub, os_, oe = labels.get(lid, ("?", None, None, None))
        label = term + (f" — {sub}" if sub else "")
        if b and a and (b["new_start"], b["new_end"]) != (a["new_start"], a["new_end"]):
            changes.append({
                "locator_id": lid, "label": label,
                "old_locator": [os_, oe or os_],
                "before": [b["new_start"], b["new_end"]],
                "after": [a["new_start"], a["new_end"]],
                "before_method": b["method"], "after_method": a["method"],
                "before_score": b["score"], "after_score": a["score"],
            })
        elif b and not a:
            dropped.append({"locator_id": lid, "label": label})
        elif a and not b:
            added.append({"locator_id": lid, "label": label,
                          "after": [a["new_start"], a["new_end"]]})
    return {
        "pending_total": len(labels),
        "affected": len(changes) + len(added) + len(dropped),
        "changes": changes, "added": added, "dropped": dropped,
    }


@app.route("/api/projects/<int:pid>/rematch/preview", methods=["POST"])
def rematch_preview(pid):
    conn = get_db()
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
        return jsonify({"error": "project not found"}), 404
    ctx = build_ctx(conn, pid)
    if ctx is None:
        return jsonify({"error": "还没有启用的锚点，无法按锚点重匹配"}), 400
    return jsonify({"ok": True, "anchor_count": len(ctx["anchors"]),
                    **_rematch_changes(conn, pid)})


@app.route("/api/projects/<int:pid>/rematch", methods=["POST"])
def rematch_apply(pid):
    """固定锚点页，重算所有尚未确认定位号的候选；已确认/拒绝不动。"""
    conn = get_db()
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
        return jsonify({"error": "project not found"}), 404
    ctx = build_ctx(conn, pid)
    if ctx is None:
        return jsonify({"error": "还没有启用的锚点，无法按锚点重匹配"}), 400
    summary = _rematch_changes(conn, pid)
    ch_new = detect_chapter_starts(conn, pid, "new")
    threshold = conn.execute(
        "SELECT batch_threshold FROM projects WHERE id=?", (pid,)).fetchone()[0]
    info = run_matching(conn, pid, chapter_starts_new=ch_new, threshold=threshold,
                        anchor_ctx=ctx)
    conn.execute(
        "INSERT INTO meta (project_id,key,value) VALUES (?,?,?) "
        "ON CONFLICT(project_id,key) DO UPDATE SET value=excluded.value",
        (pid, "last_match", json.dumps(info, ensure_ascii=False)))
    validate(conn, pid)
    return jsonify({"ok": True, "info": info, **summary})


# ---- 撤销 / 快照 --------------------------------------------------------

@app.route("/api/projects/<int:pid>/undo", methods=["POST"])
def undo(pid):
    conn = get_db()
    detail = undo_last(conn, pid)
    if detail is None:
        return jsonify({"ok": False, "message": "没有可撤销的操作"})
    conn.commit()
    validate(conn, pid)
    return jsonify({"ok": True, "undone": detail})


def _take_snapshot(conn, pid):
    """序列化条目/定位号/候选/页对照锚点（撤销日志与问题是派生数据，不入快照）。"""
    entries = [dict(r) for r in conn.execute(
        "SELECT * FROM entries WHERE project_id=? ORDER BY id", (pid,))]
    locs = [dict(r) for r in conn.execute(
        "SELECT * FROM locators l JOIN entries e ON e.id=l.entry_id "
        "WHERE e.project_id=? ORDER BY l.id", (pid,))]
    cands = [dict(r) for r in conn.execute(
        "SELECT c.* FROM candidates c JOIN locators l ON l.id=c.locator_id "
        "JOIN entries e ON e.id=l.entry_id WHERE e.project_id=? ORDER BY c.id", (pid,))]
    anchors = [dict(r) for r in conn.execute(
        "SELECT * FROM page_anchors WHERE project_id=? ORDER BY id", (pid,))]
    return json.dumps({"entries": entries, "locators": locs, "candidates": cands,
                       "anchors": anchors},
                      ensure_ascii=False)


@app.route("/api/projects/<int:pid>/snapshots", methods=["GET"])
def list_snapshots(pid):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, ts FROM snapshots WHERE project_id=? ORDER BY id DESC", (pid,))
    return jsonify([dict(r) for r in rows])


@app.route("/api/projects/<int:pid>/snapshots", methods=["POST"])
def create_snapshot(pid):
    name = (request.get_json(force=True) or {}).get("name") or "命名版本"
    conn = get_db()
    payload = _take_snapshot(conn, pid)
    cur = conn.execute(
        "INSERT INTO snapshots (project_id, name, payload) VALUES (?,?,?)",
        (pid, name, payload))
    conn.commit()
    return jsonify({"id": cur.lastrowid})


@app.route("/api/projects/<int:pid>/snapshots/<int:sid>/restore", methods=["POST"])
def restore_snapshot(pid, sid):
    conn = get_db()
    snap = conn.execute("SELECT * FROM snapshots WHERE id=? AND project_id=?",
                        (sid, pid)).fetchone()
    if not snap:
        return jsonify({"error": "not found"}), 404
    # 恢复前自动留存当前状态；撤销 = 删除恢复后数据并重新写回留存状态
    current = json.loads(_take_snapshot(conn, pid))

    undo_statements = [
        ("DELETE FROM entries WHERE project_id=?", [pid]),
    ]
    undo_statements += [(
        "INSERT INTO entries (id, project_id, term, subterm, kind, ref_target,"
        " parent_id, sort_order) VALUES (?,?,?,?,?,?,?,?)",
        [e["id"], pid, e["term"], e["subterm"], e["kind"], e["ref_target"],
         e["parent_id"], e["sort_order"]]) for e in current["entries"]]
    undo_statements += [(
        "INSERT INTO locators (id, entry_id, old_start, old_end, new_start, new_end,"
        " status, anchor) VALUES (?,?,?,?,?,?,?,?)",
        [l["id"], l["entry_id"], l["old_start"], l["old_end"], l["new_start"],
         l["new_end"], l["status"], l["anchor"]]) for l in current["locators"]]
    undo_statements += [(
        "INSERT INTO candidates (id, locator_id, rank, new_start, new_end, method,"
        " score, reasons) VALUES (?,?,?,?,?,?,?,?)",
        [c["id"], c["locator_id"], c["rank"], c["new_start"], c["new_end"],
         c["method"], c["score"], c["reasons"]]) for c in current["candidates"]]

    data = json.loads(snap["payload"])
    current_anchors = current.get("anchors", [])
    undo_statements += [("DELETE FROM page_anchors WHERE project_id=?", [pid])]
    undo_statements += [(
        "INSERT INTO page_anchors (id, project_id, old_page, new_page, status, note,"
        " created_at) VALUES (?,?,?,?,?,?,?)",
        [a["id"], pid, a["old_page"], a["new_page"], a["status"], a["note"],
         a["created_at"]]) for a in current_anchors]

    conn.execute("DELETE FROM entries WHERE project_id=?", (pid,))
    conn.execute("DELETE FROM page_anchors WHERE project_id=?", (pid,))
    for e in data["entries"]:
        conn.execute(
            "INSERT INTO entries (id, project_id, term, subterm, kind, ref_target,"
            " parent_id, sort_order) VALUES (?,?,?,?,?,?,?,?)",
            (e["id"], pid, e["term"], e["subterm"], e["kind"], e["ref_target"],
             e["parent_id"], e["sort_order"]))
    conn.executemany(
        "INSERT INTO locators (id, entry_id, old_start, old_end, new_start, new_end,"
        " status, anchor) VALUES (?,?,?,?,?,?,?,?)",
        [(l["id"], l["entry_id"], l["old_start"], l["old_end"], l["new_start"],
          l["new_end"], l["status"], l["anchor"]) for l in data["locators"]])
    conn.executemany(
        "INSERT INTO candidates (id, locator_id, rank, new_start, new_end, method,"
        " score, reasons) VALUES (?,?,?,?,?,?,?,?)",
        [(c["id"], c["locator_id"], c["rank"], c["new_start"], c["new_end"],
          c["method"], c["score"], c["reasons"]) for c in data["candidates"]])
    conn.executemany(
        "INSERT INTO page_anchors (id, project_id, old_page, new_page, status, note,"
        " created_at) VALUES (?,?,?,?,?,?,?)",
        [(a["id"], pid, a["old_page"], a["new_page"], a["status"], a["note"],
          a["created_at"]) for a in data.get("anchors", [])])
    log_action(conn, pid, "restore", {"snapshot": snap["name"]}, undo_statements)
    conn.commit()
    validate(conn, pid)
    return jsonify({"ok": True})


# ---- 页面内容 / 设置 / 导出 ---------------------------------------------

@app.route("/api/pages/<int:pid>/<edition>/<int:page_no>")
def page_content(pid, edition, page_no):
    conn = get_db()
    row = conn.execute(
        "SELECT page_no, label, content FROM pages WHERE project_id=? AND edition=? AND page_no=?",
        (pid, edition, page_no)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


@app.route("/api/projects/<int:pid>/settings", methods=["POST"])
def update_settings(pid):
    data = request.get_json(force=True)
    conn = get_db()
    fields, params = [], []
    for key in ("chapter_policy", "cross_chapter", "heading_regex", "batch_threshold"):
        if key in data:
            val = data[key]
            if key == "cross_chapter":
                val = 1 if val else 0
            fields.append(f"{key}=?")
            params.append(val)
    if fields:
        conn.execute(f"UPDATE projects SET {', '.join(fields)} WHERE id=?", (*params, pid))
        conn.commit()
    validate(conn, pid)
    return jsonify({"ok": True})


@app.route("/api/projects/<int:pid>/export/<kind>")
def export(pid, kind):
    conn = get_db()
    name_row = conn.execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone()
    base = (name_row["name"] if name_row else "index").replace(" ", "_")
    if kind == "csv":
        body, mime, fname = export_csv(conn, pid), "text/csv; charset=utf-8", base + "_index.csv"
    elif kind == "proof":
        body, mime, fname = export_proof_html(conn, pid), "text/html; charset=utf-8", base + "_proof.html"
    elif kind == "issues":
        body, mime, fname = export_issues(conn, pid), "text/plain; charset=utf-8", base + "_issues.txt"
    elif kind == "anchors":
        body, mime, fname = export_anchors_json(conn, pid), "application/json; charset=utf-8", \
            base + "_anchors.json"
    else:
        return jsonify({"error": "unknown export"}), 400
    resp = Response(body, mimetype=mime)
    resp.headers["Content-Disposition"] = f'attachment; filename="{fname}"'
    return resp


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
