#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""与现实同步的校验（只读）：CATALOG 新鲜度、检索索引新鲜度、路径可达性、笔记标签合规。

用法：
  python tools/check_freshness.py
  python tools/check_freshness.py --root <技能根> --vault <笔记库>

路径统一从 memory.config.json 取（见 README「配置」）。
退出码：0 全绿；1 有问题。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent


def load_config(root: Path) -> dict:
    for name in ("memory.config.json", "config.example.json"):
        p = root / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8-sig"))
            except Exception:
                pass
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--vault", default="")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    cfg = load_config(root)
    paths = cfg.get("paths") or {}
    vault = args.vault or paths.get("workspace") or ""
    code_repo = paths.get("codeRepo") or ""
    algo_repo = paths.get("algoRepo") or ""

    refs = root / "references"
    issues: list[str] = []
    warns: list[str] = []

    # 1) CATALOG 是否落后
    catalog = refs / "CATALOG.md"
    if not catalog.exists():
        issues.append("CATALOG.md 不存在：跑 tools/catalog.py")
    else:
        cat_time = catalog.stat().st_mtime
        newer = [
            p.name for p in refs.glob("*.md")
            if p.name not in ("CATALOG.md", "变更日志.md") and p.stat().st_mtime > cat_time
        ]
        if newer:
            warns.append(f"CATALOG 落后：{len(newer)} 个模块比它新，跑 tools/catalog.py（如 {newer[0]}）")

    # 2) memdb 索引是否落后
    db = root / "tools" / "memdb.sqlite3"
    if not db.exists():
        warns.append("memdb.sqlite3 不存在：跑 memdb.py index")
    else:
        db_time = db.stat().st_mtime
        newer_md = [
            p.name for p in refs.glob("*.md")
            if p.name != "CATALOG.md" and p.stat().st_mtime > db_time
        ]
        if newer_md:
            warns.append(f"检索索引落后：{len(newer_md)} 个模块比 memdb 新，跑 memdb.py index（如 {newer_md[0]}）")

    # 3) 关键路径可达性
    home = Path.home()
    candidates = [vault, code_repo, algo_repo, str(root)]
    dsh = home / ".dsh"
    if dsh.exists():
        candidates.append(str(dsh / "profiles" / "desktop"))
    for p in candidates:
        if p and not Path(p).expanduser().exists():
            issues.append(f"路径失效：{p}（记忆里还在引用）")

    # 4) 笔记标签白名单
    wl_file = refs / "02-学习习惯与笔记偏好.md"
    if wl_file.exists() and vault and Path(vault).expanduser().is_dir():
        wl_text = wl_file.read_text(encoding="utf-8", errors="replace")
        allowed = set(re.findall(r"\|\s*`([^`]+)`\s*\|", wl_text))
        bad: list[str] = []
        if allowed:
            for f in Path(vault).expanduser().rglob("*.md"):
                if any(part.startswith(".") for part in f.parts):
                    continue
                raw = f.read_text(encoding="utf-8", errors="replace")
                m = re.match(r"(?s)^---\r?\n(.*?)\r?\n---", raw)
                if not m:
                    continue
                for ln in m.group(1).split("\n"):
                    hit = re.match(r"^\s*-\s*([^\s#][^\r\n]*?)\s*$", ln)
                    if hit:
                        tag = hit.group(1).strip().strip("\"'")
                        if tag and tag not in allowed:
                            bad.append(f"{tag} <- {f.name}")
        if bad:
            uniq = sorted(set(bad))
            warns.append(f"笔记标签越界 {len(bad)} 处：" + "; ".join(uniq))

    print(f"=== 同步校验 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    if not issues and not warns:
        print("✅ 全部同步：目录、索引、路径、标签都对得上")
        return 0
    if issues:
        print(f"❌ 必须修 ({len(issues)})")
        for item in issues:
            print("  - " + item)
    if warns:
        print(f"⚠️ 建议看 ({len(warns)})")
        for item in warns:
            print("  - " + item)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
