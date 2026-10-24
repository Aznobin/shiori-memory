#!/usr/bin/env python3
"""frag_eval.py —— 片段层命中率自测。

做法：随机抽 N 个片段，用它的标题/关键词造查询（模拟"她提一嘴"），
看检索能不能把原片段排进前 1 / 3 / 5 名，顺带算 MRR。
这是自检索（self-retrieval），衡量的是索引与打分的质量，不是她的真实问法，
但能稳定发现"切得太碎、关键词没进索引、打分偏到主片段"这类退化。
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frag  # noqa: E402


def make_query(item: dict, df: dict[str, int]) -> str:
    """从片段里挑「稀有词」当查询——模拟她提一个具体说法，而不是模块级大词。"""
    rare = [t for t in item.get("tokens", [])
            if 2 <= len(t) <= 8 and df.get(t, 0) <= 2 and not t.isdigit()]
    random.shuffle(rare)
    if rare:
        return " ".join(rare[:2])
    words = [w for w in item["keywords"] if 2 <= len(w) <= 8]
    random.shuffle(words)
    return " ".join(words[:2]) if words else item["title"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    random.seed(args.seed)
    idx = frag.load_index()
    idf = frag.idf_map(idx)
    df: dict[str, int] = {}
    for i in idx:
        for t in set(i.get("tokens", [])):
            df[t] = df.get(t, 0) + 1
    sample = random.sample(idx, min(args.n, len(idx)))

    hits = {1: 0, 3: 0, 5: 0}
    mrr = 0.0
    misses = []
    for item in sample:
        q = make_query(item, df)
        qtok = frag.query_tokens(q)
        ranked = sorted(idx, key=lambda i: -frag.score(i, qtok, idf))
        pos = next((k + 1 for k, i in enumerate(ranked) if i["frag"] == item["frag"]), 999)
        for k in hits:
            if pos <= k:
                hits[k] += 1
        mrr += 1 / pos
        if pos > 3:
            misses.append((q, item["frag"], [i["frag"] for i in ranked[:3]]))

    n = len(sample)
    print(f"样本 {n} 条")
    for k in (1, 3, 5):
        print(f"  hit@{k}: {hits[k] / n:.0%}")
    print(f"  MRR: {mrr / n:.3f}")
    for q, want, got in misses[:6]:
        print(f"  漏: q={q!r} 期望 {want} 实际前3 {got}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
