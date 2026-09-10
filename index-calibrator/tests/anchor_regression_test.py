# -*- coding: utf-8 -*-
"""两处已复核正常使用缺陷的回归测试（不依赖 Flask）。

反例 1（合法压缩区间必须保留每个启用锚点）：
  旧 1→新 1、旧 10→新 5（斜率 4/9 < 1，多页折叠到同一新页）。
  旧 10 必须仍精确映射到新 5，不能被“逐页严格递增”兜底顶到新 10；
  page_map、segments、导出对照表、匹配候选都要与锚点一致。

反例 2（按锚点重匹配只重算相邻锚点之间的待确认定位号）：
  旧 3→新 4、旧 7→新 9 界定区间。区间外的待确认定位号（旧 1、旧 10 等）
  候选必须原样保留：不删除、不重建（candidate 行 id 与全部字段不变）。
"""
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.db as dbmod  # noqa: E402

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
dbmod.DB_PATH = pathlib.Path(_tmp.name)
dbmod.init_db()

from core.db import get_db  # noqa: E402
from core.anchors import (  # noqa: E402
    build_ctx, export_anchors_json, locator_bounds, segments, validate_anchor,
)
from core.matcher import run_matching  # noqa: E402
from core.sample_data import load_sample  # noqa: E402
from core.validator import detect_chapter_starts  # noqa: E402

conn = get_db()


def check(name, cond, extra=""):
    print(("  ok  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra else ""))
    assert cond, name


# ===========================================================================
# 反例 1：压缩区间 旧1→新1、旧10→新5
# ===========================================================================
print("反例 1：合法压缩区间")

pid_c = conn.execute("INSERT INTO projects (name) VALUES (?)",
                     ("压缩区间测试：旧10页 → 新6页",)).lastrowid

# 旧版 10 页、新版 6 页；每页给可区分的内容
for i in range(1, 11):
    conn.execute(
        "INSERT INTO pages (project_id, edition, page_no, label, content) VALUES (?,?,?,?,?)",
        (pid_c, "old", i, str(i),
         f"Old chapter topic {i} alfa bravo charlie delta echo subject{i}.\n"
         f"Paragraph about matter {i} with distinctive wording foxglove{i}."))
for i in range(1, 7):
    conn.execute(
        "INSERT INTO pages (project_id, edition, page_no, label, content) VALUES (?,?,?,?,?)",
        (pid_c, "new", i, str(i),
         f"New compressed edition page {i} gathered topics.\n"
         f"Content echo subject gathered on new page {i}, wording foxglove."))

# 旧 10 的单页定位号
eid = conn.execute(
    "INSERT INTO entries (project_id, term, subterm, kind) VALUES (?,?,?,?)",
    (pid_c, "Closing Topic", None, "term")).lastrowid
lid10 = conn.execute(
    "INSERT INTO locators (entry_id, old_start, old_end) VALUES (?,?,?)",
    (eid, 10, 10)).lastrowid

# 两个锚点通过单调性校验（旧页、新页都严格递增；新页范围 1..6）
check("旧1→新1 校验通过", validate_anchor(conn, pid_c, 1, 1) is None)
check("旧10→新5 校验通过（压缩，不倒退）", validate_anchor(conn, pid_c, 10, 5) is None)
conn.execute(
    "INSERT INTO page_anchors (project_id, old_page, new_page, status, note)"
    " VALUES (?,?,?,?,?)", (pid_c, 1, 1, "active", "压缩段起点"))
conn.execute(
    "INSERT INTO page_anchors (project_id, old_page, new_page, status, note)"
    " VALUES (?,?,?,?,?)", (pid_c, 10, 5, "active", "压缩段终点：第十页并入新第五页"))
conn.commit()

ctx = build_ctx(conn, pid_c)
pm = ctx["page_map"]

# 关键断言 1：锚点页精确，旧10 不被顶成新10
check("锚点精确：旧1→新1", pm[1] == 1, str(pm.get(1)))
check("锚点精确：旧10→新5（不被严格递增顶成新10）", pm[10] == 5,
      f"page_map={pm}")
# 中间页非递减（允许折叠到相同新页），且不超过末锚点新页 5
vals = [pm[o] for o in range(1, 11)]
check("投影非递减（压缩折叠合法）", all(b >= a for a, b in zip(vals, vals[1:])),
      str(vals))
check("中间页不超过末锚点新页 5", all(v <= 5 for v in vals), str(vals))
check("投影不超出新版全书 6 页", all(1 <= v <= 6 for v in vals), str(vals))
check("斜率 4/9", abs(ctx["slopes"][1] - 4 / 9) < 1e-9, str(ctx["slopes"]))

# segments：锚点区间 旧1-10 → 新1-5
segs = segments(conn, pid_c, ctx)
interval = next(s for s in segs if s["kind"] == "interval")
check("区间段终点为新5（不是新10）",
      interval["new_start"] == 1 and interval["new_end"] == 5,
      str(interval))
check("区间段斜率记录为压缩 <1", interval["slope"] is not None and interval["slope"] < 1,
      str(interval["slope"]))

# locator_bounds：旧10 单页被 pin 固定到新5
b = locator_bounds(ctx, 10, 10)
check("旧10 定位号固定 pin (10,5)", b["pins"] == [(10, 5)], str(b["pins"]))
check("旧10 候选区间夹到新5..5", b["seg_lo"] == 5 and b["seg_hi"] == 5,
      str((b["seg_lo"], b["seg_hi"])))
check("旧10 投影范围 [5,5]", b["projected"] == (5, 5), str(b["projected"]))

# matcher：旧10 候选必须固定到新5（而非跑到新10——新版根本只有6页）
run_matching(conn, pid_c, chapter_starts_new=[], threshold=0.85, anchor_ctx=ctx)
top = conn.execute(
    "SELECT new_start, new_end, method, reasons FROM candidates "
    "WHERE locator_id=? AND rank=1", (lid10,)).fetchone()
check("旧10 候选固定到新5（压缩锚点不被违反）",
      (top["new_start"], top["new_end"]) == (5, 5),
      str((top["new_start"], top["new_end"], top["method"])))
blob = json.loads(top["reasons"])
check("候选带锚点固定标记", blob.get("anchor_pinned") is True, str(blob)[:200])

# 导出对照表：page_map / anchors / locators 投影都与锚点一致
payload = json.loads(export_anchors_json(conn, pid_c))
check("导出 page_map 旧10→新5", payload["page_map"]["10"] == 5,
      str(payload["page_map"]))
check("导出 page_map 旧1→新1", payload["page_map"]["1"] == 1)
exp_seg = next(s for s in payload["segments"] if s["kind"] == "interval")
check("导出区间 新1..5", exp_seg["new_start"] == 1 and exp_seg["new_end"] == 5,
      str(exp_seg))
a10 = next(a for a in payload["anchors"] if a["old_page"] == 10)
check("导出锚点 旧10→新5 且为启用", a10["new_page"] == 5 and a10["status"] == "active",
      str(a10))
loc10 = next(l for l in payload["locators"]
             if l["term"] == "Closing Topic" and l["old_range"] == [10, 10])
check("导出定位号投影区间 [5,5]", loc10["projected_new_range"] == [5, 5],
      str(loc10))
check("导出定位号实际候选与投影同步", loc10["in_sync"] is True
      and loc10["actual_new_range"] == [5, 5], str(loc10))

# ===========================================================================
# 反例 2：按锚点重匹配只动相邻锚点区间内的待确认定位号
# ===========================================================================
print("反例 2：区间外候选原样保留")

pid = load_sample(conn)
ch_new = detect_chapter_starts(conn, pid, "new")

# 先在无锚状态建立全部候选基线
run_matching(conn, pid, chapter_starts_new=ch_new, threshold=0.85)


def all_rank1(connection):
    return {r["locator_id"]: dict(r) for r in connection.execute(
        """SELECT c.id AS cid, c.locator_id, c.new_start, c.new_end, c.method,
                  c.score, c.reasons, l.old_start, l.old_end, l.status,
                  e.term, e.subterm
             FROM candidates c JOIN locators l ON l.id=c.locator_id
             JOIN entries e ON e.id=l.entry_id
            WHERE e.project_id=? AND c.rank=1 AND l.status='pending'""",
        (pid,)).fetchall()}


baseline = all_rank1(conn)

# 锚点：旧3→新4、旧7→新9
check("旧3→新4 校验通过", validate_anchor(conn, pid, 3, 4) is None)
check("旧7→新9 校验通过", validate_anchor(conn, pid, 7, 9) is None)
conn.execute(
    "INSERT INTO page_anchors (project_id, old_page, new_page, status, note)"
    " VALUES (?,?,?,?,?)", (pid, 3, 4, "active", "区间界定一"))
conn.execute(
    "INSERT INTO page_anchors (project_id, old_page, new_page, status, note)"
    " VALUES (?,?,?,?,?)", (pid, 7, 9, "active", "区间界定二"))
conn.commit()
ctx2 = build_ctx(conn, pid)
check("两个界定锚点", [(a["old_page"], a["new_page"]) for a in ctx2["anchors"]] == [(3, 4), (7, 9)])

# 区间外：旧范围完全在旧3之前（COALESCE(old_end)<3）或旧起点在旧7之后（old_start>7）
# 横跨区间两侧的多页范围（如 Beekeeping 旧1-10）按 locator_bounds 允许全书范围，
# 不属于“严格区间内”，单列
outside = {lid: c for lid, c in baseline.items()
           if (c["old_end"] or c["old_start"]) < 3 or c["old_start"] > 7}
strict_inside = {lid: c for lid, c in baseline.items()
                 if c["old_start"] >= 3 and (c["old_end"] or c["old_start"]) <= 7}
inside = {lid: c for lid, c in baseline.items() if lid not in outside}
check("存在区间外待确认定位号（旧1/旧10 等）", len(outside) >= 2,
      str([(c["term"], c["old_start"], c["old_end"]) for c in outside.values()]))
check("存在严格区间内待确认定位号", len(strict_inside) >= 1,
      str([(c["term"], c["old_start"]) for c in strict_inside.values()]))

# 关键：anchor_scope=True 重匹配
run_matching(conn, pid, chapter_starts_new=ch_new, threshold=0.85,
             anchor_ctx=ctx2, anchor_scope=True)
after = all_rank1(conn)

preserved_bad = []
for lid, c0 in outside.items():
    c1 = after.get(lid)
    # 行 id 与全部字段必须与基线逐字节一致（证明既未删除也未重建）
    if c1 is None or any(c1[k] != c0[k] for k in
                         ("cid", "new_start", "new_end", "method", "score", "reasons")):
        preserved_bad.append((c0["term"], c0["old_start"],
                              None if c1 is None else (c1["cid"], c1["new_start"])))
check("区间外候选原样保留（id 与全部字段不变）", not preserved_bad,
      str(preserved_bad))

# 点名核对：旧1 Honey Bees、旧10 Winter stores
def term_cand(term, old_s):
    for lid, c in after.items():
        if c["term"] == term and c["old_start"] == old_s:
            return c
    return None


for term, old_s in [("Honey Bees", 1), ("Winter stores", 10)]:
    c0 = next(c for c in baseline.values() if c["term"] == term and c["old_start"] == old_s)
    c1 = term_cand(term, old_s)
    check(f"区间外 {term} 旧{old_s} 候选行未重建（cid {c0['cid']} 不变）",
          c1 is not None and c1["cid"] == c0["cid"]
          and (c1["new_start"], c1["new_end"]) == (c0["new_start"], c0["new_end"]),
          str(None if c1 is None else (c1["cid"], c1["new_start"], c1["new_end"])))

# 区间内至少有定位号被实际重算（DELETE+INSERT 使 candidate 行 id 增大）
recomputed = [c1["term"] for lid, c1 in after.items()
              if lid in inside and baseline[lid]["cid"] != c1["cid"]]
check("区间内存在被重算的定位号", len(recomputed) >= 1, str(recomputed))

# 已确认/拒绝定位号始终不动（取一个区间内定位号确认后再重匹配）
ls = next(c for c in after.values() if c["term"] == "Lifespan")  # 旧3，区间内
conn.execute("UPDATE locators SET status='confirmed', new_start=?, new_end=? WHERE id=?",
             (4, 4, ls["locator_id"]))
conn.commit()
run_matching(conn, pid, chapter_starts_new=ch_new, threshold=0.85,
             anchor_ctx=ctx2, anchor_scope=True)
row = conn.execute(
    "SELECT status, new_start FROM locators WHERE id=?", (ls["locator_id"],)).fetchone()
check("区间内已确认定位号不被重匹配改动",
      row["status"] == "confirmed" and row["new_start"] == 4,
      str((row["status"], row["new_start"])))

# 区间内候选不得越出相邻锚点所夹新区间：旧范围整体在 旧3..7 的，候选块应在 新4..9
violators = []
for lid, c in after.items():
    if lid in strict_inside and (c["new_start"] < 4 or c["new_end"] > 9):
        violators.append((c["term"], c["old_start"], c["new_start"], c["new_end"]))
check("严格区间内候选不越出新锚点区间 新4..9", not violators, str(violators))

print("\n锚点两处缺陷回归测试全部通过 ✔")
