#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日记 → 记忆 的每日同步（把当天日记压成 `11-日常记录` 里的一行）

用法：
  python tools/diary_sync.py                 # 同步今天
  python tools/diary_sync.py --date 2026-10-18
  python tools/diary_sync.py --dry           # 只看会写成什么，不落盘
  python tools/diary_sync.py --all-missing   # 补最近 14 天里 11 缺行的日子

规则：
  - 只读日记，不改日记；只写 `references/11-日常记录.md`
  - 一天一行，同一天已有就 EDIT 那一行，不新开
  - 压缩口径：今天做了 / 今天学会 / 遇到的问题 各取要点，数据栏取 Git、单词
  - 落盘后提示跑 `python tools/memdb.py index`
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import diary  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "references", "11-日常记录.md")
CONFIG = os.path.join(ROOT, "memory.config.json")
WEEK = "一二三四五六日"

SECTION_KEYS = [
    ("did", ("今天做了", "今天干嘛", "做了什么")),
    ("learned", ("今天学会", "学会了")),
    ("stuck", ("遇到的问题", "问题", "卡在")),
    ("data", ("今天的数据", "数据")),
]
DATA_KEYS = ("Git", "单词", "查询", "算法")


def workspace():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            ws = json.load(f)["paths"]["workspace"]
        if os.path.isdir(ws):
            return ws
    except Exception:
        pass
    # 没配置时的兜底：不写死用户名，按环境变量 / 常见位置 / 当前目录找（2026-10-19）
    env = os.environ.get("MEMORY_WORKSPACE")
    if env and os.path.isdir(env):
        return env
    home = os.path.expanduser("~")
    for cand in (os.getcwd(), home):
        if os.path.isdir(os.path.join(cand, diary("dir"))):
            return cand
    return os.getcwd()


def diary_path(d):
    return os.path.join(
        workspace(),
        diary("dir"),
        "%s %d年%d月%d日.md" % (diary("prefix"), d.year, d.month, d.day),
    )


def clean(text):
    text = re.sub(r"^- \[[ xX]\]\s*", "", text)          # 任务勾选框
    text = re.sub(r"^- \s*", "", text)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]", r"\1", text)  # wikilink 只留文字
    return text.strip().rstrip("。；; ")


def parse(path):
    """返回 {section: [要点...]}，data 是 {Git: '0', 单词: '2 个'} """
    out = {"did": [], "learned": [], "stuck": [], "data": {}}
    if not os.path.exists(path):
        return None
    cur = None
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("## "):
                title = line[3:].strip()
                cur = None
                for key, names in SECTION_KEYS:
                    if any(title.startswith(n) for n in names):
                        cur = key
                        break
                continue
            if cur is None:
                continue
            if cur == "data":
                m = re.match(r"^>\s*([^：:]+)[：:]\s*(.*)$", line.strip())
                if m and m.group(1).strip() in DATA_KEYS and m.group(2).strip():
                    out["data"][m.group(1).strip()] = clean(m.group(2))
                continue
            if line.startswith("- "):
                item = clean(line)
                if item:
                    out[cur].append(item)
    return out


def summarize(d, info):
    """把日记压成一行（不含 `- 日期：` 前缀）"""
    parts = []
    if info["did"]:
        parts.append("做了：" + "；".join(info["did"][:3]))
    if info["learned"]:
        parts.append("学会：" + "；".join(info["learned"][:2]))
    if info["stuck"]:
        parts.append("卡在：" + "；".join(info["stuck"][:1]))
    if info["data"]:
        parts.append(
            "数据 " + " / ".join("%s %s" % (k, v) for k, v in info["data"].items())
        )
    if not parts:
        parts.append("当天日记基本空白")
    line = "；".join(parts)
    return line if len(line) <= 260 else line[:259] + "…"


def render(d, body):
    return "- %s（周%s）：%s（源：日记）" % (
        d.isoformat(),
        WEEK[d.weekday()],
        body,
    )


def upsert(line, d):
    """把一行写进 11 的当月小节；已存在同日则替换。返回动作名。"""
    with open(LOG, encoding="utf-8") as f:
        text = f.read()
    month = "## %d-%02d" % (d.year, d.month)
    date_prefix = "- %s（周" % d.isoformat()
    lines = text.split("\n")
    action = "add"

    # 1) 同一天已有 → 原位替换
    for i, ln in enumerate(lines):
        if ln.startswith(date_prefix):
            lines[i] = line
            action = "edit"
            break
    else:
        # 2) 找当月小节，插到它最后一条后面
        idx = next((i for i, ln in enumerate(lines) if ln.strip() == month), None)
        if idx is None:
            # 3) 没有当月小节 → 建一个，放在 `## 写入口径` 之后、最早的月小节之前
            first_month = next(
                (i for i, ln in enumerate(lines) if re.match(r"^## \d{4}-\d{2}$", ln.strip())),
                None,
            )
            block = [month, "", line, ""]
            if first_month is None:
                lines += block
            else:
                lines[first_month:first_month] = block
            action = "add-month"
        else:
            end = next(
                (
                    i
                    for i in range(idx + 1, len(lines))
                    if lines[i].startswith("## ") or lines[i].startswith("<!--")
                ),
                len(lines),
            )
            insert_at = end
            while insert_at > idx + 1 and not lines[insert_at - 1].strip():
                insert_at -= 1
            lines.insert(insert_at, line)

    text = "\n".join(lines)
    text = re.sub(r"(?m)^updated: .*$", "updated: %s" % d.isoformat(), text, count=1)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write(text)
    return action


def sync_one(d, dry=False):
    path = diary_path(d)
    info = parse(path)
    if info is None:
        print("跳过 %s：没有日记文件" % d.isoformat())
        return False
    line = render(d, summarize(d, info))
    if dry:
        print("[dry] " + line)
        return True
    action = upsert(line, d)
    print("%-10s %s" % (action, line))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD，默认今天")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--all-missing", action="store_true", help="补最近 14 天里 11 没写的日子")
    a = ap.parse_args()

    if a.all_missing:
        today = datetime.date.today()
        n = 0
        with open(LOG, encoding="utf-8") as f:
            existing = f.read()
        for i in range(14, -1, -1):
            d = today - datetime.timedelta(days=i)
            if ("- %s（周" % d.isoformat()) in existing:
                continue
            n += 1 if sync_one(d, a.dry) else 0
        print("共处理 %d 天；记得跑 python tools/memdb.py index" % n)
        return

    d = datetime.date.fromisoformat(a.date) if a.date else datetime.date.today()
    if sync_one(d, a.dry):
        if not a.dry:
            print("记得跑 python tools/memdb.py index")


if __name__ == "__main__":
    sys.exit(main())
