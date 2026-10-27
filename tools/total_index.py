#!/usr/bin/env python3
"""生成 memory/000-总索引.md —— 记忆库总索引。

规则：
- 两级结构：先按分区（000-099 身份背景 … 900-999 骨架协议）列内容模块，
  再在该分区下挂「子类节点」——`memory/<分区>/` 或 `references/<分区>/` 里的骨架占位。
- 子类节点只是空壳（kind: skeleton-node / distribute: blank），看得见但**不进检索**。
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
from paths import CONTENT_DIR, ROOT, content_module_files, partition_tag, skeleton_node_files

OUT = CONTENT_DIR / "000-总索引.md"


def main() -> None:
    grouped = defaultdict(list)
    for num, p in sorted(content_module_files().items()):
        title = p.stem.split("-", 1)[1] if "-" in p.stem else p.stem
        rel = os.path.relpath(p, OUT.parent).replace(os.sep, "/")
        grouped[partition_tag(num)].append((num, title, p.name, rel))

    # 第二级：子类骨架节点，同样按分区标签归堆（分区里没有内容模块也要出现）
    nodes = defaultdict(list)
    for num, p in sorted(skeleton_node_files().items()):
        nodes[partition_tag(num)].append((num, p.name, p.parent.relative_to(ROOT).as_posix(), p.parent.name))

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
        "> 用途：两级结构——每个分区先列内容模块，再挂该分区的子类节点（骨架占位，不进检索）。",
        "",
    ]
    for part in sorted(set(grouped) | set(nodes)):
        lines.append(f"## {part}")
        lines.append("")
        if grouped.get(part):
            lines.append("| 模块 | 标题 | 文件 |")
            lines.append("| --- | --- | --- |")
            for num, title, fname, rel in grouped[part]:
                lines.append(f"| `{num}` | {title} | [{fname}]({rel}) |")
            lines.append("")
        else:
            lines.append("（本分区暂无内容模块，只有子类骨架占位。）")
            lines.append("")
        if nodes.get(part):
            pdir = sorted(nodes[part])[0][3]
            lines.append(f"### {pdir} 子类节点（骨架占位，不进检索）")
            lines.append("")
            lines.append("| 节点 | 文件 | 位置 |")
            lines.append("| --- | --- | --- |")
            for num, fname, where, _pdir in sorted(nodes[part]):
                lines.append(f"| `{num}` | {fname} | `{where}/` |")
            lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"wrote {OUT} ({sum(len(v) for v in grouped.values())} modules"
        f" + {sum(len(v) for v in nodes.values())} 子类节点)"
    )


if __name__ == "__main__":
    main()
