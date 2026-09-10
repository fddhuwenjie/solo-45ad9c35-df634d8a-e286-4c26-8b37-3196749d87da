# -*- coding: utf-8 -*-
"""两处已复核缺陷的回归测试（不依赖 Flask）。

覆盖：
  1. export_proof_html 可正常返回完整 HTML（原 StringIO.get() AttributeError）；
  2. 已 resolved 的 chapter_parity 在规则再次触发时重新打开，不产生重复行；
  3. 主条目已确认新版页、子条目仍待确认（旧版页）时，不跨版本误报主子矛盾；
     同时同一版本轴内真正的矛盾仍要报出。
"""
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
from core.exporter import export_proof_html  # noqa: E402
from core.validator import validate  # noqa: E402


def check(name, cond, extra=""):
    print(("  ok  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra else ""))
    assert cond, name


conn = get_db()


def make_project(name):
    return conn.execute("INSERT INTO projects (name) VALUES (?)", (name,)).lastrowid


def add_page(pid, edition, page_no, content):
    conn.execute(
        "INSERT INTO pages (project_id, edition, page_no, label, content) VALUES (?,?,?,?,?)",
        (pid, edition, page_no, str(page_no), content))


def add_entry(pid, term, subterm=None, kind="term", target=None):
    cur = conn.execute(
        "INSERT INTO entries (project_id, term, subterm, kind, ref_target) VALUES (?,?,?,?,?)",
        (pid, term, subterm, kind, target))
    return cur.lastrowid


def add_loc(entry_id, old_start, old_end=None, new_start=None, new_end=None, status="pending"):
    cur = conn.execute(
        "INSERT INTO locators (entry_id, old_start, old_end, new_start, new_end, status)"
        " VALUES (?,?,?,?,?,?)",
        (entry_id, old_start, old_end, new_start, new_end, status))
    return cur.lastrowid


# ---------------------------------------------------------------------------
# 1) 可打印校样
# ---------------------------------------------------------------------------

pid = make_project("回归测试：校样")
for i in range(1, 4):
    add_page(pid, "old", i, f"旧版第 {i} 页内容")
    add_page(pid, "new", i, f"新版第 {i} 页内容")
eid = add_entry(pid, "Honey & Wax <测试>")
add_loc(eid, 1, new_start=2, status="confirmed")
eid2 = add_entry(pid, "Pending Term")
add_loc(eid2, 3, status="pending")

proof = export_proof_html(conn, pid)
check("校样返回 str", isinstance(proof, str))
check("校样以 <!doctype html> 开头", proof.lstrip().lower().startswith("<!doctype html>"))
check("校样以 </html> 结尾", proof.rstrip().endswith("</html>"))
check("校样含条目名（已转义特殊字符）",
      "Honey &amp; Wax &lt;测试&gt;" in proof and "Honey & Wax" not in proof)
check("校样含新版页码 2", ">2<" in proof)
check("校样含待确认标记", "[待确认 旧3]" in proof)

# ---------------------------------------------------------------------------
# 2) chapter_parity 解决后可重新打开
# ---------------------------------------------------------------------------

pid2 = make_project("回归测试：章奇偶重开")
# 新版第 2 页（偶数）为章节首页
add_page(pid2, "new", 1, "前言段落，没有章节标记。")
add_page(pid2, "new", 2, "Chapter 1. A Real Heading\n正文内容。")
add_page(pid2, "old", 1, "Chapter 1. 旧版首页。")

conn.execute(
    "UPDATE projects SET chapter_policy='recto' WHERE id=?", (pid2,))
validate(conn, pid2)
parity_rows = conn.execute(
    "SELECT id, status FROM issues WHERE project_id=? AND code='chapter_parity'",
    (pid2,)).fetchall()
check("recto 规则下偶数章首页报问题", len(parity_rows) == 1 and parity_rows[0]["status"] == "open",
      f"{[dict(r) for r in parity_rows]}")
issue_id = parity_rows[0]["id"]

# 再次 validate 不应产生重复行，也不应改变状态
validate(conn, pid2)
rows_again = conn.execute(
    "SELECT COUNT(*) c FROM issues WHERE project_id=? AND code='chapter_parity'",
    (pid2,)).fetchone()["c"]
check("重复校验不产生重复问题行", rows_again == 1)

# 切到任意页规则 -> 问题解决
conn.execute("UPDATE projects SET chapter_policy='any' WHERE id=?", (pid2,))
validate(conn, pid2)
st = conn.execute("SELECT status FROM issues WHERE id=?", (issue_id,)).fetchone()["status"]
check("改为任意页规则后问题 resolved", st == "resolved", st)
n_open = conn.execute(
    "SELECT COUNT(*) c FROM issues WHERE project_id=? AND status='open'", (pid2,)).fetchone()["c"]
check("此时无未决问题", n_open == 0, str(n_open))

# 再切回 recto -> 同一问题重新打开（复用原行，而非插入新行）
conn.execute("UPDATE projects SET chapter_policy='recto' WHERE id=?", (pid2,))
validate(conn, pid2)
st2 = conn.execute("SELECT status FROM issues WHERE id=?", (issue_id,)).fetchone()["status"]
total2 = conn.execute(
    "SELECT COUNT(*) c FROM issues WHERE project_id=? AND code='chapter_parity'",
    (pid2,)).fetchone()["c"]
check("改回 recto 后原问题重新打开", st2 == "open", st2)
check("重新打开未新增重复行", total2 == 1, str(total2))

# ---------------------------------------------------------------------------
# 3) 主子条目不跨版本误报矛盾
# ---------------------------------------------------------------------------

pid3 = make_project("回归测试：主子矛盾版本轴")
for i in range(1, 21):
    add_page(pid3, "old", i, f"旧版第 {i} 页。")
    add_page(pid3, "new", i, f"新版第 {i} 页。")

# 场景 3a：主条目旧版范围 10-12，已确认改到新版 15-17；
#          子条目仍待确认，旧版页 11（在主条目的旧版范围内）。
parent = add_entry(pid3, "Pollination")
child = add_entry(pid3, "Pollination", subterm="bees and")
add_loc(parent, 10, 12, new_start=15, new_end=17, status="confirmed")
add_loc(child, 11, status="pending")

validate(conn, pid3)
contras = conn.execute(
    "SELECT message FROM issues WHERE project_id=? AND code='subterm_contradiction' AND status='open'",
    (pid3,)).fetchall()
check("主新/子旧时不误报主子矛盾", len(contras) == 0,
      "; ".join(r["message"] for r in contras))

# 场景 3b：子条目也确认到新版，且落在主条目新版范围之外 -> 必须报新版矛盾
ploc_child = conn.execute("SELECT id FROM locators WHERE entry_id=?", (child,)).fetchone()["id"]
conn.execute(
    "UPDATE locators SET new_start=19, new_end=19, status='confirmed' WHERE id=?",
    (ploc_child,))
conn.commit()
validate(conn, pid3)
contras2 = conn.execute(
    "SELECT message FROM issues WHERE project_id=? AND code='subterm_contradiction' AND status='open'",
    (pid3,)).fetchall()
check("同版本轴内真正的矛盾仍报出", len(contras2) == 1 and "新版" in contras2[0]["message"],
      "; ".join(r["message"] for r in contras2))

# 场景 3c：把主条目改回待确认（只剩旧版范围），子条目旧版页落在主旧版范围外
#          -> 旧版矛盾报出；若子在主旧版范围内则不报
conn.execute(
    "UPDATE locators SET new_start=NULL, new_end=NULL, status='pending' WHERE entry_id=?",
    (parent,))
conn.commit()
validate(conn, pid3)
contras3 = conn.execute(
    "SELECT message FROM issues WHERE project_id=? AND code='subterm_contradiction' AND status='open'",
    (pid3,)).fetchall()
# 此时子是新版19、主只有旧版10-12：版本轴不同（主旧/子新），不应比较
check("主旧/子新时同样不跨版本误报", len(contras3) == 0,
      "; ".join(r["message"] for r in contras3))

# 子也回到旧版、且落在主旧版范围外 -> 报旧版矛盾
conn.execute(
    "UPDATE locators SET new_start=NULL, new_end=NULL, status='pending' WHERE id=?",
    (ploc_child,))
# 改成主旧版范围 10-12 之外的旧页 18
conn.execute("UPDATE locators SET old_start=18, old_end=18 WHERE id=?", (ploc_child,))
conn.commit()
validate(conn, pid3)
contras4 = conn.execute(
    "SELECT message FROM issues WHERE project_id=? AND code='subterm_contradiction' AND status='open'",
    (pid3,)).fetchall()
check("旧版轴内的矛盾仍报出", len(contras4) == 1 and "旧版" in contras4[0]["message"],
      "; ".join(r["message"] for r in contras4))

print("\n回归测试全部通过 ✔")
