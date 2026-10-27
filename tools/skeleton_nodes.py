#!/usr/bin/env python3
"""骨架节点生成器：占位 000/100/200/…/900，只写结构，不写记忆内容。

规则（她 2026-10-26 定）：末位为 0 的整数编号全部是骨架节点，禁止手写、
禁止 agent 自动填充；内容模块只能占用末位非 0 的编号。

目录归属：000–899 写 `memory/`，900–999 写 `references/`。
`000-总索引.md` 不在这里生成，由 `tools/total_index.py` 负责。

用法：
    python tools/skeleton_nodes.py            # 只生成缺失的骨架节点
    python tools/skeleton_nodes.py --force    # 重写全部骨架节点（会覆盖手改内容）
    python tools/skeleton_nodes.py --dry      # 只打印计划，不写盘
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "references"

# 每个整百节点是一个分区目录；子类节点（110/120/…）等分区定稿后再生成。
PARTITIONS = {
    "000": "总索引",
    "100": "日常与状态",
    "200": "学习与技能",
    "300": "工作与项目",
    "400": "人际与称呼",
    "500": "偏好与口味",
    "600": "约定",
    "700": "隐私与敏感",
    "800": "AI 人设",
    "900": "骨架与协议",
}

TEMPLATE = """---
id: "{mid}"
title: "{title}"
kind: skeleton-node
keywords: []
sensitive: false
always: false
relates_to: []
updated: {today}
distribute: blank
---

# {mid} {title}

> 骨架节点：由 `tools/skeleton_nodes.py` 生成，别手改。本文件只放结构，不放记忆内容。

## 子类占位

| 节点 | 说明 |
| --- | --- |
| 待生成 | 子类目录（{prefix}10、{prefix}20…）由生成器按 `routes.py` 与模块 frontmatter 补齐 |
"""


def _target_dir(root: Path, mid: str) -> Path:
    """内容分区（000–899）写 memory/，骨架协议分区（900–999）写 references/。"""
    return root / ("references" if int(mid) >= 900 else "memory")


def plan(root: Path) -> list[tuple[Path, str]]:
    out = []
    for mid, title in PARTITIONS.items():
        if mid == "000":
            # 000 是总索引，由 tools/total_index.py 生成；这里不写占位，避免覆盖真索引。
            continue
        target = _target_dir(root, mid) / f"{mid}-{title}.md"
        out.append((target, TEMPLATE.format(mid=mid, title=title, today=date.today().isoformat(), prefix=mid[:1])))
    return out


SUBCLASS_TEMPLATE = """---
id: "{mid}"
title: "{title}"
kind: skeleton-node
keywords: []
sensitive: false
always: false
relates_to: ["{parent}"]
updated: {today}
distribute: blank
---

# {mid} {title}

> 骨架节点：由 `tools/skeleton_nodes.py` 生成，别手改。父分区 `{parent}`。
"""


def plan_subclasses(root: Path) -> list[tuple[Path, str]]:
    out = []
    today = date.today().isoformat()
    for mid, title in PARTITIONS.items():
        if mid == "000":
            continue
        # 子类占位仍留在 references/：现有 220-子类占位.md 等文件在这里，
        # 迁移子类目录是单独的搬家步骤，不在本次“只改索引”范围内。
        folder = root / "references" / f"{mid}-{title}"
        prefix = mid[:1]
        for n in range(1, 10):
            sub = f"{prefix}{n}0"
            target = folder / f"{sub}-子类占位.md"
            out.append((target, SUBCLASS_TEMPLATE.format(mid=sub, title="子类占位", parent=mid, today=today)))
    return out


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="生成骨架节点占位文件")
    ap.add_argument("--subclasses", action="store_true", help="同时生成各分区的子类占位节点（references/<分区>/<n>0-*.md）")
    ap.add_argument("--force", action="store_true", help="重写已存在的节点")
    ap.add_argument("--dry", action="store_true", help="只打印计划")
    ap.add_argument("--root", help="库根（默认脚本上一级）")
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else ROOT
    nodes = plan(root) + (plan_subclasses(root) if args.subclasses else [])
    wrote = skipped = 0
    for path, text in nodes:
        if path.exists() and not args.force:
            skipped += 1
            continue
        if args.dry:
            print(f"[dry] {path.relative_to(root)}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        wrote += 1
    print(f"骨架节点：写入 {wrote}，跳过 {skipped}（共 {len(nodes)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
