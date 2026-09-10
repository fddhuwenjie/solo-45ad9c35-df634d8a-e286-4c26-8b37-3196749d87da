# -*- coding: utf-8 -*-
"""锚点相关 Flask 路由的集成测试（内存 test_client + 临时数据库）。"""
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

import app as appmod  # noqa: E402
from core.sample_data import load_sample  # noqa: E402

conn = dbmod.get_db()
pid = load_sample(conn)
conn.commit()

client = appmod.app.test_client()
failures = []


def check(name, cond, extra=""):
    print(("  ok  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        failures.append(name)


def jpost(url, body=None):
    return client.post(url, data=json.dumps(body or {}),
                       content_type="application/json")


def jget(url):
    return client.get(url)


# 初始状态：样例预置 1 个停用锚点，无启用锚点
r = jget(f"/api/projects/{pid}/anchors")
st0 = r.get_json()
check("GET anchors 初始仅 1 个停用锚点",
      len(st0["anchors"]) == 1 and st0["anchors"][0]["status"] == "disabled",
      str(len(st0["anchors"])))
check("初始无分段（无启用锚点）", st0["segments"] == [])

# 正常创建
r = jpost(f"/api/projects/{pid}/anchors",
          {"old_page": 1, "new_page": 2, "note": "前言插入处"})
check("创建锚点 200", r.status_code == 200, str(r.status_code))
a1 = r.get_json()["id"]

# 倒退锚点 -> 409 + anchor_conflict
r = jpost(f"/api/projects/{pid}/anchors", {"old_page": 4, "new_page": 1})
check("倒退锚点 409", r.status_code == 409 and r.get_json().get("code") == "anchor_conflict",
      str(r.status_code))

# 越界 -> 409? 当前实现越界返回 409 吗？validate_anchor 返回文案，路由统一 409
r = jpost(f"/api/projects/{pid}/anchors", {"old_page": 99, "new_page": 2})
check("越界锚点被拒（4xx）", r.status_code in (400, 409), str(r.status_code))

# 停用保存绕过单调性
r = jpost(f"/api/projects/{pid}/anchors",
          {"old_page": 4, "new_page": 1, "active": False, "note": "待核对"})
check("停用锚点可保存 200", r.status_code == 200, str(r.status_code))
d1 = r.get_json()["id"]
check("停用锚点出现在冲突列表",
      any(c["anchor_id"] == d1 for c in r.get_json()["conflicts"]))

# 启用冲突锚点 -> 409
r = jpost(f"/api/anchors/{d1}/toggle")
check("启用倒退锚点 409", r.status_code == 409, str(r.status_code))

# 继续建两个合法锚点
r = jpost(f"/api/projects/{pid}/anchors", {"old_page": 6, "new_page": 8})
a2 = r.get_json()["id"]
r = jpost(f"/api/projects/{pid}/anchors", {"old_page": 10, "new_page": 12})
a3 = r.get_json()["id"]
st = jget(f"/api/projects/{pid}/anchors").get_json()
check("三个启用锚点", sum(1 for a in st["anchors"] if a["status"] == "active") == 3)
check("分段数 4（head+2区间+tail）", len(st["segments"]) == 4, str(len(st["segments"])))
check("page_map 旧6->新8", st["page_map"]["6"] == 8)
check("含样例预置停用锚点", sum(1 for a in st["anchors"] if a["status"] == "disabled") == 2)

# 备注
r = jpost(f"/api/anchors/{a2}/note", {"note": "第二章起始"})
check("备注 200 且生效", r.status_code == 200
      and next(a for a in r.get_json()["anchors"] if a["id"] == a2)["note"] == "第二章起始")

# 无锚点项目的重匹配预览 -> 400（新建空项目）
r = jpost("/api/projects", {"name": "无锚点项目"})
empty_pid = r.get_json()["id"]
r = jpost(f"/api/projects/{empty_pid}/rematch/preview")
check("无锚点预览 400", r.status_code == 400, str(r.status_code))

# 先跑一次常规匹配建立候选
r = jpost(f"/api/projects/{pid}/match")
check("常规匹配 200", r.status_code == 200, str(r.status_code))

# 找到 Lifespan locator id（旧3）
state = jget(f"/api/state/{pid}").get_json()
ls = [l for e in state["entries"] for l in e["locators"]
      if e["term"] == "Lifespan"]
check("找到 Lifespan 定位号", len(ls) == 1)
lid = ls[0]["id"]
before_top = ls[0]["candidate"] and (ls[0]["candidate"]["new_start"],
                                     ls[0]["candidate"]["new_end"])

# 预览（三锚点 1->2, 6->8, 10->12 下旧3投影4，候选可能仍 4-5）
r = jpost(f"/api/projects/{pid}/rematch/preview")
prev = r.get_json()
check("预览 200 且含统计",
      r.status_code == 200 and "affected" in prev and "changes" in prev,
      str(r.status_code))
check("预览统计 pending_total > 0", prev["pending_total"] > 0, str(prev["pending_total"]))

# 预览不改变正式候选
state2 = jget(f"/api/state/{pid}").get_json()
ls2 = [l for e in state2["entries"] for l in e["locators"] if e["term"] == "Lifespan"][0]
after_preview = ls2["candidate"] and (ls2["candidate"]["new_start"],
                                      ls2["candidate"]["new_end"])
check("预览不改正式候选", after_preview == before_top,
      f"{before_top} vs {after_preview}")

# 执行重匹配
r = jpost(f"/api/projects/{pid}/rematch")
res = r.get_json()
check("执行重匹配 200", r.status_code == 200 and res["ok"], str(r.status_code))
check("执行返回受影响数", "affected" in res and "changes" in res)
state3 = jget(f"/api/state/{pid}").get_json()
ls3 = [l for e in state3["entries"] for l in e["locators"] if e["term"] == "Lifespan"][0]
top3 = ls3["candidate"]
check("重匹配后候选在锚点区间内 新2..8",
      top3 and 2 <= top3["new_start"] and top3["new_end"] <= 8,
      str((top3["new_start"], top3["new_end"])))
hb = [l for e in state3["entries"] for l in e["locators"] if e["term"] == "Honey Bees"][0]
check("单页锚点 Honey Bees 固定新2",
      hb["candidate"]["new_start"] == 2 and hb["candidate"]["new_end"] == 2,
      str((hb["candidate"]["new_start"], hb["candidate"]["new_end"])))
check("候选带 anchor_pinned 标记", hb["candidate"]["anchor_pinned"] is True)
check("state 含锚点数据", "anchors" in state3 and len(state3["anchors"]["anchors"]) == 5)

# 已确认定位号不被重匹配改动
jpost(f"/api/locators/{lid}/decision", {"action": "rebind", "start": 5, "end": 5})
r = jpost(f"/api/projects/{pid}/rematch")
state4 = jget(f"/api/state/{pid}").get_json()
ls4 = [l for e in state4["entries"] for l in e["locators"] if e["term"] == "Lifespan"][0]
check("已确认定位号重匹配后仍为人工值",
      ls4["status"] == "confirmed" and ls4["new_start"] == 5, str((ls4["status"], ls4["new_start"])))

# 导出锚点 JSON
r = client.get(f"/api/projects/{pid}/export/anchors")
check("JSON 导出 200", r.status_code == 200 and r.mimetype == "application/json",
      str(r.status_code))
data = r.get_json()
check("导出含 segments/anchors/page_map/conflicts/locators",
      all(k in data for k in ("segments", "anchors", "page_map", "conflicts", "locators")))
check("导出锚点 5 个（3 启用 2 停用）", len(data["anchors"]) == 5, str(len(data["anchors"])))
check("导出含备注", any(a["note"] for a in data["anchors"]))
check("导出含停用锚点冲突", any(c["kind"] == "disabled_anchor" for c in data["conflicts"]))

# 快照：保存含锚点 -> 删除锚点 -> 恢复 -> 锚点回来
r = jpost(f"/api/projects/{pid}/snapshots", {"name": "含锚点版本"})
sid = r.get_json()["id"]
conn2 = dbmod.get_db()
conn2.execute("DELETE FROM page_anchors WHERE project_id=?", (pid,))
conn2.commit()
st_after_del = jget(f"/api/projects/{pid}/anchors").get_json()
check("删除后无锚点", len(st_after_del["anchors"]) == 0)
r = jpost(f"/api/projects/{pid}/snapshots/{sid}/restore")
check("快照恢复 200", r.status_code == 200, str(r.status_code))
st_rest = jget(f"/api/projects/{pid}/anchors").get_json()
check("恢复后 5 个锚点回来", len(st_rest["anchors"]) == 5, str(len(st_rest["anchors"])))
check("恢复保留停用状态",
      any(a["status"] == "disabled" for a in st_rest["anchors"]))

# 撤销恢复（锚点也应回到删除后状态）
r = jpost(f"/api/projects/{pid}/undo")
check("撤销恢复 200", r.status_code == 200 and r.get_json()["ok"])
st_undo = jget(f"/api/projects/{pid}/anchors").get_json()
check("撤销恢复后锚点再次清空", len(st_undo["anchors"]) == 0,
      str(len(st_undo["anchors"])))

# 单独撤销锚点操作：重新加锚点后撤销
r = jpost(f"/api/projects/{pid}/anchors", {"old_page": 1, "new_page": 2})
n = len(jget(f"/api/projects/{pid}/anchors").get_json()["anchors"])
check("重新加锚点", n == 1, str(n))
jpost(f"/api/projects/{pid}/undo")
n2 = len(jget(f"/api/projects/{pid}/anchors").get_json()["anchors"])
check("撤销新增锚点", n2 == 0, str(n2))

# 删除不存在项目 404
r = jget("/api/projects/9999/anchors")
check("不存在项目 404", r.status_code == 404, str(r.status_code))

print()
if failures:
    print(f"失败 {len(failures)} 项: {failures}")
    sys.exit(1)
print("Flask 锚点路由集成测试全部通过 ✔")
