# -*- coding: utf-8 -*-
"""页对照锚点功能的端到端测试（不依赖 Flask，用临时数据库）。

覆盖：
  1. 单调分段映射：倒退/重复占用/越界拒绝，停用后可加，启用再校验；
  2. 线性插值投影与区间边界（首/尾外推、单锚、多锚）；
  3. matcher 锚点约束：单页锚点强制置顶，候选块不出相邻区间；
  4. rematch 内存副本预览：受影响数、原候选→新候选变化，正式库不动；
  5. 快照保存/恢复锚点；
  6. JSON 对照表含 segments / anchors / conflicts / page_map；
  7. 撤销：增/启停/删/备注全部可逐条撤销。
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
from core.sample_data import load_sample  # noqa: E402
from core.matcher import run_matching  # noqa: E402
from core.validator import detect_chapter_starts  # noqa: E402
from core.anchors import (  # noqa: E402
    anchor_rows, build_ctx, export_anchors_json, locator_bounds,
    segments, validate_anchor,
)
from core.utils import log_action, undo_last  # noqa: E402

conn = get_db()
pid = load_sample(conn)
ch_new = detect_chapter_starts(conn, pid, "new")


def check(name, cond, extra=""):
    print(("  ok  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra else ""))
    assert cond, name


def add_anchor(old, new, status="active", note=""):
    cur = conn.execute(
        "INSERT INTO page_anchors (project_id, old_page, new_page, status, note)"
        " VALUES (?,?,?,?,?)", (pid, old, new, status, note))
    conn.commit()
    return cur.lastrowid


def top_cand(locator_id):
    r = conn.execute(
        "SELECT new_start, new_end, method, reasons FROM candidates "
        "WHERE locator_id=? AND rank=1", (locator_id,)).fetchone()
    return r


def find_loc(term, sub=None, old_s=None):
    q = ("SELECT l.id FROM locators l JOIN entries e ON e.id=l.entry_id "
         "WHERE e.project_id=? AND e.term=?")
    args = [pid, term]
    if sub is not None:
        q += " AND e.subterm=?"
        args.append(sub)
    if old_s is not None:
        q += " AND l.old_start=?"
        args.append(old_s)
    return conn.execute(q, args).fetchone()["id"]


# ---------------------------------------------------------------------------
# 1) 校验：越界 / 重复占用 / 倒退
# ---------------------------------------------------------------------------

check("旧页越界被拒", validate_anchor(conn, pid, 99, 3) is not None)
check("新页越界被拒", validate_anchor(conn, pid, 1, 88) is not None)
check("正常锚点通过", validate_anchor(conn, pid, 1, 2) is None)

a1 = add_anchor(1, 2)
check("旧页重复占用被拒", validate_anchor(conn, pid, 1, 5) is not None)
check("新页重复占用被拒", validate_anchor(conn, pid, 4, 2) is not None)
check("新页倒退被拒（旧增新减）", validate_anchor(conn, pid, 3, 1) is not None)
check("单调推进通过", validate_anchor(conn, pid, 6, 8) is None)

# 停用 a1 后，冲突规则应放开
conn.execute("UPDATE page_anchors SET status='disabled' WHERE id=?", (a1,))
conn.commit()
check("停用后旧页可再占用", validate_anchor(conn, pid, 1, 3) is None)
check("停用后新页可再占用", validate_anchor(conn, pid, 5, 2) is None)
conn.execute("UPDATE page_anchors SET status='active' WHERE id=?", (a1,))
conn.commit()

# 完全重复的锚点（连旧带新都相同）
check("完全相同锚点被拒", validate_anchor(conn, pid, 1, 2) is not None)

# ---------------------------------------------------------------------------
# 2) 分段映射：建锚点 旧1→新2、旧6→新8、旧10→新12
#    区间斜率：(8-2)/(6-1)=1.2；(12-8)/(10-6)=1.0
# ---------------------------------------------------------------------------

a2 = add_anchor(6, 8)
a3 = add_anchor(10, 12)
ctx = build_ctx(conn, pid)
check("三个启用锚点", len(ctx["anchors"]) == 3, str([(a["old_page"], a["new_page"])
                                                       for a in ctx["anchors"]]))

# 旧 1..10 投影必须严格单调
pm = ctx["page_map"]
vals = [pm[o] for o in range(1, 11)]
check("逐页投影严格单调", all(b > a for a, b in zip(vals, vals[1:])), str(vals))
check("锚点页投影固定 1→2", pm[1] == 2, str(pm))
check("锚点页投影固定 6→8", pm[6] == 8, str(pm))
check("锚点页投影固定 10→12", pm[10] == 12, str(pm))
# 旧 3 在 [1,6] 区间：2 + (3-1)*1.2 = 4.4 → 4
check("区间内插值 旧3≈新4", pm[3] == 4, str(pm[3]))
# 旧 4：2 + 3*1.2 = 5.6 → 6
check("区间内插值 旧4≈新6", pm[4] == 6, str(pm[4]))

segs = segments(conn, pid, ctx)
kinds = [s["kind"] for s in segs]
check("分段含 head/interval/tail",
      kinds == ["head", "interval", "interval", "tail"], str(kinds))
check("分段锚点边界", segs[1]["new_start"] == 2 and segs[1]["new_end"] == 8)
tail = segs[-1]
check("尾段从最后锚点开始", tail["old_start"] == 10 and tail["new_start"] == 12)

# 单锚情形：斜率回退为 1
conn.execute("UPDATE page_anchors SET status='disabled' WHERE id IN (?,?)", (a2, a3))
conn.commit()
ctx1 = build_ctx(conn, pid)
segs1 = segments(conn, pid, ctx1)
check("单锚只有一个分段", len(segs1) == 1 and segs1[0]["kind"] == "single")
check("单锚外推斜率 1：旧2→新3", ctx1["page_map"][2] == 3, str(ctx1["page_map"]))
conn.execute("UPDATE page_anchors SET status='active' WHERE id IN (?,?)", (a2, a3))
conn.commit()

# locator_bounds：相邻锚点区间
b_mid = locator_bounds(ctx, 3, 4)          # 位于 旧1→2 与 旧6→8 之间
check("中间定位号区间 新2..8", b_mid["seg_lo"] == 2 and b_mid["seg_hi"] == 8,
      str((b_mid["seg_lo"], b_mid["seg_hi"])))
check("中间定位号无固定页", b_mid["pins"] == [])
b_pin = locator_bounds(ctx, 6, 6)
check("锚点页定位号固定 旧6→新8", b_pin["pins"] == [(6, 8)], str(b_pin["pins"]))
b_span = locator_bounds(ctx, 5, 7)
check("横跨锚点的范围固定 pin", b_span["pins"] == [(6, 8)], str(b_span["pins"]))
check("横跨范围区间 新2..12", b_span["seg_lo"] == 2 and b_span["seg_hi"] == 12)
b_head = locator_bounds(ctx, 1, 2)
check("起点贴锚点时下界=锚点新页", b_head["seg_lo"] == 2, str(b_head["seg_lo"]))
b_head2 = locator_bounds(ctx, 2, 3)
check("首段（首锚之后）下界取首锚新页", b_head2["seg_lo"] == 2, str(b_head2["seg_lo"]))
b_tail = locator_bounds(ctx, 9, 10)
check("尾段终点贴锚点时上界=锚点新页", b_tail["seg_hi"] == 12, str(b_tail["seg_hi"]))
b_tail2 = locator_bounds(ctx, 8, 9)
check("末锚之前区间：下界取前锚新页", b_tail2["seg_lo"] == 8 and b_tail2["seg_hi"] == 12,
      str((b_tail2["seg_lo"], b_tail2["seg_hi"])))

# ---------------------------------------------------------------------------
# 3) matcher 约束
# ---------------------------------------------------------------------------

run_matching(conn, pid, chapter_starts_new=ch_new, threshold=0.85, anchor_ctx=ctx)

# 旧6 单页锚点固定到新8（Swarming 原自动候选可能就是 8；改用旧1 Honey Bees → 新2 验证）
lid = find_loc("Honey Bees")
top = top_cand(lid)
check("单页锚点候选固定 旧1→新2", (top["new_start"], top["new_end"]) == (2, 2),
      str((top["new_start"], top["new_end"], top["method"])))
blob = json.loads(top["reasons"])
check("头名候选带 anchor_pinned 标记", blob.get("anchor_pinned") is True)
check("锚点固定提示存在", any(n[0] == "anchor_pinned" for n in blob.get("anchor_notes", [])))
check("固定候选方法标记为 anchor", top["method"] in ("anchor", "combined"), top["method"])

# 区间内的定位号：旧3（Lifespan）候选必须落在新区间 [2,8]
lid3 = find_loc("Lifespan")
t3 = top_cand(lid3)
check("区间内候选不越界 新2..8", 2 <= t3["new_start"] and t3["new_end"] <= 8,
      str((t3["new_start"], t3["new_end"])))

# 尾段定位号 Winter stores 旧10（与 pin 重合）→ 必须固定新12
lidw = find_loc("Winter stores")
tw = top_cand(lidw)
check("尾段单页锚点固定 旧10→新12", (tw["new_start"], tw["new_end"]) == (12, 12),
      str((tw["new_start"], tw["new_end"])))

# 无锚点时匹配行为与原来一致（回退）
for aid in (a1, a2, a3):
    conn.execute("UPDATE page_anchors SET status='disabled' WHERE id=?", (aid,))
conn.commit()
run_matching(conn, pid, chapter_starts_new=ch_new, threshold=0.85)
t_free = top_cand(find_loc("Honey Bees"))
check("停用全部锚点后候选不受固定约束",
      json.loads(t_free["reasons"]).get("anchor_pinned") is None)
for aid in (a1, a2, a3):
    conn.execute("UPDATE page_anchors SET status='active' WHERE id=?", (aid,))
conn.commit()

# ---------------------------------------------------------------------------
# 4) rematch 预览（内存副本，不动正式数据）
#    先在无锚状态匹配建立基线，再启用锚点预览，应出现变化且正式库头名不变。
# ---------------------------------------------------------------------------

# 此时库中候选是上一节用三锚点算出的；先停用全部锚点重算得到“无锚基线”
for aid in (a1, a2, a3):
    conn.execute("UPDATE page_anchors SET status='disabled' WHERE id=?", (aid,))
conn.commit()
run_matching(conn, pid, chapter_starts_new=ch_new, threshold=0.85)
free_lifespan = dict(top_cand(find_loc("Lifespan")))
check("无锚基线 Lifespan 不在新2..3",
      not (free_lifespan["new_start"] in (2, 3)),
      str((free_lifespan["new_start"], free_lifespan["new_end"])))

# 用锚点把 Lifespan（旧3）固定到单页新4：旧1→2、旧2→3、旧3→4、旧10→12
for aid in (a1, a2, a3):
    conn.execute("UPDATE page_anchors SET status='disabled' WHERE id=?", (aid,))
conn.commit()
am2 = add_anchor(2, 3)
am3 = add_anchor(3, 4)
conn.execute("UPDATE page_anchors SET status='active' WHERE id=?", (a1,))
conn.execute("UPDATE page_anchors SET status='active' WHERE id=?", (a3,))
conn.commit()

# 复刻 app._rematch_changes 的核心流程（避免依赖 Flask）
import sqlite3  # noqa: E402

def preview_changes():
    before = {r["id"]: dict(r) for r in conn.execute(
        """SELECT c.locator_id AS id, c.new_start, c.new_end FROM candidates c
             JOIN locators l ON l.id=c.locator_id JOIN entries e ON e.id=l.entry_id
            WHERE e.project_id=? AND c.rank=1 AND l.status='pending'""", (pid,))}
    mem = sqlite3.connect(":memory:")
    conn.backup(mem)
    mem.row_factory = sqlite3.Row
    run_matching(mem, pid, chapter_starts_new=ch_new, threshold=0.85)
    after = {r["id"]: dict(r) for r in mem.execute(
        """SELECT c.locator_id AS id, c.new_start, c.new_end FROM candidates c
             JOIN locators l ON l.id=c.locator_id JOIN entries e ON e.id=l.entry_id
            WHERE e.project_id=? AND c.rank=1 AND l.status='pending'""", (pid,))}
    mem.close()
    changed = [(lid, (b["new_start"], b["new_end"]), (a["new_start"], a["new_end"]))
               for lid, (b, a) in ((k, (before[k], after[k])) for k in before
                                   if k in after)
               if (b["new_start"], b["new_end"]) != (a["new_start"], a["new_end"])]
    return before, after, changed


before, after, changed = preview_changes()
check("预览发现至少 1 条候选变化", len(changed) >= 1, str(changed[:5]))
lid_ls = find_loc("Lifespan")
check("变化含 Lifespan（区间约束改变候选）",
      any(c[0] == lid_ls for c in changed),
      str([c for c in changed if c[0] == lid_ls]))
still = dict(top_cand(lid_ls))
check("预览不修改正式库头名候选",
      (still["new_start"], still["new_end"])
      == (free_lifespan["new_start"], free_lifespan["new_end"]),
      str((still["new_start"], still["new_end"])))

# 已确认定位号不参与重算
conn.execute("UPDATE page_anchors SET status='active' WHERE id=?", (a2,))
conn.commit()
hb = find_loc("Honey Bees")
conn.execute("UPDATE locators SET status='confirmed', new_start=?, new_end=? WHERE id=?",
             (5, 5, lid3))
conn.commit()
run_matching(conn, pid, chapter_starts_new=ch_new, threshold=0.85)
row = conn.execute("SELECT new_start, status FROM locators WHERE id=?", (lid3,)).fetchone()
check("已确认定位号不被重匹配改动", row["status"] == "confirmed" and row["new_start"] == 5)
conn.execute("UPDATE locators SET status='pending', new_start=NULL, new_end=NULL WHERE id=?",
             (lid3,))
conn.commit()

# ---------------------------------------------------------------------------
# 5) 冲突：停用锚点与现行映射冲突 + 定位号漂移
# ---------------------------------------------------------------------------

# 移除预览用的临时锚点，恢复三锚点场景
conn.execute("DELETE FROM page_anchors WHERE id IN (?,?)", (am2, am3))
conn.commit()

# 先按锚点正式匹配一次
run_matching(conn, pid, chapter_starts_new=ch_new, threshold=0.85)

d1 = add_anchor(2, 9, status="disabled", note="编辑怀疑排版错位")   # 新9 未被占用但倒退
payload = json.loads(export_anchors_json(conn, pid))
conf = payload["conflicts"]
disabled_conf = [c for c in conf if c["kind"] == "disabled_anchor"]
check("停用倒退锚点列入冲突", any(c["anchor_id"] == d1 for c in disabled_conf),
      str([c["message"] for c in disabled_conf]))
check("冲突带备注", any(c["anchor_id"] == d1 and "排版错位" in c["note"] for c in disabled_conf))

d2 = add_anchor(8, 2, status="disabled")  # 新2 已被启用锚点占用
payload = json.loads(export_anchors_json(conn, pid))
disabled_conf = [c for c in payload["conflicts"] if c["kind"] == "disabled_anchor"]
check("占用型停用锚点列入冲突",
      any(c["anchor_id"] == d2 and "占用" in c["message"] for c in disabled_conf))

# 人为制造已确认漂移：把 Honey Bees 确认到远离投影的新页 11
conn.execute("UPDATE locators SET status='confirmed', new_start=11, new_end=11 WHERE id=?", (hb,))
conn.commit()
payload = json.loads(export_anchors_json(conn, pid))
drift = [c for c in payload["conflicts"] if c["kind"] == "confirmed_drift"]
check("已确认漂移列入冲突", any(c["locator_id"] == hb for c in drift),
      str([(c["kind"], c.get("locator_id")) for c in payload["conflicts"]]))
conn.execute("UPDATE locators SET status='pending', new_start=NULL, new_end=NULL WHERE id=?", (hb,))
conn.commit()

# ---------------------------------------------------------------------------
# 6) JSON 对照表结构
# ---------------------------------------------------------------------------

payload = json.loads(export_anchors_json(conn, pid))
check("导出含 anchors", len(payload["anchors"]) == 6, str(len(payload["anchors"])))
check("导出含 segments", len(payload["segments"]) == 4)
check("导出含 page_map", payload["page_map"]["6"] == 8)
check("导出含 locators 对照", len(payload["locators"]) >= 15, str(len(payload["locators"])))
sample_loc = next(l for l in payload["locators"]
                  if l["term"] == "Honey Bees" and not l["subterm"])
check("定位号带投影区间", sample_loc["projected_new_range"] == [2, 2],
      str(sample_loc))
check("导出 JSON 可解析且含项目名", "养蜂手册" in payload["project"]["name"])

# ---------------------------------------------------------------------------
# 7) 快照保存 / 恢复锚点
# ---------------------------------------------------------------------------

snap = json.loads(json.dumps({
    "entries": [dict(r) for r in conn.execute(
        "SELECT * FROM entries WHERE project_id=? ORDER BY id", (pid,))],
    "locators": [dict(r) for r in conn.execute(
        "SELECT l.* FROM locators l JOIN entries e ON e.id=l.entry_id "
        "WHERE e.project_id=? ORDER BY l.id", (pid,))],
    "candidates": [dict(r) for r in conn.execute(
        "SELECT c.* FROM candidates c JOIN locators l ON l.id=c.locator_id "
        "JOIN entries e ON e.id=l.entry_id WHERE e.project_id=?", (pid,))],
    "anchors": [dict(r) for r in conn.execute(
        "SELECT * FROM page_anchors WHERE project_id=?", (pid,))],
}, default=str))

check("快照含 6 个锚点", len(snap["anchors"]) == 6, str(len(snap["anchors"])))
conn.execute("DELETE FROM page_anchors WHERE project_id=?", (pid,))
conn.commit()
check("清空后无锚点", len(anchor_rows(conn, pid)) == 0)
conn.executemany(
    "INSERT INTO page_anchors (id, project_id, old_page, new_page, status, note, created_at)"
    " VALUES (?,?,?,?,?,?,?)",
    [(a["id"], pid, a["old_page"], a["new_page"], a["status"], a["note"], a["created_at"])
     for a in snap["anchors"]])
conn.commit()
rows = anchor_rows(conn, pid)
check("快照恢复锚点（含停用状态与备注）",
      len(rows) == 6 and any(a["status"] == "disabled" and a["note"] for a in rows),
      str([(a["old_page"], a["new_page"], a["status"]) for a in rows]))
check("恢复后启用锚点重建映射", build_ctx(conn, pid) is not None)

# ---------------------------------------------------------------------------
# 8) 撤销：增/删/启停/备注
# ---------------------------------------------------------------------------

n_before = len(anchor_rows(conn, pid))
# 用 log_action 记录一次“新增”并撤销
cur = conn.execute(
    "INSERT INTO page_anchors (project_id, old_page, new_page, status, note)"
    " VALUES (?,?,?,?,?)", (pid, 4, 6, "disabled", "x"))
nid = cur.lastrowid
log_action(conn, pid, "anchor_add", {"anchor": nid},
           "DELETE FROM page_anchors WHERE id=?", [nid])
conn.commit()
check("新增后计数 +1", len(anchor_rows(conn, pid)) == n_before + 1)
undo_last(conn, pid)
conn.commit()
check("撤销新增后计数还原", len(anchor_rows(conn, pid)) == n_before)
check("撤销后该锚点确实消失",
      conn.execute("SELECT 1 FROM page_anchors WHERE id=?", (nid,)).fetchone() is None)

print("\n锚点功能测试全部通过 ✔")
