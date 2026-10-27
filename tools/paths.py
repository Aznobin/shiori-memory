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
# （921–929 这批协议件是裸编号文件名，如 `923-巡检记录.md`）
MODULE_RE = re.compile(r"^(\d{3}[a-z]?)(?:-.+)?\.md$")
# 骨架区编号下界：>= 900 归 references/，其余归 memory/
SKELETON_FLOOR = 900

# ── 模块编号清单（2026-10-27 批 X）─────────────────────────────────────
# 这些集合原先散在各工具里写死；两位→三位迁移（`9d8add2`）时就漏改过。
# 编号一变，这里的每一项都得跟着核；工具统一 `from paths import ...`，别再抄字面量。
# 注：`META_MODULE_IDS`（元规则模块）留在 `tools/routes.py`——它要拿 `ROUTES`
# 表做过期断言，放这边会和 routes 互相 import。
SENSITIVE_MODULE_IDS = frozenset({"701", "102", "211"})   # 隐私/健康/情绪：不进运维派生、不进注入
SELF_MODULE_IDS = frozenset({"803", "808", "809"})        # 我：性格 / 账 / 立场
USER_MODULE_IDS = frozenset({"001", "003", "002"})        # 她：背景 / 侧写 / 命名
SUBJECT_MODULE_IDS = frozenset({"801", "001", "003"})     # 「关于人」的断言：强制来源覆盖范围
WHOLE_MODULE_IDS = frozenset({"801"})                     # 必须整片到达、不切分
MAIN_SCORE_MODULE_IDS = frozenset({
    "813", "811", "812", "201", "202", "301", "902", "809", "808", "903", "802", "915", "917", "918",
})
DIARY_MODULE_ID = "101"                                   # 日记形态的内容默认归这里
DIARY_NOISE_MODULE_IDS = frozenset({
    "302", "902", "903", "904", "905", "906", "907", "908", "911", "912", "913", "915", "916", "917", "918",
})
ROUTE_SKIP_MODULE_IDS = frozenset({"901", "801"})         # 导入笔记时不作为路由目标
STRUCTURAL_MODULE_IDS = frozenset({"921", "922", "923", "925", "927", "929"})   # 协议件：日志/收件箱/清单等，不算正式模块
BANNED_DOC_IDS = frozenset({"701", "102", "changelog"})   # 不进检索：隐私 / 健康 / 变更日志
PERSONA_MODULE_ID = "813"                                 # 语气与交流（非运维问句时降权）
HARD_LAYER_MODULE_ID = "809"                              # 独立人格与立场：硬塞层正文来源
MORNING_MODULE_IDS = ("813", "809", "808")                # 开场包：语气 / 立场 / 我自己的账

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


PARTITION_DIR_RE = re.compile(r"^(\d{3})-")


def partition_dirs(base: Path) -> list[Path]:
    """某根下的分区目录（第二级），如 `memory/100-日常与状态`；按编号排序。"""
    if not base.is_dir():
        return []
    out = [d for d in sorted(base.iterdir()) if d.is_dir() and PARTITION_DIR_RE.match(d.name)]
    return out


def _scan_partitions(base: Path) -> dict[str, Path]:
    """扫第二级：`<根>/<分区目录>/*.md` → `{模块号: Path}`（2026-10-27 坐实第二级）。"""
    out: dict[str, Path] = {}
    for d in partition_dirs(base):
        for num, p in _scan(d).items():
            out.setdefault(num, p)
    return out


def _is_skeleton_node(path: Path) -> bool:
    """frontmatter 里 `kind: skeleton-node` 的是占位骨架，不算内容模块。

    骨架节点（000/100/…/900）只放结构、keywords 恒空；把它们塞进检索、
    目录和注入层等于往真模块清单里掺空壳，查出来什么都没有。
    """
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:600]
    except OSError:
        return False
    return re.search(r"^kind:\s*[\"']?skeleton-node", head, re.M) is not None


def module_files() -> dict[str, Path]:
    """两个根里的模块文件，`{模块号: Path}`；同号时按归属目录优先。"""
    out = _scan(SKELETON_DIR)
    for num, p in _scan(CONTENT_DIR).items():
        if num in out and split_of(num) == "skeleton":
            # >=900 的正本在 references/；memory/ 里同号那份是误放，别让它顶掉正本
            continue
        out[num] = p
    return out


def content_module_files() -> dict[str, Path]:
    """只返回内容模块：滤掉骨架占位（含第二级子类节点），给检索/目录/注入用。"""
    return {n: p for n, p in module_files().items() if not _is_skeleton_node(p)}


def skeleton_node_files() -> dict[str, Path]:
    """第二级骨架节点清单：`memory/<分区>/*.md` 与 `references/<分区>/*.md` 里 kind: skeleton-node 的。

    这是「骨架节点清单」的唯一入口——生成器（tools/skeleton_nodes.py）、体检
    （tools/memory_check.py）、打包清单（tools/pack.py）都从这里取，别再各自 glob。
    这些文件**不进检索、不进注入**：`content_module_files()` 会把它们滤掉。
    """
    out: dict[str, Path] = {}
    for base in (CONTENT_DIR, SKELETON_DIR):
        for num, p in _scan_partitions(base).items():
            if _is_skeleton_node(p):
                out[num] = p
    return out


def all_module_files() -> dict[str, Path]:
    """一级模块 + 二级骨架节点，给「看得见、但不进检索」的账本用。"""
    out = module_files()
    for num, p in skeleton_node_files().items():
        out.setdefault(num, p)
    return out


def module_paths() -> list[Path]:
    """两个根里的模块文件路径（按模块号排序）。"""
    return [module_files()[k] for k in sorted(module_files())]


def resolve_module(num: str) -> Path | None:
    """按模块号找文件：先 `memory/`，再 `references/`；找不到返回 None。

    两种命名都要认：`809-独立人格与立场.md`（带标题）和 `923-巡检记录.md`（协议件裸编号）。
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
        # 2026-10-27：二级结构落地后，「在不在对的地方」按根目录判，
        # 不能拿 `src.parent != want_dir` 比——分区子目录里的文件父级是分区目录。
        if want_dir not in src.parents:
            out.append((src, want_dir / src.name))
    return out


def _print_check() -> None:
    skel = {n: p for n, p in _scan(SKELETON_DIR).items()}
    cont = {n: p for n, p in _scan(CONTENT_DIR).items()}
    nodes = skeleton_node_files()
    print(f"ROOT          {ROOT}")
    print(f"SKELETON_DIR  {SKELETON_DIR}  （{'存在' if SKELETON_DIR.is_dir() else '不存在'}）")
    print(f"CONTENT_DIR   {CONTENT_DIR}  （{'存在' if CONTENT_DIR.is_dir() else '不存在'}）")
    print("")
    print(f"骨架区（一级）{len(skel)} 个：")
    for num in sorted(skel):
        print(f"  {num:<5} {rel(skel[num])}")
    print(f"内容区（一级）{len(cont)} 个：")
    for num in sorted(cont):
        print(f"  {num:<5} {rel(cont[num])}")
    print("")
    print(f"分区 → 子类（第二级）：")
    sub_mem = sub_ref = 0
    for base, label in ((CONTENT_DIR, "memory"), (SKELETON_DIR, "references")):
        for d in partition_dirs(base):
            nums = sorted(_scan(d))
            if not nums:
                continue
            if label == "memory":
                sub_mem += len(nums)
            else:
                sub_ref += len(nums)
            print(f"  {label}/{d.name}/  {' '.join(nums)}")
    print("")
    print(f"合计 {len(module_files())} 个模块（骨架 {len(skel)} + 内容 {len(cont)}）")
    print(f"子类节点 {len(nodes)} 个（memory {sub_mem} + references {sub_ref}）")
    print(f"清单合计 {len(all_module_files())} 个（一级 {len(module_files())} + 二级 {len(nodes)}）")
    print(f"content_module_files() {len(content_module_files())} 个（已滤掉全部骨架与子类节点）")


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
