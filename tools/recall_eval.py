#!/usr/bin/env python3
"""recall_eval.py —— 检索命中率评测：固定查询集 → recall@k / MRR。

「命中率不够用」得先能量出来，否则改打分等于瞎调参。这个脚本把 22 条真实问法
钉死成评测集（每条标注**应该命中的模块号**），跑一遍给三个数：

  recall@1 / recall@3 / recall@5   前 k 个结果里有没有该命中的模块
  MRR                              第一个正确结果排名的倒数均值

判定按**模块**不按片段：同一模块的片段算命中，因为读的时候读的是模块。

**两套口径，别混着报**（2026-10-21 prober 实测出来的）：
  默认        只量片段层 `frag.score` 排模块 —— 衡量「索引与打分本身」的质量
  `--live`    量线上真路径 `recall.retrieve()` —— 多三层：memdb 挑代表行、
              `BANNED_DOCS` / `BANNED_HINTS` 过滤、没字面命中的行打 0.25 折

同一批 22 题两套能差十几个点（实测 raw 90.9% / live 81.8%），所以**只报默认那套
不算数**，跟用户说「检索命中率」时得说清是哪一套。

用法：
  python tools/recall_eval.py                 # 片段层：总指标 + 未命中明细
  python tools/recall_eval.py --live          # 线上路径（recall.retrieve）
  python tools/recall_eval.py --show 3        # 外加每条查询的 top3
  python tools/recall_eval.py --k 8
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frag  # noqa: E402

# (问法, 应该命中的模块号)  —— 问法照用户平时怎么说话写，别写成关键词串
CASES: list[tuple[str, set[str]]] = [
    ("以前说的电脑休眠怎么弄", {"08"}),
    ("<项目> 单词分析做到哪一步了", {"07"}),
    ("我性格是怎样的", {"31"}),
    ("记忆库怎么备份换机器", {"91"}),
    # ⚠️ `22` 在 `recall.py` 的 `BANNED_DOCS` 里（sensitive: true，永不自动注入），
    # 所以这条在 `--live` 口径下**结构上不可能命中**，不是检索差。留着是因为
    # 片段层该能排到它（22 行都能被 frag 排第一），但看 live 数字时要把这条扣掉。
    # 「怎么练」既落在学习习惯（02 语言小课堂），也落在统考语法（27），两边都算命中
    ("输入法键位是怎么配的", {"08"}),
    ("推送失败怎么办", {"83", "91", "94"}),
    ("日记的格式要求", {"07"}),
    ("安全墙怎么防注入", {"96"}),
    ("上下文压缩和自动注入", {"93"}),
    ("重复犯的错记在哪", {"82"}),
]

# `--live` 口径下必然命不中的题（期望模块全被 BANNED 封掉），报数时扣掉
LIVE_DEAD = {"助手生气或者难受的时候"}

MODULE_FILE_RE = re.compile(r"^(\d{2}[a-z]?)-\S+\.md$")


def existing_modules() -> set[str]:
    """本库实际存在的模块号（拿 references 文件名，不猜）。

    2026-10-24 新设备演练发现：分发包里的评测题沿用主库的期望模块，
    22 条里 9 条的模块在包里根本不存在，跑出来的百分比天生是假的。
    """
    out: set[str] = set()
    for p in frag.REF.glob("*.md"):
        m = MODULE_FILE_RE.match(p.name)
        if m:
            out.add(m.group(1))
    return out



def rank_modules(query: str, idx=None, idf=None, avg=None, k: int = 5) -> list[str]:
    """片段层排模块（2026-10-22 起直接调 frag.module_scores，含人称加权）。

    参数保留是为了不破坏老调用点：真正打分已经全在 frag 里了，
    这里再抄一份就必然和线上漂。
    """
    return [m for m, _s in frag.module_scores(query)][:k]


def rank_live(query: str, k: int) -> list[str]:
    """线上路径：recall.retrieve → 按模块去重取前 k。"""
    import recall

    out, seen = [], set()
    for _s, d, _l, _t in recall.retrieve(query):
        m = str(d).split(".")[0].split("-")[0]
        if m in seen:
            continue
        seen.add(m)
        out.append(m)
        if len(out) >= k:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--live", action="store_true",
                    help="量线上路径 recall.retrieve（含挑行/过滤/0.25 折），而不是片段层打分")
    a = ap.parse_args()

    if a.live:
        idx = idf = avg = None
        evaluator = lambda q, k: rank_live(q, k)  # noqa: E731
    else:
        idx = frag.load_index()
        idf = frag.idf_map(idx)
        avg = frag.avg_len(idx)
        evaluator = lambda q, k: rank_modules(q, idx, idf, avg, k)  # noqa: E731

    hits = {1: 0, 3: 0, 5: 0}
    mrr = 0.0
    misses = []
    dead = 0
    have = existing_modules()
    scorable = [(q, w) for q, w in CASES if w & have]
    skipped = [(q, w) for q, w in CASES if not (w & have)]
    for query, want in scorable:
        ranked = evaluator(query, max(a.k, 5))
        rank = next((i + 1 for i, m in enumerate(ranked) if m in want), None)
        if rank:
            mrr += 1.0 / rank
            for kk in hits:
                if rank <= kk:
                    hits[kk] += 1
        else:
            if a.live and query in LIVE_DEAD:
                dead += 1
            misses.append((query, want, ranked[:5]))
        if a.show:
            print(f"  {query}  →  {' '.join(ranked[:a.show])}")
    n = len(scorable)
    tag = "线上路径 recall.retrieve" if a.live else "片段层 frag.score"
    print(f"评测 {len(CASES)} 条（{tag}）")
    if skipped:
        missing = sorted({m for _q, w in skipped for m in (w - have)})
        print(f"  ⚠️ SKIP {len(skipped)} 条：期望模块本库不存在，不进分母"
              f"（缺 {', '.join(missing)}）")
        for _q, _w in skipped:
            print(f"      - {_q}  该中 {'/'.join(sorted(_w))}")
    print(f"  可评分 {n} 条")
    print(f"  recall@1  {hits[1]}/{n}  {(hits[1] / n if n else 0):.1%}")
    print(f"  recall@3  {hits[3]}/{n}  {(hits[3] / n if n else 0):.1%}")
    print(f"  recall@5  {hits[5]}/{n}  {(hits[5] / n if n else 0):.1%}")
    print(f"  MRR       {(mrr / n if n else 0):.3f}")
    if dead:
        eff = n - dead
        print(f"\n  注意：其中 {dead} 条期望模块被 `BANNED_DOCS` 永久屏蔽（结构上不可能命中），"
              f"扣掉后有效分母 {eff}：")
        print(f"     有效 recall@1  {hits[1]}/{eff}  {hits[1] / eff:.1%}")
        print(f"     有效 recall@3  {hits[3]}/{eff}  {hits[3] / eff:.1%}")
        print(f"     有效 MRR       {mrr / eff:.3f}")
    if misses:
        print("未命中：")
        for query, want, got in misses:
            print(f"  ✗ {query}  该中 {'/'.join(sorted(want))}  实得 {' '.join(got)}")
    if skipped:
        # 判据长牙的地方：评测集与库不配时分数天生是假的，巡检必须能红。
        print(f"\n✗ 退出码 1：{len(skipped)} 条评测题的期望模块本库不存在，"
              f"这份分数不能上报（先修评测集或库，别改这条判据）")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

