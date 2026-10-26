#!/usr/bin/env python3
"""骨架完整性检查：对照 SKELETON.sha256，看骨架有没有被改过。

骨架 = 作者的东西，不是使用者的地：
    tools/  bin/  templates/  framework-public/  AGENTS.md  SKILL.md
    setup.ps1  VERSION  config.example.json  references/9*.md（协议件）

你自己在 references/ 里建的模块不在骨架里，随便改。
你自己在 tools/private/ 里放的脚本也不在骨架里，随便改。

用法：
    python tools/skeleton_guard.py           # 检查；被动过就逐条列出并 exit 1
    python tools/skeleton_guard.py --quiet   # 只打一行结论

这个脚本只读不写：它不会「顺手」把清单重新生成一遍，否则谁改完跑一次就自动盖章了。
要重新生成清单只有一条路——重新打包（作者那边）。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "SKELETON.sha256"
SKELETON_DIRS = ("tools", "bin", "templates", "framework-public")
SKELETON_FILES = ("AGENTS.md", "SKILL.md", "setup.ps1", "VERSION", "config.example.json")


def sha_of(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest() -> dict[str, str]:
    want: dict[str, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and len(parts[0]) == 64:
            want[parts[1].strip().lstrip("*")] = parts[0]
    return want


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="骨架完整性检查（对照 SKELETON.sha256）")
    ap.add_argument("--quiet", action="store_true", help="只打一行结论")
    ap.add_argument("--root", help="库根（默认脚本上一级）")
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else ROOT
    manifest = root / "SKELETON.sha256"
    if not manifest.exists():
        print("没有 SKELETON.sha256：这份不是分发包（或者清单被删了），跳过检查")
        return 0

    want = load_manifest()
    if not want:
        print(f"SKELETON.sha256 是空的或格式不对：{manifest}")
        return 1

    changed: list[str] = []
    missing: list[str] = []
    for rel, old in sorted(want.items()):
        f = root / rel
        if not f.exists():
            missing.append(rel)
        elif sha_of(f) != old:
            changed.append(rel)

    known = set(want)
    extra: list[str] = []
    for d in SKELETON_DIRS:
        base = root / d
        if not base.exists():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file():
                continue
            if f.relative_to(root).parts[:2] == ("tools", "private"):
                continue
            rel = f.relative_to(root).as_posix()
            if rel not in known:
                extra.append(rel)
    for f in sorted((root / "references").glob("9*.md")):
        rel = f.relative_to(root).as_posix()
        if rel not in known:
            extra.append(rel)

    ok = not (changed or missing or extra)
    if args.quiet:
        print("✓ 骨架未改" if ok else f"✗ 骨架被动过：改 {len(changed)} / 缺 {len(missing)} / 多 {len(extra)}")
        return 0 if ok else 1

    if ok:
        print(f"✓ 骨架未改（{len(want)} 项，对照 SKELETON.sha256）")
        return 0

    print(f"✗ 骨架被动过（清单 {len(want)} 项）")
    if changed:
        print(f"\n被改过（{len(changed)}）——这些是作者的文件，改了就收不到下一次升级：")
        for rel in changed:
            print(f"  ~ {rel}")
    if missing:
        print(f"\n不见了（{len(missing)}）：")
        for rel in missing:
            print(f"  - {rel}")
    if extra:
        print(f"\n多出来的（{len(extra)}）——骨架目录里不该有自己的文件，你的东西放 references/：")
        for rel in extra:
            print(f"  + {rel}")
    print("\n要恢复：从发行包重新解压覆盖（或跑 python tools/update.py apply 换骨架）。")
    print("确实要改骨架：先问库的主人，得到明确同意再动手。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
