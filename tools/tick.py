#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tick.py —— 巡检记录的「机器区」：一遍跑完体检 / 检索评测 / 仓库状态，把数字写进
`references/923-巡检记录.md` 的 TICK 区块，手改正本区块会被下次运行覆盖。

用法：
    python tools/tick.py           # 跑一遍并写回 923-巡检记录.md
    python tools/tick.py --dry     # 只打印，不落盘

为什么要有它（2026-10-24，大纲步骤 10）：`923-巡检记录.md` 的数字是人手抄的，
抄一次就再没对过——同一批数字在三处互相矛盾（19/22 86.4% vs 18/22 81.8%、
切片组 153 vs 152、提交 223 vs 242）。数字由脚本现跑现写，就不存在「抄错」。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import SKELETON_DIR  # noqa: E402

RECORD = SKELETON_DIR / "923-巡检记录.md"
BEGIN = "<!-- TICK:BEGIN 机器生成，手改会被下次运行覆盖 -->"
END = "<!-- TICK:END -->"


def run(args: list[str]) -> str:
    p = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (p.stdout or "") + (p.stderr or "")


def grab(pattern: str, text: str, default: str = "?") -> str:
    m = re.search(pattern, text)
    return m.group(1) if m else default


def collect() -> dict[str, str]:
    from concurrent.futures import ThreadPoolExecutor

    py = sys.executable
    d: dict[str, str] = {}
    # 2026-10-24：原来 7 个进程串行跑要 878ms，时间几乎全花在 Windows 的
    # Python 进程启动上（每个 ~50-120ms），跟内容量无关。这些命令互不依赖、
    # 全部只读（体检 / 统计 / lint / 评测 / 三条 git 查询），并发之后
    # 总耗时压到最慢那一个。
    jobs = {
        "check": [py, str(ROOT / "tools" / "memory_check.py")],
        "stats": [py, str(ROOT / "tools" / "frag.py"), "stats"],
        "lint": [py, str(ROOT / "tools" / "frag.py"), "lint"],
        "recall": [py, str(ROOT / "tools" / "recall_eval.py")],
        "commits": ["git", "rev-list", "--all", "--count"],
        "status": ["git", "status", "--porcelain"],
        "lr": ["git", "rev-list", "--left-right", "--count", "origin/main...HEAD"],
    }
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futures = {k: ex.submit(run, v) for k, v in jobs.items()}
        out = {k: futures[k].result() for k in jobs}

    check, stats, lint, recall = out["check"], out["stats"], out["lint"], out["recall"]
    d["硬错误"] = grab(r"❌ 必须修 \((\d+)\)", check, "0")
    d["建议看"] = grab(r"⚠️ 建议看 \((\d+)\)", check, "0")
    d["模块数"] = grab(r"模块 (\d+) 个", check)
    d["SKILL行"] = grab(r"SKILL\.md (\d+) 行", check)

    d["片段数"] = grab(r"片段 (\d+) 个", stats)
    d["片段模块"] = grab(r"片段 \d+ 个 · 模块 (\d+) 个", stats)

    d["lint问题"] = grab(r"问题 (\d+) 条", lint)

    d["recall@1"] = re.sub(r"\s+", " ", grab(r"recall@1(.*)", recall)).strip()
    d["recall@3"] = re.sub(r"\s+", " ", grab(r"recall@3(.*)", recall)).strip()
    d["recall@5"] = re.sub(r"\s+", " ", grab(r"recall@5(.*)", recall)).strip()
    d["MRR"] = grab(r"MRR\s+([\d.]+)", recall)

    d["提交"] = out["commits"].strip() or "?"
    st = out["status"].strip()
    d["工作区"] = "clean" if not st else f"{len(st.splitlines())} 个文件未提交"
    lr = out["lr"].strip()
    if lr and len(lr.split()) == 2:
        behind, ahead = lr.split()
        d["同步"] = "与 origin 一致" if behind == "0" and ahead == "0" else f"领先 {ahead} / 落后 {behind}"
    else:
        d["同步"] = "无法比对 origin"
    return d


def build_block(d: dict[str, str]) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = [
        ("运行时间", stamp),
        ("体检", f"硬错误 {d['硬错误']} / 建议看 {d['建议看']} 条"),
        ("模块 / 片段", f"{d['模块数']} 模块 / {d['片段数']} 片段（lint 问题 {d['lint问题']} 条）"),
        ("检索", f"recall@1 {d['recall@1']} · @3 {d['recall@3']} · @5 {d['recall@5']} · MRR {d['MRR']}"),
        ("SKILL.md", f"{d['SKILL行']} 行"),
        ("仓库", f"{d['提交']} 提交 · 工作区 {d['工作区']} · {d['同步']}"),
    ]
    lines = [BEGIN, "## 机器区（最近一次 `tools/tick.py`）", "",
             "> 这一段整块由脚本覆盖，别手改——手改的数字下次跑就被打回实测值。", "",
             "| 项 | 值 |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in rows]
    lines.append(END)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印，不写回")
    args = ap.parse_args()

    d = collect()
    block = build_block(d)
    print(block)

    if args.dry:
        print("\n（--dry：没有写回 %s）" % RECORD.name)
        return 0

    with open(RECORD, encoding="utf-8", newline="") as f:
        text = f.read()
    nl = "\r\n" if "\r\n" in text else "\n"
    block = block.replace("\n", nl)

    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        new = head + block + tail
    else:
        # 第一次跑：插在「最近 5 次」表格之前，intro 引用块之后
        m = re.search(r"(?m)^\| 日期 ", text)
        if not m:
            print(f"❌ {RECORD.name} 里找不到「| 日期 」表头，拒绝瞎插；先手工确认文件结构")
            return 1
        new = text[: m.start()] + block + nl + nl + text[m.start():]

    if new != text:
        with open(RECORD, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        print(f"\n✅ 已写回 {RECORD.name}（TICK 区块）")
    else:
        print(f"\n= 内容没变，{RECORD.name} 未改动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
