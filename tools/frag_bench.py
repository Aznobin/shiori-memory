#!/usr/bin/env python
"""frag_bench.py —— 真实问题的检索验收（不是自造查询）。

用法：python tools/frag_bench.py [-k 3] [--show-miss]
数据：tools/frag_bench.json（自己准备，不进空壳包），每条 {q: 用户的真实问法, want: [答案里必须出现的关键词]}
判定：前 k 名里只要有片段正文/标题含 want 里任一词，就算命中。
"""
from __future__ import annotations

import argparse
import json
import sys

import frag

BENCH = frag.ROOT / "tools" / "frag_bench.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", type=int, default=3, help="看前几名")
    ap.add_argument("--show-miss", action="store_true", help="打印漏掉的候选")
    args = ap.parse_args()

    if not BENCH.exists():
        print(f"缺数据文件 {BENCH}：按 {{q, want}} 格式自己造几条真实问法再跑")
        return 1
    cases = json.loads(BENCH.read_text(encoding="utf-8"))
    idx = frag.load_index()
    idf = frag.idf_map(idx)

    hit = 0
    for case in cases:
        q = case["q"]
        want = [w.lower() for w in case["want"]]
        qtok = frag.query_tokens(q)
        ranked = sorted(idx, key=lambda i: -frag.score(i, qtok, idf))[: args.k]
        got = []
        ok = False
        for item in ranked:
            text = (frag.ROOT / item["file"]).read_text(encoding="utf-8").lower()
            title = item["title"].lower()
            if any(w in text or w in title for w in want):
                ok = True
            got.append(item["frag"])
        hit += 1 if ok else 0
        mark = "OK " if ok else "MISS"
        print(f"{mark} {q}  → {got}")
        if not ok and args.show_miss:
            for f in got:
                item = next(i for i in idx if i["frag"] == f)
                print(f"      {f} {item['title']}")

    n = len(cases)
    print(f"\n真实问题命中率（前 {args.k}）：{hit}/{n} = {hit / n:.0%}", file=sys.stderr)
    return 0 if hit == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
