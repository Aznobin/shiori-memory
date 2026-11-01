#!/usr/bin/env python3
"""骨架完整性检查：对照 SKELETON.sha256，看骨架有没有被改过。

骨架 = 作者的东西，不是使用者的地：
    tools/  bin/  templates/  framework-public/  AGENTS.md  SKILL.md
    setup.ps1  VERSION  config.example.json  references/9*.md（协议件）

你自己在 references/ 里建的模块不在骨架里，随便改。
你自己在 tools/private/ 里放的脚本也不在骨架里，随便改。

跑出来的东西也不算改骨架：__pycache__/、*.pyc、memdb.sqlite3（连它的 -wal / -shm
兄弟）、.style_override.json —— 这些是本机自己生成的机器状态，你跑一次脚本就会冒出来，
不该为它报「骨架被动过」。

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

# 用户自己长出来的结构件：归属用户的库，不是骨架（真源 = tools/update.py 的 USER_OWNED）。
# 2026-11-02 实测：这些文件开箱后会被正常使用重写（921 追加日志 / 923 tick 重写 / 927 catalog
# 重算 / 928 routes 重算），签进清单就一路误报「骨架被动过」；而 pack.py 自 2026-11-02 起不再
# 把它们签进 SKELETON.sha256，于是又会被下面那条「references/9*.md 不在清单里就是多出来的」
# 误报成「多出来的」。两处都要认这份常量。
try:
    from update import USER_OWNED  # noqa: E402
except Exception:  # noqa: BLE001
    # 真源同 tools/update.py USER_OWNED（import 拿不到时的兜底，改动请只改那份）
    USER_OWNED = (
        "references/921-变更日志.md",
        "references/927-记忆库总览.md",
        "references/923-巡检记录.md",
        "references/922-待确认inbox.md",
        "references/928-路由详表.md",
        "references/924-合并候选清单.md",
        "references/926-模块标题.md",
    )

# 运行期产物：跑过一次脚本就在骨架目录里冒出来，全是本机生成的机器状态，不算「骨架被动过」。
# 2026-11-02 全新包实测：解压 + 跑一次 setup 就多出 7 个 tools/__pycache__/*.pyc 和
# 1 个 tools/memdb.sqlite3，整份包刚到手就报「多 8」。这里只忽略这些，别的照样查。
RUNTIME_DIRS = ("__pycache__",)
RUNTIME_SUFFIXES = (".pyc", ".pyo")
RUNTIME_FILES = ("memdb.sqlite3", ".style_override.json")


def is_runtime(rel: str) -> bool:
    """这一份是不是「跑出来的」：本机自己生成的状态，不是作者放进包里的东西。"""
    parts = rel.split("/")
    if any(p in RUNTIME_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    if name.endswith(RUNTIME_SUFFIXES):
        return True
    if name in RUNTIME_FILES or name.startswith("memdb.sqlite3-"):
        # memdb.sqlite3 的 -wal / -shm 兄弟跟它一起生成
        return True
    return False


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
        if rel in USER_OWNED:
            # 老包清单里可能还签着这些用户结构件；它们本来就该被用户重写，不算「被动过」
            continue
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
            if is_runtime(rel):
                continue
            if rel not in known:
                extra.append(rel)
    for f in sorted((root / "references").glob("9*.md")):
        rel = f.relative_to(root).as_posix()
        if rel in USER_OWNED:
            # 用户长出来的结构件：不签清单，也不算「多出来的」
            continue
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
