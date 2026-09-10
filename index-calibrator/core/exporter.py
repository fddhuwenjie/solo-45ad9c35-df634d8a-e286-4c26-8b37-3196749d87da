# -*- coding: utf-8 -*-
"""三种成果物导出：更新后的索引 CSV、可打印校样 HTML、未决问题报告。"""
import csv
import io
import json


def _format_range(start, end):
    if start is None:
        return ""
    return str(start) if end in (None, start) else f"{start}-{end}"


def _entry_rows(conn, project_id):
    return conn.execute(
        """SELECT e.*,
             (SELECT group_concat(
                (SELECT CASE WHEN l.new_start IS NOT NULL
                        THEN (l.new_start || COALESCE('-' || NULLIF(l.new_end,l.new_start),''))
                        ELSE ('~' || l.old_start || COALESCE('-' || NULLIF(l.old_end,l.old_start),''))
                    END), '; ')
              FROM locators l WHERE l.entry_id=e.id) AS loc_text,
             (SELECT group_concat(status||':'||id) FROM locators l WHERE l.entry_id=e.id) AS statuses
           FROM entries e WHERE e.project_id=? ORDER BY e.term,
             CASE WHEN e.subterm IS NULL THEN 0 ELSE 1 END, e.subterm""",
        (project_id,)).fetchall()


def export_csv(conn, project_id):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["term", "subterm", "kind", "target", "pages", "notes"])
    for e in _entry_rows(conn, project_id):
        locs = conn.execute(
            "SELECT * FROM locators WHERE entry_id=? ORDER BY old_start", (e["id"],)).fetchall()
        parts, notes = [], []
        for l in locs:
            if l["new_start"] is not None:
                parts.append(_format_range(l["new_start"], l["new_end"]))
            elif l["status"] == "rejected":
                notes.append(f"旧 {_format_range(l['old_start'], l['old_end'])} 已拒绝")
            else:
                parts.append("~" + _format_range(l["old_start"], l["old_end"]))
                notes.append(f"旧 {_format_range(l['old_start'], l['old_end'])} 待确认")
        if e["kind"] in ("see", "seealso") and e["ref_target"]:
            prefix = "see also" if e["kind"] == "seealso" else "see"
            parts.append(f"{prefix}: {e['ref_target']}")
        w.writerow([e["term"], e["subterm"] or "", e["kind"], e["ref_target"] or "",
                    "; ".join(p for p in parts if p), "; ".join(notes)])
    return buf.getvalue()


def export_proof_html(conn, project_id):
    proj = conn.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone()
    rows = _entry_rows(conn, project_id)
    out = io.StringIO()
    out.write(f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>索引校样 — {proj['name']}</title>
<style>
@page {{ margin: 22mm 18mm; }}
body {{ font-family: Georgia, 'Songti SC', serif; color:#111; line-height:1.5; }}
h1 {{ font-size: 20pt; border-bottom:2px solid #111; padding-bottom:6pt; }}
.letter {{ margin-top:14pt; font-weight:bold; font-size:13pt; }}
.term {{ margin: 4pt 0 2pt 0; }}
.term b {{ font-size:11pt; }}
.sub {{ margin-left: 20pt; font-size:10pt; }}
.pages {{ font-variant-numeric: tabular-nums; }}
.unresolved {{ color:#b00020; }}
.pending {{ color:#8a5a00; }}
.xref {{ color:#333; font-style:italic; }}
table.meta {{ font-size:9pt; border-collapse:collapse; margin-bottom:10pt; }}
table.meta td {{ border:1px solid #999; padding:2pt 8pt; }}
</style></head><body>
<h1>换版索引校样：{proj['name']}</h1>
""")
    current_letter = None
    for e in rows:
        locs = conn.execute(
            "SELECT * FROM locators WHERE entry_id=? ORDER BY old_start", (e["id"],)).fetchall()
        letter = e["term"][:1].upper()
        if letter != current_letter:
            current_letter = letter
            out.write(f'<div class="letter">{letter}</div>\n')
        cls = "term" if not e["subterm"] else "sub"
        out.write(f'<div class="{cls}"><b>{e["term"]}</b>')
        if e["subterm"]:
            out.write(f', {e["subterm"]}')
        page_bits = []
        for l in locs:
            if l["new_start"] is not None:
                page_bits.append(
                    f'<span class="pages">{_format_range(l["new_start"], l["new_end"])}</span>')
            elif l["status"] == "rejected":
                page_bits.append(
                    f'<span class="unresolved">[拒绝 旧{_format_range(l["old_start"], l["old_end"])}]</span>')
            else:
                page_bits.append(
                    f'<span class="pending">[待确认 旧{_format_range(l["old_start"], l["old_end"])}]</span>')
        if e["kind"] in ("see", "seealso") and e["ref_target"]:
            word = "参见" if e["kind"] == "seealso" else "见"
            page_bits.append(f'<span class="xref">{word} {e["ref_target"]}</span>')
        if page_bits:
            out.write(", " + ", ".join(page_bits))
        out.write("</div>\n")
    out.write("</body></html>")
    return out.get()


def export_issues(conn, project_id, fmt="txt"):
    rows = conn.execute(
        """SELECT i.*, e.term, e.subterm FROM issues i
           LEFT JOIN entries e ON e.id=i.entry_id
           WHERE i.project_id=? AND i.status='open'
           ORDER BY CASE i.severity WHEN 'error' THEN 0 ELSE 1 END, i.code""",
        (project_id,)).fetchall()
    if fmt == "json":
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2)
    lines = ["# 未决问题报告", f"共 {len(rows)} 个未决问题", ""]
    for r in rows:
        owner = r["term"] or "（全局）"
        if r["subterm"]:
            owner += " — " + r["subterm"]
        lines.append(f"[{r['severity'].upper():7}] {r['code']:24} {owner}")
        lines.append(f"    {r['message']}")
    return "\n".join(lines)
