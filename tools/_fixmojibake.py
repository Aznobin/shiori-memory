#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性修复：PS 5.1 的 Get-Content 按 GBK 读 UTF-8 文件后再写回，造成双重编码。

判定：把文本按 gbk 编码再按 utf-8 解码，能成功且结果里出现常见中文，就说明是双重编码。
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROBE = "的记忆the_一个"


def untangle(text: str) -> str | None:
    try:
        fixed = text.encode("gbk").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    if not fixed:
        return None
    good = sum(1 for ch in fixed if "\u4e00" <= ch <= "\u9fff")
    if good < 20:
        return None
    return fixed


def main() -> int:
    for raw in sys.argv[1:]:
        p = Path(raw)
        if not p.exists():
            print(f"跳过（不存在）：{p}")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        fixed = untangle(text)
        if fixed is None:
            print(f"不是双重编码，不动：{p}")
            continue
        p.write_text(fixed, encoding="utf-8")
        print(f"已修复：{p}（{len(text)} 字 → {len(fixed)} 字）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
