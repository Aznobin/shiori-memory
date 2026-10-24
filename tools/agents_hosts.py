#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按主机名把 `templates/hosts/<主机名>.md` 同步到本机工作区 `AGENTS.md`。

用法：
  python tools/agents_hosts.py              # 把本机那份写到工作区（工作区有差异才写）
  python tools/agents_hosts.py --dry        # 只报差异，不写
  python tools/agents_hosts.py --save       # 反向：把本机工作区存回 hosts/<主机名>.md
  python tools/agents_hosts.py --list       # 列出已有主机备份
  python tools/agents_hosts.py --hosts A,B  # 多台一起改时，把 A 的规则并进 B（同一段规则同步到所有主机）

为什么要有这个（2026-10-22 用户提的）：
  用户原话「skill中存三个，一个框架，一个笔记本一个itx，然后要改一起改」。
  `AGENTS.md` 分三份：`templates/AGENTS.md` 是空框架（给人起步用），
  `templates/hosts/<本机名>.md`（笔记本）与 `templates/hosts/<host>.md`（台式 ITX）是两台机器的完整版，
  全在 git 里；改规则时三份一起改，换机器只要跑一次本脚本把对应那份铺下去。
  主机名不认识的机器，用 `--save` 先存一份，下次就有它了。
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import cfg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOSTS = ROOT / "templates" / "hosts"


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def host_name() -> str:
    return socket.gethostname()


def host_file(name: str) -> Path:
    return HOSTS / f"{name}.md"


def workspace_file() -> Path | None:
    ws = (cfg.get("paths") or {}).get("workspace") or ""
    if ws:
        return Path(ws) / "AGENTS.md"
    return None


def same(a: str, b: str) -> bool:
    return a.replace("\r\n", "\n") == b.replace("\r\n", "\n")


def main() -> int:
    argv = sys.argv[1:]
    hosts = sorted(p.stem for p in HOSTS.glob("*.md")) if HOSTS.is_dir() else []

    if "--list" in argv:
        cur = host_name()
        for h in hosts:
            print(("→ " if h.lower() == cur.lower() else "  ") + h)
        print(f"本机：{cur}")
        return 0

    ws = workspace_file()
    if ws is None:
        print("memory.config.json 没填 paths.workspace")
        return 1

    cur = host_name()
    matches = [h for h in hosts if h.lower() == cur.lower()]
    if "--save" in argv:
        ws_text = read(ws)
        if not ws_text:
            print(f"工作区没有 AGENTS.md：{ws}")
            return 1
        HOSTS.mkdir(parents=True, exist_ok=True)
        target = host_file(matches[0] if matches else cur)
        target.write_text(ws_text, encoding="utf-8")
        print(f"已存回 {target}")
        return 0

    if not matches:
        print(f"没有本主机（{cur}）的备份。先跑 `--save` 存一份，或从别的机器拷一份进 {HOSTS}")
        print(f"现有：{', '.join(hosts) or '(空)'}")
        return 1
    src = host_file(matches[0])
    want = read(src)
    have = read(ws)
    if same(want, have):
        print(f"{ws} 已与 hosts/{matches[0]}.md 一致，没动")
        return 0
    if "--dry" in argv:
        print(f"{ws} 与 hosts/{matches[0]}.md 有差异（{len(have.splitlines())} 行 → {len(want.splitlines())} 行）")
        return 0
    ws.parent.mkdir(parents=True, exist_ok=True)
    ws.write_text(want, encoding="utf-8")
    print(f"{ws} ← hosts/{matches[0]}.md（{len(want.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
