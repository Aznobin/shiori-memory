#!/usr/bin/env python3
"""生成 memory/000-总索引.md —— 记忆库总索引。

规则：
- 按分区（000-099 身份背景 … 900-999 骨架协议）列出所有内容模块。
- 只列内容模块（content_module_files），不列骨架节点。
- 每行一个模块，带标题与相对链接。

用法：
    python tools/total_index.py
"""
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import CONTENT_DIR, content_module_files, partition_tag

OUT = CONTENT_DIR / "000-总索引.md"


def main() -> None:
    grouped = defaultdict(list)
    for num, p in sorted(content_module_files().items()):
        title = p.stem.split("-", 1)[1] if "-" in p.stem else p.stem
        rel = os.path.relpath(p, OUT.parent).replace(os.sep, "/")
        grouped[partition_tag(num)].append((num, title, p.name, rel))

    lines = [
        "---",
        'id: "000"',
        'title: "总索引"',
        "kind: skeleton-node",
        "keywords: []",
        "sensitive: false",
        "always: false",
        "relates_to: []",
        f"updated: {date.today().isoformat()}",
        "distribute: blank",
        "---",
        "",
        "# 000 总索引",
        "",
        "> 本文件由 `tools/total_index.py` 生成，别手改。",
        "> 用途：列出记忆库所有内容模块，按分区归类。",
        "",
    ]
    for part in sorted(grouped):
        lines.append(f"## {part}")
        lines.append("")
        lines.append("| 模块 | 标题 | 文件 |")
        lines.append("| --- | --- | --- |")
        for num, title, fname, rel in grouped[part]:
            lines.append(f"| `{num}` | {title} | [{fname}]({rel}) |")
        lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({sum(len(v) for v in grouped.values())} modules)")


if __name__ == "__main__":
    main()
