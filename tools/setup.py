#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键初始化：把记忆模块接到一台新机器 / 新工作目录上（Python 版，跨平台）。

用法：
  python tools/setup.py
  python tools/setup.py --workspace /home/me/notes
  python tools/setup.py --no-index        # 只检查，不重建索引
  python tools/setup.py --force-agents    # 覆盖已有的工作区 AGENTS.md

等价于根目录 setup.ps1（PowerShell 版保留给只装了 PS 的机器）。
重复跑无副作用。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import CONTENT_DIR, SKELETON_DIR, module_files  # noqa: E402

REFS = SKELETON_DIR
MEM = CONTENT_DIR
TOOLS = ROOT / "tools"
TPL = ROOT / "templates"
CFG = ROOT / "memory.config.json"
EXAMPLE = ROOT / "config.example.json"


def step(msg: str) -> None:
    print("  " + msg)


def find_python() -> str:
    return sys.executable or shutil.which("python3") or shutil.which("python") or "python"


def load_cfg() -> dict:
    src = CFG if CFG.exists() else EXAMPLE
    try:
        return json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return {}


def host_snapshot() -> Path | None:
    """本机是否已经有 `templates/hosts/<hostname>.md` 完整版快照（2026-10-22 加）。

    `templates/AGENTS.md` 只是 81 行的空壳起步版；hosts 里那份才是本机完整版
    （§0 快速模式 + §7 那十几条本机规矩）。用 `--force-agents` 拿空壳盖掉它，
    等于一次性把本机规矩冲干净，所以有快照时直接拒绝覆盖。
    """
    try:
        import socket

        cur = socket.gethostname()
    except Exception:
        return None
    hosts = TPL / "hosts"
    if not hosts.is_dir():
        return None
    for p in hosts.glob("*.md"):
        if p.stem.lower() == cur.lower():
            return p
    return None


def write_agents(workspace: Path, cfg: dict, force: bool) -> None:
    agents = workspace / "AGENTS.md"
    if agents.exists() and not force:
        print("工作区：")
        step("AGENTS.md 已存在，保留不动")
        return
    if agents.exists() and force:
        snap = host_snapshot()
        if snap is not None:
            print("工作区：")
            step(f"已有本机完整版 templates/hosts/{snap.name}，--force-agents 不覆盖")
            step(f"真要重来：先删 {snap}，或直接跑 tools/agents_hosts.py 重新铺一遍")
            return
    tpl = TPL / "AGENTS.md"
    if not tpl.exists():
        return
    body = tpl.read_text(encoding="utf-8")
    paths = cfg.get("paths") or {}
    home = paths.get("home") or str(Path.home())
    body = body.replace("%USERPROFILE%", home)
    # 2026-10-24：`public-AGENTS.md` 里原来写死 `~/.agents/skills/shiori-memory`，
    # 分发包解压到别处时 §0 与 §0.2 的路径全是错的（女儿接手旧库第一件事就撞上）。
    body = body.replace("<记忆库>", str(ROOT))
    body = body.replace("<笔记库>", str(workspace))
    body = body.replace("<代码仓库>", paths.get("codeRepo") or "")
    if os.name != "nt":
        body = body.replace("\\", "/")
    agents.write_text(body, encoding="utf-8")
    print("工作区：")
    step("已生成 " + str(agents))


def run_py(script: Path, *args: str) -> bool:
    """跑一个 tools 下的子脚本；`args` 是它的子命令（memdb/frag 都要求必填 cmd）"""
    py = find_python()
    try:
        p = subprocess.run([py, str(script), *args], cwd=str(ROOT))
        return p.returncode == 0
    except Exception as exc:  # noqa: BLE001
        step(f"⚠️ {script.name} 跑不起来：{exc}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default="")
    ap.add_argument("--python", default="")
    ap.add_argument("--no-index", action="store_true")
    ap.add_argument("--force-agents", action="store_true")
    args = ap.parse_args()

    print("=== 记忆模块初始化 ===")

    for d in (REFS, MEM, TOOLS, TPL):
        d.mkdir(parents=True, exist_ok=True)
    if not (ROOT / "SKILL.md").exists():
        print("❌ SKILL.md 不在，这不是完整的记忆模块目录")
        return 1

    print("结构：")
    step("根目录 " + str(ROOT))
    step(f"模块 {len(module_files())} 个")

    cfg = load_cfg()
    paths = cfg.setdefault("paths", {})
    if not paths.get("home"):
        paths["home"] = str(Path.home())
    py = args.python or paths.get("python") or find_python()
    paths["python"] = py
    if args.workspace:
        paths["workspace"] = str(Path(args.workspace).expanduser())
    cfg["version"] = cfg.get("version") or "0.1.0"
    cfg.setdefault("placeholders", {"home": "~", "workspace": "<笔记库>"})
    cfg.setdefault("pack", {
        "excludeDirs": ["archive", "__pycache__", ".git", "fragments"],
        "excludeFiles": [".style_override.json", "memdb.sqlite3"],
        "scrub": {},
        "regenerateCatalog": True,
    })
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("配置：")
    step("python  " + py)
    step("工作区  " + (paths.get("workspace") or "(未设置，跳过 AGENTS.md)"))

    if paths.get("workspace"):
        write_agents(Path(paths["workspace"]).expanduser(), cfg, args.force_agents)
        # 骨架是空壳：硬塞层 + 大类表两块托管块要由 agents_sync 从真源填进去（2026-10-21）
        if not run_py(TOOLS / "agents_sync.py"):
            step("⚠️ 托管块没填上，稍后手动跑 tools/agents_sync.py")

    if not args.no_index:
        print("索引：")
        if not run_py(TOOLS / "memdb.py", "index"):
            step("⚠️ memdb 建索引失败，检索会退化到 grep，不影响其它功能")
        if not run_py(TOOLS / "frag.py", "build"):
            step("⚠️ 片段层没建起来，读取退回整份模块")
        run_py(TOOLS / "catalog.py")

    print("=== 完成 ===")
    print("接下来：")
    print("  1. 填 memory.config.json 里的 workspace / codeRepo（体检脚本用它）")
    print("  2. 把打开记忆的约定写进你自己的 AGENTS.md（第 1 节）")
    print("  3. 空模块（distribute: blank）按自己的情况逐条填，格式见 references/926-模块标题.md")
    print("  4. 要发给别人：python tools/pack.py")
    print("  5. 换设备 / 改过 86 §七之后：python tools/agents_sync.py 对齐 AGENTS.md 的托管块")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
