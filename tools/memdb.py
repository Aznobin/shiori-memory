#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记忆库索引与检索（SQLite 单文件，磁盘随便占）。

用法：
  python memdb.py index                 # 重建索引（改完 md 就跑一次）
  python memdb.py stats                 # 库的概况
  python memdb.py search "关键词" [-k 5]  # 三因子打分检索（含命中行号）
  python memdb.py conflicts             # 同槽位多值（矛盾检测）
  python memdb.py expire                # TTL 过期条目
  python memdb.py quiz                  # 20 题命中率自测
  python memdb.py forget "子串" [--yes]  # 软删除（只标 DB + 审计，md 手动改）
  python memdb.py snapshot              # 手动备用：打 zip 快照（2026-10-19 起默认不拍，历史走 git）

设计：markdown 是唯一事实源，本库只是索引/审计层，删了重跑 index 就能重建。
"""
import argparse
import contextlib
import math
import os
import re
import sqlite3
import sys
import time
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "tools", "memdb.sqlite3")
REFS = os.path.join(ROOT, "references")
STATE = os.path.join(os.path.expanduser("~"), ".agents", "state")
LOCK = os.path.join(STATE, "memory.lock")
SNAP = os.path.join(STATE, "snapshots")
CJK = r"\u4e00-\u9fff"

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  id TEXT PRIMARY KEY, path TEXT, title TEXT, always INT, sensitive INT,
  updated TEXT, nlines INT, kw TEXT);
CREATE TABLE IF NOT EXISTS entries(
  rid INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT, line INT, text TEXT,
  kind TEXT, importance INT, ttl TEXT, slot TEXT,
  deleted INT DEFAULT 0, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS links(doc_id TEXT, target TEXT);
CREATE TABLE IF NOT EXISTS audit(
  aid INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, action TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS i_entries_doc ON entries(doc_id);
"""


def read(p):
    with open(p, "r", encoding="utf-8-sig") as f:
        return f.read()


@contextlib.contextmanager
def flock(timeout=60):
    """跨会话互斥：建锁文件，超时或残锁（>5 分钟）自动清。"""
    os.makedirs(STATE, exist_ok=True)
    t0 = time.time()
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(LOCK)
            except OSError:
                age = 0
            if age > 300:
                try:
                    os.remove(LOCK)
                except OSError:
                    pass
                continue
            if time.time() - t0 > timeout:
                raise SystemExit(f"记忆锁被占着（{LOCK}），等了 {timeout}s，放弃")
            time.sleep(0.3)
            continue
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
        break
    try:
        yield
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def parse_front(txt):
    meta = {}
    if txt.startswith("---"):
        end = txt.find("\n---", 3)
        if end > 0:
            head, body = txt[3:end], txt[end + 4:]
            last = None
            for ln in head.splitlines():
                m = re.match(r"^([A-Za-z_]+):\s*(.*)$", ln)
                if m:
                    last = m.group(1)
                    meta[last] = m.group(2).strip().strip('"')
                elif last and re.match(r"^\s+-\s+", ln):
                    item = re.sub(r"^\s+-\s+", "", ln).strip()
                    meta[last] = (meta.get(last, "") + " " + item).strip()
            return meta, body
    return meta, txt


def connect():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    return con


def tok(q):
    out = []
    for seg in re.findall("[" + CJK + r"]+|[A-Za-z0-9_.\-]+", q):
        if re.match("[" + CJK + "]", seg):
            if len(seg) == 1:
                out.append(seg)
            else:
                out += [seg[i:i + 2] for i in range(len(seg) - 1)]
        else:
            out.append(seg.lower())
    return [t for t in out if t]


def kind_of(line):
    s = line.strip()
    if s.startswith("#"):
        return "head"
    if s.startswith(("- ", "* ")):
        return "bullet"
    if s.startswith("|"):
        return "table"
    return "prose"


def importance_of(con, doc_id, text):
    if re.search(r"红线|禁止|不许|绝不许|必须|一律|别主动", text):
        return 3
    row = con.execute("SELECT always FROM docs WHERE id=?", (doc_id,)).fetchone()
    return 2 if row and row[0] else 1


def do_index(_):
    con = connect()
    for t in ("docs", "entries", "links"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    con.executescript(SCHEMA)
    files = [os.path.join(ROOT, "SKILL.md")]
    for f in sorted(os.listdir(REFS)):
        if f.endswith(".md"):
            files.append(os.path.join(REFS, f))
    n = 0
    for p in files:
        txt = read(p)
        meta, body = parse_front(txt)
        doc_id = meta.get("id") or os.path.splitext(os.path.basename(p))[0]
        kw = " ".join(k.strip(" -") for k in meta.get("keywords", "").splitlines() if k.strip())
        con.execute(
            "INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?,?,?)",
            (doc_id, os.path.basename(p), meta.get("title", doc_id),
             1 if meta.get("always") == "true" else 0,
             1 if meta.get("sensitive") == "true" else 0,
             meta.get("updated", ""), len(body.splitlines()), kw),
        )
        for m in re.findall(r"\[\[([^\]|#]+)", body):
            con.execute("INSERT INTO links VALUES (?,?)", (doc_id, m.strip()))
        for i, line in enumerate(body.splitlines(), 1):
            if not line.strip():
                continue
            ttl = None
            m = re.search(r"（TTL[:：]\s*(\d{4}-\d{2}-\d{2})", line)
            if m:
                ttl = m.group(1)
            slot = None
            m = re.search(r"〔槽位[:：]?\s*([^〕]+)〕", line)
            if m:
                slot = m.group(1).strip()
            con.execute(
                "INSERT INTO entries(doc_id,line,text,kind,importance,ttl,slot)"
                " VALUES (?,?,?,?,?,?,?)",
                (doc_id, i, line.strip(), kind_of(line),
                 importance_of(con, doc_id, line), ttl, slot),
            )
            n += 1
    con.commit()
    print(f"索引完成：{len(files)} 个文件，{n} 行 → {DB}")


def recency(day):
    try:
        d = (date.today() - datetime.strptime(day, "%Y-%m-%d").date()).days
    except Exception:
        return 0.85
    return 1.2 if d <= 7 else (1.0 if d <= 30 else 0.85)


def search_rows(con, q, k=5):
    toks = tok(q)
    if not toks:
        return []
    docs = {r[0]: r for r in con.execute("SELECT id,title,updated,always,kw FROM docs")}
    rows = con.execute(
        "SELECT rid,doc_id,line,text,kind,importance,ttl,slot FROM entries WHERE deleted=0"
    ).fetchall()
    n = max(1, len(rows))
    df = {}
    for t in toks:
        df[t] = sum(1 for r in rows if t in r[3].lower())
    # 稀有权重：df 越小越值钱（1/df^0.6）
    w = {t: 1.0 / ((df[t] + 1) ** 0.6) for t in toks}
    # `rare` 只从**语料里真出现过**的词里挑（2026-10-21 修）：
    # 原来看全部 token，而 bigram 切词会产生大量跨词边界的二元组（「时间复杂」切出
    # `间复`、`度怎`），它们 df=0、权重最高，于是 `rare` 恒为这种假词，
    # 下面 `rare not in matched` 对所有结果都成立、整条检索被统一 ×0.25，
    # 排序全被压平（实测「时间复杂度怎么算」「代理又连不上了」都因此错命中）。
    present = [t for t in toks if df[t] > 0]
    rare = max(present, key=lambda t: w[t]) if present else None
    # 覆盖率也只算**能匹配到的词**（2026-10-21 同一处修）：`cover` 的分母原来是全部
    # token，而 bigram 跨边界产生的假词永远匹配不到，分母虚高、cover 恒低，
    # 长句尤其吃亏（实测 39 个查询里 32 个含 df=0 的假词）。
    live = set(present) or set(toks)
    today = date.today().isoformat()
    scored = []
    for rid, doc_id, line, text, kind, imp, ttl, slot in rows:
        if ttl and ttl < today:
            continue
        low = text.lower()
        matched = [t for t in toks if t in low]
        if not matched:
            continue
        d = docs.get(doc_id, ("", "", "", 0, ""))
        base = sum(w[t] for t in matched)
        cover = 0.6 + 0.4 * len(set(matched) & live) / len(live)
        if rare is None or rare not in matched:
            base *= 0.25
        kw = (d[4] or "").lower()
        kw_boost = 1.3 if any(t in kw for t in toks if len(t) > 1) else 1.0
        s = base * cover * (1 + 0.3 * imp) * recency(d[2]) * kw_boost
        if kind == "head":
            s *= 0.6
        scored.append((round(s, 2), doc_id, line, text))
    scored.sort(key=lambda x: -x[0])
    return scored[:k]


def show(rows):
    for s, doc, line, text in rows:
        print(f"  [{s:>5}] {doc}:{line}  {text[:150]}")


def do_search(a):
    con = connect()
    show(search_rows(con, a.query, a.k))


def do_stats(_):
    con = connect()
    d = con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    e = con.execute("SELECT COUNT(*) FROM entries WHERE deleted=0").fetchone()[0]
    dl = con.execute("SELECT COUNT(*) FROM entries WHERE deleted=1").fetchone()[0]
    sz = os.path.getsize(DB) / 1024 if os.path.exists(DB) else 0
    print(f"模块 {d} 个 · 条目 {e} 行 · 软删 {dl} 行 · 库 {sz:.0f} KB")
    for title, n, up in con.execute(
            "SELECT title, COUNT(*), MAX(0) FROM docs JOIN entries ON entries.doc_id=docs.id"
            " GROUP BY docs.id ORDER BY docs.id"):
        print(f"  {title:<24} {n:>4} 行")


def do_conflicts(_):
    con = connect()
    rows = con.execute(
        "SELECT doc_id, slot, COUNT(*), GROUP_CONCAT(line) FROM entries"
        " WHERE deleted=0 AND slot IS NOT NULL GROUP BY doc_id, slot HAVING COUNT(*)>1"
    ).fetchall()
    if not rows:
        print("没有同槽位多值。")
    for doc, slot, n, lines in rows:
        print(f"  {doc} 〔{slot}〕 {n} 条，行号 {lines}")
        for (t,) in con.execute(
                "SELECT text FROM entries WHERE doc_id=? AND slot=? AND deleted=0", (doc, slot)):
            print(f"    - {t[:110]}")


def do_expire(_):
    con = connect()
    today = date.today().isoformat()
    rows = con.execute(
        "SELECT doc_id, line, ttl, text FROM entries WHERE deleted=0 AND ttl IS NOT NULL"
        " AND ttl <= ? ORDER BY ttl", (today,)).fetchall()
    if not rows:
        print("没有过期条目。")
    for doc, line, ttl, text in rows:
        print(f"  {doc}:{line} [{ttl} 过期] {text[:110]}")


QUIZ = [
    ("她什么学习身份、多大", "身份 年龄", "大二"),
    ("我怎么称呼自己", "自称 称呼", "自称"),
    ("语气两档怎么切", "破限词", "破限词"),
    ("她最烦哪种回复风格", "括号 动作 长", "括号"),
    ("算法讲解给不给完整代码", "代码块 默认 输出", "不默认输出代码块"),
    ("笔记精炼规则", "精炼 笔记", "精炼"),
    ("省 token 的规矩", "token", "token"),
    ("日记怎么读", "日记 daily", "daily"),
    ("项目现在什么版本", "版本 V0", "V0."),
    ("pwsh 挂了怎么办", "pwsh 3221225794", "3221225794"),
    ("抓网页用什么工具", "bcdp 浏览器", "bcdp"),
    ("记忆写入要带什么回执", "回执", "回执"),
    ("台账什么时候升级", "台账 升级", "2 次"),
    ("体检器怎么跑", "memory-check", "memory-check"),
    ("主动开口的几个时间点", "08:30 定时", "08:30"),
    ("发图边界", "learn_meme", "learn_meme"),
    ("哪些健康信息不主动提", "健康 提及", "敏感健康信息"),
    ("自维护能改什么范围", "记忆模块自身", "记忆模块自身"),
    ("Hindsight 为什么关了", "hindsight", "hindsight"),
    ("上次改了记忆库的哪里", "变更日志", "变更日志"),
]


def do_quiz(_):
    con = connect()
    allrows = con.execute("SELECT text FROM entries WHERE deleted=0").fetchall()
    corpus = "\n".join(r[0].lower() for r in allrows)
    hit = miss = gap = 0
    print("题号  结果    问题 → 期望词")
    for i, (q, keys, want) in enumerate(QUIZ, 1):
        if want.lower() not in corpus:
            gap += 1
            print(f"{i:>3}  语料缺失  {q} → {want}")
            continue
        top = search_rows(con, keys, 3)
        if any(want.lower() in t.lower() for _, _, _, t in top):
            hit += 1
            print(f"{i:>3}  命中    {q} → {want}")
        else:
            miss += 1
            print(f"{i:>3}  漏检    {q} → {want}  （top1: {top[0][3][:60] if top else '空'}）")
    total = len(QUIZ)
    print(f"\n命中 {hit}/{total} = {hit / total:.0%} · 漏检 {miss} · 语料缺失 {gap}")


def do_forget(a):
    con = connect()
    rows = con.execute(
        "SELECT rid,doc_id,line,text FROM entries WHERE deleted=0 AND text LIKE ?",
        (f"%{a.substr}%",)).fetchall()
    if not rows:
        print("没匹配到。")
        return
    for rid, doc, line, text in rows:
        print(f"  {doc}:{line}  {text[:120]}")
    if not a.yes:
        print("\n只看不改。确认后加 --yes（只标 DB 软删 + 审计，md 还得手动删）")
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for rid, doc, line, text in rows:
        con.execute("UPDATE entries SET deleted=1, deleted_at=? WHERE rid=?", (now, rid))
        con.execute("INSERT INTO audit(at,action,detail) VALUES (?,?,?)",
                    (now, "delete", f"{doc}:{line} {text[:80]}"))
    con.commit()
    print(f"已软删 {len(rows)} 行（记得去 md 里删掉对应行）")


def do_snapshot(a):
    """把 references/ 全部 md 打一份 zip 快照，保留最近 N 份（默认 20）。"""
    import zipfile
    keep = max(1, int(getattr(a, "keep", 20) or 20))
    os.makedirs(SNAP, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(SNAP, f"references-{stamp}.zip")
    with flock():
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(os.listdir(REFS)):
                p = os.path.join(REFS, name)
                if os.path.isfile(p) and name.endswith(".md"):
                    z.write(p, name)
                elif os.path.isdir(p):
                    for f in sorted(os.listdir(p)):
                        fp = os.path.join(p, f)
                        if os.path.isfile(fp):
                            z.write(fp, f"{name}/{f}")
    snaps = sorted(
        f for f in os.listdir(SNAP)
        if f.startswith("references-") and f.endswith(".zip")
    )
    for old in snaps[:-keep]:
        try:
            os.remove(os.path.join(SNAP, old))
        except OSError:
            pass
    size = os.path.getsize(out)
    print(f"快照：{out}（{size} bytes，本目录共 {min(len(snaps), keep)} 份，留最近 {keep} 份）")


def do_random(a):
    """随机抽 1～N 条记忆，用来自己开话题（跳过敏感与 always 模块）。"""
    import random
    n = max(1, getattr(a, "n", 1))
    con = connect()
    docs = con.execute(
        "SELECT id, title FROM docs WHERE sensitive=0 AND always=0 "
        "AND id NOT IN ('变更日志', 'CATALOG', 'SKILL', '_待确认', '_巡检记录', '_复核清单')"
    ).fetchall()
    random.shuffle(docs)
    picked = 0
    for doc_id, title in docs:
        if picked >= n:
            break
        rows = con.execute(
            "SELECT line, text FROM entries WHERE doc_id=? "
            "AND length(trim(text))>15 AND text NOT LIKE '|%' "
            "AND text NOT LIKE '#%' AND text NOT LIKE '>%' "
            "AND text NOT LIKE '%---%' AND text NOT LIKE '```%'",
            (doc_id,),
        ).fetchall()
        if not rows:
            continue
        line, text = random.choice(rows)
        print(f"[{title} L{line}] {text}")
        picked += 1


def main():
    ap = argparse.ArgumentParser(description="记忆库索引与检索")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index").set_defaults(fn=do_index)
    sub.add_parser("stats").set_defaults(fn=do_stats)
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("-k", type=int, default=5)
    p.set_defaults(fn=do_search)
    sub.add_parser("conflicts").set_defaults(fn=do_conflicts)
    sub.add_parser("expire").set_defaults(fn=do_expire)
    sub.add_parser("quiz").set_defaults(fn=do_quiz)
    p = sub.add_parser("forget")
    p.add_argument("substr")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=do_forget)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--keep", type=int, default=20, help="保留最近几份快照，默认 20")
    snap.set_defaults(fn=do_snapshot)
    p = sub.add_parser("random")
    p.add_argument("-n", type=int, default=1)
    p.set_defaults(fn=do_random)
    a = ap.parse_args()
    if a.cmd == "index":
        with flock():
            a.fn(a)
    else:
        a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
