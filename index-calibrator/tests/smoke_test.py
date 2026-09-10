# -*- coding: utf-8 -*-
"""不依赖 Flask 的端到端核心逻辑冒烟测试（用临时数据库）。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 让 db 使用临时数据库
import core.db as dbmod
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
dbmod.DB_PATH = __import__("pathlib").Path(_tmp.name)
dbmod.init_db()

from core.db import get_db
from core.sample_data import load_sample, OLD_TEXT, NEW_TEXT, SAMPLE_CSV
from core.importer import parse_paged_text, parse_index_csv
from core.matcher import run_matching
from core.validator import validate, detect_chapter_starts
from core.state import build_state
from core.exporter import export_csv, export_proof_html, export_issues

conn = get_db()
pid = load_sample(conn)

def check(name, cond, extra=""):
    print(("  ok  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra else ""))
    assert cond, name

# --- 解析 ---
op = parse_paged_text(OLD_TEXT); np_ = parse_paged_text(NEW_TEXT)
check("旧版解析 10 页", len(op) == 10, str(len(op)))
check("新版解析 12 页", len(np_) == 12, str(len(np_)))

rows = parse_index_csv(SAMPLE_CSV)
check("CSV 解析 20 行", len(rows) == 20, str(len(rows)))
smoker = [r for r in rows if r["term"] == "Smoker"][0]
check("倒置范围被解析为 10-9", smoker["ranges"][0][:2] == (10, 9))
xrefs = [r for r in rows if r["kind"] in ("see", "seealso")]
check("交叉引用 4 条", len(xrefs) == 4, str(len(xrefs)))

# --- 匹配 ---
ch_new = detect_chapter_starts(conn, pid, "new")
check("新版章节首页 [2,8,10]", ch_new == [2, 8, 10], str(ch_new))
ch_old = detect_chapter_starts(conn, pid, "old")
check("旧版章节首页 [1,6,8]", ch_old == [1, 6, 8], str(ch_old))

info = run_matching(conn, pid, chapter_starts_new=ch_new, threshold=0.85)
print("  ... 全局偏移:", info["global_offset"], "占比:", info["offset_ratio"])
check("主偏移为 +2", info["global_offset"] == 2, str(info["global_offset"]))
check("偏移占比 < 0.75（偏移不统一）", info["offset_ratio"] < 0.75, str(info["offset_ratio"]))
validate(conn, pid)

st = build_state(conn, pid)
by = {}
for e in st["entries"]:
    for l in e["locators"]:
        by[(e["term"], e["subterm"], l["old_start"], l["old_end"])] = (l, e)

def cand(term, sub=None, s=None, e=None):
    for (t, sub2, os_, oe), (l, en) in by.items():
        if t == term and sub2 == sub and (s is None or os_ == s) and (e is None or oe == e):
            return l, en
    return None, None

# 期望映射
expects = [
    ("Honey Bees", None, 2), ("Lifespan", None, 4),
    ("Waggle dance", None, 5), ("Queen", None, 6),
    ("Piping", None, 7), ("Swarming", None, 8),
    ("Honeycomb", None, 9), ("Extraction", None, 10),
    ("Winter stores", None, 12), ("Wax", None, 9),
]
for term, sub, ns in expects:
    l, _ = cand(term, sub)
    c = l and l.get("candidate")
    check(f"{term} 候选起点 {ns}", c and c["new_start"] == ns,
          f"got {c and (c['new_start'], c['new_end'], c['score'], c['confidence'])}")

l, _ = cand("Anatomy")
c = l["candidate"]
check("Anatomy 拆分为 3-4", (c["new_start"], c["new_end"]) == (3, 4), str((c["new_start"], c["new_end"])))
check("Anatomy 有拆分原因", any(r[0] == "split" for r in c["reasons"]))

l, _ = cand("Venom")
c = l["candidate"]
check("Venom 条目词缺失原因", any(r[0] == "term_absent" for r in c["reasons"]),
      str(c["reasons"]))

l, _ = cand("Honey", "varietals", 9, 10)
c = l["candidate"]
check("Honey/varietals 9-10 候选在 11 附近", c and c["new_start"] == 11,
      str(c and (c["new_start"], c["new_end"], c["confidence"], c["score"])))

# --- 问题 ---
codes = {i["code"] for i in st["issues"]}
for need in ["inverted_range", "duplicate_see", "missing_target", "subterm_contradiction"]:
    check(f"问题存在: {need}", need in codes, str(sorted(codes)))

# 高置信数量
hi = [l for l, _ in by.values() if l.get("candidate") and l["candidate"]["confidence"] == "high"]
print(f"  ... 高置信 {len(hi)} / 中 {st['stats']['medium']} / 低 {st['stats']['low']}")
check("至少 4 个高置信（可演示批量接受）", len(hi) >= 4, str(len(hi)))

# --- 决定 + 撤销 ---
from core.utils import log_action, undo_last
l_hb, e_hb = cand("Honey Bees")
conn.execute("UPDATE locators SET new_start=?, new_end=?, status='confirmed' WHERE id=?",
             (l_hb["candidate"]["new_start"], l_hb["candidate"]["new_end"], l_hb["id"]))
log_action(conn, pid, "confirm", {"label": "Honey Bees"},
           "UPDATE locators SET new_start=NULL,new_end=NULL,status='pending' WHERE id=?",
           [l_hb["id"]])
conn.commit()
validate(conn, pid)
after = conn.execute("SELECT status FROM locators WHERE id=?", (l_hb["id"],)).fetchone()["status"]
check("确认后状态 confirmed", after == "confirmed", after)
undo_last(conn, pid)
conn.commit()
validate(conn, pid)
after2 = conn.execute("SELECT status FROM locators WHERE id=?", (l_hb["id"],)).fetchone()["status"]
check("撤销后回到 pending", after2 == "pending", after2)

# --- 导出 ---
csv_text = export_csv(conn, pid)
check("CSV 导出含表头", csv_text.startswith("term,subterm"))
proof = export_proof_html(conn, pid)
check("校样 HTML 生成", "索引校样" in proof and "Honey Bees" in proof)
issues_txt = export_issues(conn, pid)
check("问题报告生成", "未决问题报告" in issues_txt)

print("\n全部冒烟测试通过 ✔")
