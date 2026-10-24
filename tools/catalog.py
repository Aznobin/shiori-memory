#!/usr/bin/env python3
"""生成 references/CATALOG.md —— 记忆库一页总览。

换设备或新会话时先读 CATALOG，就能看清每个模块装什么、
多大、最近改过没，不用打开任何模块正文。

用法：
    python tools/catalog.py          # 重新生成 CATALOG.md
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "references"
OUT = REF / "CATALOG.md"


def parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        return {}
    fm = m.group(1)
    data = {}
    for key in ("id", "title", "updated"):
        mm = re.search(rf"^{key}:\s*[\"']?(.+?)[\"']?\s*$", fm, re.M)
        if mm:
            data[key] = mm.group(1).strip()
    # keywords 支持列表与行内数组两种写法
    kw = []
    mm = re.search(r"^keywords:\s*\[(.*?)\]", fm, re.M)
    if mm:
        kw = [x.strip().strip("\"'") for x in mm.group(1).split(",") if x.strip()]
    else:
        mm = re.search(r"^keywords:\s*\r?\n((?:\s*-\s*.*\r?\n?)+)", fm, re.M)
        if mm:
            kw = [x.strip().lstrip("- ").strip("\"'")
                  for x in mm.group(1).splitlines() if x.strip()]
    data["keywords"] = kw
    return data


# 与 tools/memory_check.py 的 module_files 用同一份排除表，两个脚本的「模块数」必须相等
# 2026-10-24：`_合并候选.md` 是 `decay.py --pairs` 的生成物，memory_check / routes / pack 早就排了，
# 这里漏一个 → CATALOG 数出 24、体检数出 23，两页打架。补上后三处同为 23。
EXCLUDE = ("_模板.md", "CATALOG.md", "_复核清单.md", "_巡检记录.md", "_路由详表.md",
           "_合并候选.md")


def line_count(text: str) -> int:
    """与 tools/memory_check.py 的 line_count 同款：尾部空行不算，两种口径必须一致。"""
    body = text.replace("\r\n", "\n").rstrip("\n")
    return 0 if not body else len(body.split("\n"))


def main() -> None:
    rows = []
    for p in sorted(REF.glob("*.md")):
        if p.name in EXCLUDE:
            continue
        text = p.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        lines = line_count(text)
        title = fm.get("title") or p.stem
        kw = "、".join(fm.get("keywords", [])[:4])
        upd = fm.get("updated", "—")
        size = len(text.encode("utf-8")) // 1024
        rows.append((p.stem, title, kw, lines, f"{size}KB", upd))

    head = [
        "# 记忆库总览（CATALOG）",
        "",
        "> 本文件由 `tools/catalog.py` 自动生成，别手改；改完模块跑一次脚本刷新。",
        "> 用途：换设备或新会话开局读这一页就够导航，不用打开模块正文。",
        "",
        f"模块数：{len(rows)}　生成时间：{__import__('datetime').date.today().isoformat()}",
        "",
        "| 模块 | 标题 | 关键词 | 行数 | 大小 | 更新 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    body = [f"| `{n}` | {t} | {k} | {ln} | {s} | {u} |" for n, t, k, ln, s, u in rows]
    OUT.write_text("\n".join(head + body) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(rows)} modules)")


if __name__ == "__main__":
    main()
