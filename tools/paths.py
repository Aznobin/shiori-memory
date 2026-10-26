#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全库唯一的路径入口（阶段3a）。

内容模块（000–899）放 `memory/`，骨架与协议件（900–999）放 `references/`。
本模块只负责“文件在哪”，不搬文件、不改内容；搬家由 3b 的 `--plan` 输出清单后手工 `git mv`。

用法：
    python tools/paths.py --check      # 打印两边的模块清单与总数
    python tools/paths.py --plan       # 打印「谁该搬到哪」的清单（只打印，不动手）

兼容一层：`resolve_module()` 先找 `memory/`，再找 `references/`，
所以 3a 阶段（内容还在 `references/`）行为与迁移前完全一致。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKELETON_DIR = ROOT / "references"
CONTENT_DIR = ROOT / "memory"

# 模块文件名：三位编号（可带一位小写字母后缀），标题可有可无
# （921–929 这批协议件是裸编号文件名，如 `923.md`）
MODULE_RE = re.compile(r"^(\d{3}[a-z]?)(?:-.+)?\.md$")
# 骨架区编号下界：>= 900 归 references/，其余归 memory/
SKELETON_FLOOR = 900

# 编号分区（2026-10-26 改口径，三位数）：片段 tag / 路由 / 检索共用一张表
PARTITIONS: list[tuple[int, int, str]] = [
    (0, 99, "身份背景"),
    (100, 199, "日常状态"),
    (200, 299, "学习技能"),
    (300, 399, "项目工作"),
    (400, 499, "人际称呼"),
    (500, 599, "偏好口味"),
    (600, 699, "约定"),
    (700, 799, "隐私"),
    (800, 899, "AI 人设"),
    (900, 999, "骨架协议"),
]


def partition_tag(num: str) -> str:
    """模块号 → 分区标签，形如 `"200-299 学习技能"`；号非法时返回 `"未分区"`。"""
    m = re.match(r"^(\d+)", str(num).strip())
    if not m:
        return "未分区"
    n = int(m.group(1))
    for lo, hi, name in PARTITIONS:
        if lo <= n <= hi:
            return f"{lo:03d}-{hi:03d} {name}"
    return "未分区"


def _num_of(path: Path) -> str | None:
    m = MODULE_RE.match(path.name)
    return m.group(1) if m else None


def _scan(dir_path: Path) -> dict[str, Path]:
    if not dir_path.is_dir():
        return {}
    out: dict[str, Path] = {}
    for p in sorted(dir_path.glob("*.md")):
        num = _num_of(p)
        if num:
            out[num] = p
    return out


def module_files() -> dict[str, Path]:
    """两个根里的模块文件，`{模块号: Path}`；同号时 `memory/` 优先。"""
    out = _scan(SKELETON_DIR)
    out.update(_scan(CONTENT_DIR))
    return out


def module_paths() -> list[Path]:
    """两个根里的模块文件路径（按模块号排序）。"""
    return [module_files()[k] for k in sorted(module_files())]


def resolve_module(num: str) -> Path | None:
    """按模块号找文件：先 `memory/`，再 `references/`；找不到返回 None。

    两种命名都要认：`809-独立人格与立场.md`（带标题）和 `923.md`（协议件裸编号）。
    """
    num = str(num).strip()
    for base in (CONTENT_DIR, SKELETON_DIR):
        if not base.is_dir():
            continue
        bare = base / f"{num}.md"
        if bare.is_file():
            return bare
        for p in sorted(base.glob(f"{num}-*.md")):
            if _num_of(p) == num:
                return p
    return None


def split_of(num: str) -> str:
    """`"skeleton"` / `"content"`：按编号判归属，不看内容。"""
    num = str(num).strip()
    m = re.match(r"^(\d+)", num)
    if not m:
        return "skeleton"
    return "skeleton" if int(m.group(1)) >= SKELETON_FLOOR else "content"


def target_of(num: str) -> Path:
    """该模块「应该」落在哪个目录（3b 搬家清单用）。"""
    base = SKELETON_DIR if split_of(num) == "skeleton" else CONTENT_DIR
    return base


def rel(path: Path) -> str:
    """相对仓库根的正斜杠路径，写进 dist 清单/文档用。"""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def plan() -> list[tuple[Path, Path]]:
    """[(现址, 目标)]：只列真的需要搬的，已就位的不列。"""
    out: list[tuple[Path, Path]] = []
    for num, src in sorted(module_files().items()):
        want_dir = target_of(num)
        if src.parent != want_dir:
            out.append((src, want_dir / src.name))
    return out


def _print_check() -> None:
    skel = {n: p for n, p in _scan(SKELETON_DIR).items()}
    cont = {n: p for n, p in _scan(CONTENT_DIR).items()}
    print(f"ROOT          {ROOT}")
    print(f"SKELETON_DIR  {SKELETON_DIR}  （{'存在' if SKELETON_DIR.is_dir() else '不存在'}）")
    print(f"CONTENT_DIR   {CONTENT_DIR}  （{'存在' if CONTENT_DIR.is_dir() else '不存在'}）")
    print("")
    print(f"骨架区 {len(skel)} 个：")
    for num in sorted(skel):
        print(f"  {num:<5} {rel(skel[num])}")
    print(f"内容区 {len(cont)} 个：")
    for num in sorted(cont):
        print(f"  {num:<5} {rel(cont[num])}")
    print("")
    print(f"合计 {len(module_files())} 个模块（骨架 {len(skel)} + 内容 {len(cont)}）")


def _print_plan() -> None:
    files = module_files()
    stay = [p for num, p in sorted(files.items()) if split_of(num) == "skeleton"]
    todo = plan()
    content = len(files) - len(stay)
    print(f"内容模块 {content} 个，待搬 {len(todo)} 个 → {rel(CONTENT_DIR)}/")
    for src, dst in todo:
        print(f"  git mv {rel(src)} {rel(dst)}")
    print(f"骨架 {len(stay)} 个留在 {rel(SKELETON_DIR)}/：")
    for p in stay:
        print(f"  {rel(p)}")
    if not todo:
        print(f"（内容 {content} 已在 {rel(CONTENT_DIR)}/、待搬 0）")


def main(argv: list[str]) -> int:
    if "--check" in argv:
        _print_check()
        return 0
    if "--plan" in argv:
        _print_plan()
        return 0
    print(__doc__.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
