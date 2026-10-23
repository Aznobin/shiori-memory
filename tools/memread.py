#!/usr/bin/env python3
"""按大类整篇读记忆（2026-10-20 她要求：别切片了，大类找，整篇读）。

用法（技能根目录）：
    python tools\\memread.py "项目进度"        # 命中的大类 + 模块全文
    python tools\\memread.py --list            # 只列大类表
    python tools\\memread.py "吃什么" --quiet  # 只报命中情况，不打印正文

为什么不用片段层：切片靠关键词匹配，词面不对就零命中；大类 + 整篇读，
少了「切错、搜不到」这一整类故障，代价是 token——她明确说不再省这个。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import cfg  # noqa: E402
from routes import ROUTES  # noqa: E402

REF = Path((cfg.get("paths") or {}).get("skill") or Path(__file__).resolve().parent.parent) / "references"


def parse_meta(text: str) -> dict:
    meta: dict = {}
    if not text.startswith("---"):
        return meta
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    return meta


def module_files() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in sorted(REF.glob("*.md")):
        meta = parse_meta(p.read_text(encoding="utf-8", errors="replace"))
        mid = (meta.get("id") or p.name.split("-")[0]).strip('"')
        out.setdefault(mid, p)
    return out


STOP = {"什么", "怎么", "我的", "我是", "是的", "是不", "吗", "呢", "啦", "的", "我", "是", "有", "没"}


def tokens_of(q: str) -> list[str]:
    """按空格切 + 中文二元组展开；去掉「什么/我/是」这类高频虚词，避免假命中。"""
    parts = q.replace("，", " ").replace("。", " ").replace("？", " ").replace("！", " ").split()
    out: list[str] = []
    for t in parts:
        t = t.strip()
        if not t:
            continue
        out.append(t)
        if len(t) >= 3 and not t.isascii():
            out.extend(t[i:i + 2] for i in range(len(t) - 1))
    return [t for t in out if t not in STOP and len(t) >= 2]


def score_route(query_tokens: list[str], cat: str, what: str, how: str, mod_ids: list[str], files: dict[str, Path]) -> float:
    """大类名 / 模块标题 / keywords 权重 3，正文权重 1。"""
    head = f"{cat} {what} {how}".lower()
    bodies = ""
    titles = ""
    for mid in mod_ids:
        p = files.get(mid)
        if not p:
            continue
        body = p.read_text(encoding="utf-8", errors="replace")
        meta = parse_meta(body)
        titles += " " + (meta.get("title") or p.stem) + " " + meta.get("keywords", "")
        bodies += "\n" + body
    titles, bodies = titles.lower(), bodies.lower()
    score = 0.0
    for t in query_tokens:
        tl = t.lower()
        score += 3.0 * (head.count(tl) + titles.count(tl))
        score += 1.0 * bodies.count(tl)
    return score


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--list":
        for cat, mods, what, how in ROUTES:
            print(f"{cat}\t{mods}\t{what}\t{how}")
        return 0

    quiet = "--quiet" in argv
    query = " ".join(a for a in argv if not a.startswith("--"))
    qts = tokens_of(query)
    files = module_files()

    ranked = []
    for cat, mods, what, how in ROUTES:
        ids = [m.strip("` ") for m in mods.split("`") if m.strip("` ").isdigit() or m.strip("` ")[:1].isdigit()]
        route_text = f"{cat} {what} {how}"
        ranked.append((score_route(qts, cat, what, how, ids, files), cat, ids, what, how))
    ranked.sort(reverse=True)
    best = [r for r in ranked if r[0] >= 3][:2]

    if not best:
        print(f"没记：查「{query}」，大类表零命中（先按字面说没记，不编）")
        return 1

    for sc, cat, ids, what, how in best:
        print(f"## 大类：{cat}（匹配 {sc:.0f}）—— {what}")
        for mid in ids:
            p = files.get(mid)
            if not p:
                continue
            body = p.read_text(encoding="utf-8", errors="replace")
            meta = parse_meta(body)
            print(f"\n### [{mid}] {meta.get('title') or p.stem}  ({p})")
            if not quiet:
                print(body.rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
