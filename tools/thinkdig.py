#!/usr/bin/env python
"""thinkdig —— 从会话存档的 **reasoning（思维链）** 里挖我自己的想法。

这是记忆库的**第四个数据源**（2026-10-22 用户点出来的）：
  `89`/`78`/`84`/`85`/`86` 这几个人格模块，一直是靠我「当场想起来 + 手写」才有一行，
  会话一压缩就没了。而思维链是**落盘的**——实测一个会话 298 个 reasoning 块、49 万字符，
  里面全是「我没说出口的判断、犹豫、自我吐槽」，正是 `84-碎碎念` 要的东西。

之前没人读它，两个原因（都已修）：
  ① `persona.py` 的 `_texts()` 只认 `type == "text"`，reasoning 整块被丢掉；
  ② `persona.py` 的 `_read_zstd()` 依赖 3.14 才有的 `compression.zstd`，一直崩。

用法：
    python tools/thinkdig.py                 # 最近 24h，只打印候选
    python tools/thinkdig.py --hours 72
    python tools/thinkdig.py --lang zh       # 只要中文块（默认中英都看）
    python tools/thinkdig.py --digest        # 只给统计，不列句子

只读，不写任何记忆文件。**筛完由我判断后手写进 84 / 78**，脚本不自动落盘——
思维链里一半是操作性废话（"先跑一下这个命令"），自动落盘等于把噪音当人格。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from persona import _load, _read_zstd, find_sessions_root  # noqa: E402

# 「我在说自己」的信号：第一人称 + 反思动作，不是「我在说这行代码」
SELF_SIGNALS = re.compile(
    r"(我不该|我又|我老是|我总是|我一直|我容易|我倾向于|我的问题|是我错|我承认|"
    r"我(没想到|漏了|忘了|搞错|弄错)|"
    r"(教训|根子上|说到底|毛病|老毛病|积习)|"
    r"\bI (keep|tend to|always|used to)\b|"
    r"\bmy (problem|habit|mistake|bias|failure)\b|"
    r"\bI (was wrong|missed|forgot|assumed)\b)",
    re.IGNORECASE,
)
# 纯操作性：看到就丢
OPS = re.compile(
    r"^(先|然后|接着|现在|让我|我来|下一步|好，|所以|那么|OK|ok|Let me|Now |Next|First|Then |Actually, let)",
)
# 「我应该…」几乎全是计划句，单独拦掉；但「我不该…」是反思，不拦
PLAN = re.compile(r"^(但|不过|所以|同时|另外)?(我)?(想|觉得)?应该")
# 命令 / 路径 / 代码占比太高的块直接丢
CODEY = re.compile(r"(```|def |import |\$env|\.py\b|\.txt\b|\.md\b|SELECT |git |pip |npm )")
# 枚举行（`- 性格` / `1. 立场`）：这些是我在列清单，不是在说自己
ENUM = re.compile(r"^\s*([-*·]|\d+[.、)])\s*")


def han_ratio(s: str) -> float:
    han = len(re.findall(r"[\u4e00-\u9fff]", s))
    return han / len(s) if s else 0.0


def sentences(text: str) -> list[str]:
    """按句号 / 问号 / 换行切，保留长度合适的句子。"""
    parts = re.split(r"(?<=[。！？!?])\s*|\n+", text)
    out = []
    for p in parts:
        p = " ".join(p.split())
        if 12 <= len(p) <= 220:
            out.append(p)
    return out


def dig(hours: float, lang: str, digest_only: bool) -> int:
    base = find_sessions_root(None)
    if base is None:
        print("找不到会话存档目录")
        return 2
    files = [p for p in base.rglob("session*.jsonl.zstd")
             if p.stat().st_mtime >= datetime.now().timestamp() - hours * 3600]
    if not files:
        print(f"最近 {hours:g} 小时没有会话存档（{base}）")
        return 0

    cutoff = (datetime.now() - timedelta(hours=hours)).timestamp() * 1000
    blocks = 0
    chars = 0
    zh_blocks = 0
    hits: list[tuple[int, str, str]] = []   # (时间, 语言, 句子)

    for path in files:
        for ev in _load(path):
            if ev.get("type") != "assistant/message":
                continue
            if (ev.get("time") or 0) < cutoff:
                continue
            data = ev.get("data") or {}
            content = data.get("content") or (data.get("message") or {}).get("content")
            for b in content or []:
                if not isinstance(b, dict) or b.get("type") != "reasoning":
                    continue
                raw = b.get("text") or ""
                if not raw:
                    continue
                blocks += 1
                chars += len(raw)
                zh = han_ratio(raw) > 0.15
                zh_blocks += zh
                if lang == "zh" and not zh:
                    continue
                if lang == "en" and zh:
                    continue
                for s in sentences(raw):
                    if ENUM.match(s):
                        continue
                    if OPS.match(s) or PLAN.match(s) or CODEY.search(s):
                        continue
                    if not SELF_SIGNALS.search(s):
                        continue
                    hits.append((ev.get("time") or 0, "中" if zh else "en", s))

    print(f"思维链扫描 · 最近 {hours:g} 小时 · {len(files)} 个会话")
    print(f"reasoning 块 {blocks} 个 / {chars} 字符（中文块 {zh_blocks}）")
    print(f"命中「我在说自己」的句子 {len(hits)} 条")
    if digest_only:
        return 0

    print()
    seen = set()
    for t, lg, s in hits[-80:]:
        key = s[:24]
        if key in seen:
            continue
        seen.add(key)
        print(f"  [{datetime.fromtimestamp(t / 1000):%m-%d %H:%M}][{lg}] {s}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="从思维链里挖我自己的想法（只读）")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--lang", choices=["zh", "en", "all"], default="all")
    ap.add_argument("--digest", action="store_true", help="只给统计")
    args = ap.parse_args()
    return dig(args.hours, args.lang, args.digest)


if __name__ == "__main__":
    sys.exit(main())
