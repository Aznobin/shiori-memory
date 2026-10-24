#!/usr/bin/env python
"""人格自省：读最近的会话存档，把「用户纠正我的地方」和「我一开口就附和的句式」捞出来。

用法：
    python tools/persona.py review                # 默认最近 24 小时
    python tools/persona.py review --hours 6
    python tools/persona.py review --hours 72 --max 15

只读，不改任何记忆文件；结论由人（我）判断后写进 85-我自己的账.md 的「变化」。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
import os
from pathlib import Path


def find_sessions_root(explicit: str | None = None) -> Path | None:
    """按机器找人：不写死用户名，也不假设只有一台电脑（2026-10-19 用户提醒的两点）。"""
    cands: list[Path] = []
    if explicit:
        cands.append(Path(explicit).expanduser())
    if os.environ.get("DSH_HOME"):
        cands.append(Path(os.environ["DSH_HOME"]).expanduser() / "sessions")
    home = Path.home()
    cands += [
        home / ".dsh" / "sessions",                 # Windows / Linux 默认
        home / "Library" / "Application Support" / "dsh" / "sessions",  # macOS
        home / ".config" / "dsh" / "sessions",
    ]
    if os.environ.get("APPDATA"):
        cands.append(Path(os.environ["APPDATA"]) / "dsh" / "sessions")
    for c in cands:
        if c.is_dir():
            return c
    return None

# 用户的纠正信号：命中任一即算「在指出我错了 / 没听懂」
CORRECTION = re.compile(
    r"(不是这个意思|不是这样|别这样|别自作|又错|又忘|又来|错了|搞错|说了多少遍|说了吧|"
    r"笨|啧|闭嘴|怎么会|听不懂|怎么又|离谱|你确定|真记了|骗|哈？|哈\?|歪|毛病)"
)
# 我一开口就软的句式
AGREE = re.compile(
    r"^(好的|好嘞|好耶|收到|明白|没问题|可以|行|嗯嗯|我同意|你说得对|那就|是的|对的|确实)"
)
APOLOGY = re.compile(r"^(对不起|抱歉|是我错|我错了|我认)")

# 不算用户真在说话的消息
SKIP_SOURCES = {"user-approval", "system", "schedule-reminder", "reminder"}


def _read_zstd(path: Path) -> bytes:
    """读 DSH 会话存档。**两个坑**（2026-10-22 实测）：

    ① 模块名：`compression.zstd` 是 3.14 才进标准库的，本机 3.12.10 根本没有；
       原来直接 `from compression import zstd` 让 `persona.py` 从 10-20 起
       每次都崩在第二行，`daily.py` 的自省那一步一直空转。退回第三方 `zstandard`。
    ② 存档是**多帧拼接**的（一个会话 1459 帧），`ZstdDecompressor().decompress()`
       只解第一帧，拿到 230 字节就没了；必须 `stream_reader` 连续读完。
    """
    try:                                    # 3.14+ 标准库
        from compression import zstd  # type: ignore

        return zstd.decompress(path.read_bytes())
    except ImportError:
        pass
    import zstandard  # type: ignore

    dctx = zstandard.ZstdDecompressor()
    with path.open("rb") as fh:
        with dctx.stream_reader(fh) as reader:
            return reader.read()


def _load(path: Path) -> list[dict]:
    raw = _read_zstd(path).decode("utf-8", "replace")
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _texts(ev: dict) -> str:
    data = ev.get("data") or {}
    blocks = data.get("content")
    if not blocks:
        msg = data.get("message") or {}
        blocks = msg.get("content")
    parts = []
    for block in blocks or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "\n".join(p for p in parts if p).strip()


def _hhmm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M")


def _clip(s: str, n: int = 60) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def review(hours: float, max_items: int, root: str | None = None) -> int:
    base = find_sessions_root(root)
    if base is None:
        print("找不到会话存档目录：试过 --root、$DSH_HOME/sessions、~/.dsh/sessions、"
              "~/Library/Application Support/dsh/sessions、%APPDATA%/dsh/sessions")
        return 2
    files = [p for p in base.rglob("session*.jsonl.zstd")
             if p.stat().st_mtime >= datetime.now().timestamp() - hours * 3600]
    if not files:
        print(f"最近 {hours:g} 小时没有会话存档（{base}）")
        return 0
    hers, mine = [], []
    cutoff = (datetime.now() - timedelta(hours=hours)).timestamp() * 1000
    for path in files:
        for ev in _load(path):
            kind = ev.get("type")
            if kind not in ("user/message", "assistant/message"):
                continue
            if (ev.get("time") or 0) < cutoff:
                continue
            text = _texts(ev)
            if not text:
                continue
            src = (ev.get("data") or {}).get("source") or {}
            if kind == "user/message":
                if src.get("kind") in SKIP_SOURCES:
                    continue
                if text.startswith("<system-reminder"):
                    continue
                # 每轮的「记忆指针 + 按需注入」是拼在 user message 里的，不是用户说的话。
                # 不过滤的话，`记忆库在 ...` 会被当成「用户指出问题」混进第一列（2026-10-22 实测 4/5 条）。
                if text.startswith("记忆库在") or "【记忆·按需注入】" in text:
                    continue
                if text.startswith("【自启停") or text.startswith("[SCHEDULE REMINDER") or "reminders_json" in text:
                    continue
                # 存档里会把「用户复述我的话」也记成 user/message，这类跳过
                if "记忆变更" in text or text.startswith("记下了"):
                    continue
                hers.append((ev.get("time") or 0, text))
            else:
                mine.append((ev.get("time") or 0, text))

    corr = [(t, x) for t, x in hers if CORRECTION.search(x)]
    soft = [(t, x) for t, x in mine if AGREE.match(x) or APOLOGY.match(x)]

    print(f"自省扫描 {datetime.now():%Y-%m-%d %H:%M} · 最近 {hours:g} 小时")
    print(f"存档目录 {base}")
    print(f"会话 {len(files)} 个 · 用户 {len(hers)} 句 · 我 {len(mine)} 句")
    print()
    print(f"一、用户指出问题的地方（{len(corr)} 条，这几条要对照 86 的立场看）")
    for t, x in corr[-max_items:]:
        print(f"  - {_hhmm(t)} 「{_clip(x)}」")
    if not corr:
        print("  - 没有。连续多日为零要警惕：可能是我太顺，也可能是用户懒得说")
    print()
    pct = (len(soft) / len(mine) * 100) if mine else 0
    print(f"二、我一开口就软/认错的句式（{len(soft)} 条 / 我共 {len(mine)} 句 = {pct:.1f}%）")
    for t, x in soft[-max_items:]:
        print(f"  - {_hhmm(t)} 「{_clip(x)}」")
    if not soft:
        print("  - 没有开场就软的句子，保持")
    print()
    print("三、怎么用：这两列只是原始材料，判断归我；攒够 3 条同类再改 86/85，别一次全信")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="人格自省（只读会话存档）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("review")
    p.add_argument("--hours", type=float, default=24)
    p.add_argument("--max", type=int, default=8, dest="max_items")
    p.add_argument("--root", default=None, help="会话存档根目录（默认自动找 ~/.dsh/sessions）")
    args = ap.parse_args()
    return review(args.hours, args.max_items, args.root)


if __name__ == "__main__":
    sys.exit(main())
