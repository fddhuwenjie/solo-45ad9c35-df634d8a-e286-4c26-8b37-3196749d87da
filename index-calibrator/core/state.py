# -*- coding: utf-8 -*-
"""把数据库状态组装成前端需要的 JSON。"""
import json

from .validator import detect_chapter_starts


def get_meta(conn, project_id):
    return {
        r["key"]: json.loads(r["value"])
        for r in conn.execute("SELECT key, value FROM meta WHERE project_id=?", (project_id,))
    }


def build_state(conn, project_id):
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not proj:
        return None
    old_pages = conn.execute(
        "SELECT page_no, label FROM pages WHERE project_id=? AND edition='old' ORDER BY page_no",
        (project_id,)).fetchall()
    new_pages = conn.execute(
        "SELECT page_no, label FROM pages WHERE project_id=? AND edition='new' ORDER BY page_no",
        (project_id,)).fetchall()

    entries_raw = conn.execute(
        "SELECT * FROM entries WHERE project_id=? ORDER BY sort_order, id", (project_id,)).fetchall()
    loc_rows = conn.execute(
        """SELECT l.*, c.rank, c.new_start AS c_start, c.new_end AS c_end,
                  c.method, c.score, c.reasons
           FROM locators l
           LEFT JOIN candidates c ON c.locator_id=l.id AND c.rank=1
           JOIN entries e ON e.id=l.entry_id
           WHERE e.project_id=? ORDER BY l.id""", (project_id,)).fetchall()

    locs_by_entry = {}
    stats = {"total": 0, "pending": 0, "confirmed": 0, "rejected": 0,
             "high": 0, "medium": 0, "low": 0, "unmatched": 0}
    for l in loc_rows:
        loc = {
            "id": l["id"], "old_start": l["old_start"], "old_end": l["old_end"],
            "new_start": l["new_start"], "new_end": l["new_end"],
            "status": l["status"], "anchor": l["anchor"],
        }
        if l["rank"]:
            blob = json.loads(l["reasons"] or "{}")
            loc["candidate"] = {
                "new_start": l["c_start"], "new_end": l["c_end"],
                "method": l["method"], "score": l["score"],
                "confidence": blob.get("confidence"),
                "reasons": blob.get("reasons", []),
            }
            stats[blob.get("confidence", "low")] += 1
        else:
            stats["unmatched"] += 1
        locs_by_entry.setdefault(l["entry_id"], []).append(loc)
        stats["total"] += 1
        stats[l["status"]] += 1

    entries = []
    for e in entries_raw:
        entry = {
            "id": e["id"], "term": e["term"], "subterm": e["subterm"],
            "kind": e["kind"], "target": e["ref_target"], "parent_id": e["parent_id"],
            "locators": locs_by_entry.get(e["id"], []),
        }
        entries.append(entry)

    issues = conn.execute(
        """SELECT i.* FROM issues i WHERE i.project_id=? AND i.status='open'
           ORDER BY CASE i.severity WHEN 'error' THEN 0 ELSE 1 END, i.id""",
        (project_id,)).fetchall()
    history = conn.execute(
        "SELECT id, ts, kind, detail FROM action_log WHERE project_id=? ORDER BY id DESC LIMIT 30",
        (project_id,)).fetchall()
    snapshots = conn.execute(
        "SELECT id, name, ts FROM snapshots WHERE project_id=? ORDER BY id DESC",
        (project_id,)).fetchall()

    return {
        "project": {
            "id": proj["id"], "name": proj["name"],
            "chapter_policy": proj["chapter_policy"],
            "cross_chapter": bool(proj["cross_chapter"]),
            "heading_regex": proj["heading_regex"],
            "batch_threshold": proj["batch_threshold"],
        },
        "pages": {
            "old": [{"page_no": p["page_no"], "label": p["label"]} for p in old_pages],
            "new": [{"page_no": p["page_no"], "label": p["label"]} for p in new_pages],
        },
        "chapters": {
            "old": detect_chapter_starts(conn, project_id, "old"),
            "new": detect_chapter_starts(conn, project_id, "new"),
        },
        "entries": entries,
        "issues": [{"id": i["id"], "code": i["code"], "severity": i["severity"],
                    "entry_id": i["entry_id"], "locator_id": i["locator_id"],
                    "message": i["message"]} for i in issues],
        "history": [{"id": h["id"], "ts": h["ts"], "kind": h["kind"],
                     "detail": json.loads(h["detail"])} for h in history],
        "snapshots": [{"id": s["id"], "name": s["name"], "ts": s["ts"]} for s in snapshots],
        "match_info": get_meta(conn, project_id),
        "stats": stats,
    }


def candidate_blob(conn, locator_id, rank=1):
    row = conn.execute(
        "SELECT reasons FROM candidates WHERE locator_id=? AND rank=?",
        (locator_id, rank)).fetchone()
    return json.loads(row["reasons"]) if row and row["reasons"] else {}


def all_candidates(conn, locator_id):
    rows = conn.execute(
        "SELECT rank, new_start, new_end, method, score, reasons FROM candidates "
        "WHERE locator_id=? ORDER BY rank", (locator_id,)).fetchall()
    out = []
    for r in rows:
        blob = json.loads(r["reasons"] or "{}")
        out.append({
            "rank": r["rank"], "new_start": r["new_start"], "new_end": r["new_end"],
            "method": r["method"], "score": r["score"],
            "confidence": blob.get("confidence"),
            "reasons": blob.get("reasons", []),
            "highlights": blob.get("highlights", {}),
        })
    return out
