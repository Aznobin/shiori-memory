#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「必须处处一样」的段落注入工作区 AGENTS.md 的托管块里（多设备靠它对齐）。

用法：
  python tools/agents_sync.py                  # 重建全部托管块（内容没变就不写）
  python tools/agents_sync.py --show           # 只打印算出什么，不碰文件
  python tools/agents_sync.py --agents <路径>  # 指定 AGENTS.md
  python tools/agents_sync.py --quiet          # 静默（给 memgit.py 内部调用）

为什么要有这个（2026-10-21 用户提的问题）：
  工作区 `AGENTS.md` 不进 git，每台设备各写一份，个性内容本来就该不同——
  但「硬塞层」和「记忆大类表」这两块**必须处处一样**，不然 A 设备有的规矩
  B 设备没有，同一个 agent 换台机器就换了个脑子。
  所以这两块改成托管块，由脚本从真源重写，块外内容一律不动：

  <!-- CORE:BEGIN -->    ← 真源 references/86 §七（硬塞层清单）
  <!-- ROUTES:BEGIN -->  ← 真源 tools/routes.py 的 ROUTES 表

  幂等：块不存在就补，存在就原地替换，内容一样就不写盘（不制造无谓的改动）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import cfg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REFS = ROOT / "references"

BEGIN = "<!-- CORE:BEGIN -->"
END = "<!-- CORE:END -->"
TITLE = "## 硬塞层（每轮注入，由 `tools/agents_sync.py` 生成，**别手写**）"

CORE_SRC = "86-独立人格与立场.md"


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def core_items() -> list[str]:
    """从 86 §七 抽硬塞层清单：真源只有这一处，其余全是派生。"""
    src = next(iter(sorted(REFS.glob("86-*.md"))), REFS / CORE_SRC)
    text = read(src)
    if not text:
        return []
    m = re.search(r"(?ms)^## 七、.*?(?=^## )", text)
    body = m.group(0) if m else text
    return [line.strip() for line in body.splitlines() if re.match(r"^\d+\.\s", line.strip())]


def core_block(items: list[str] | None = None) -> str:
    items = core_items() if items is None else items
    return "\n".join([BEGIN, *items, END])


def agents_path(explicit: str = "") -> Path | None:
    """工作区 AGENTS.md：显式 > memory.config.json 的 workspace > 当前目录逐级向上找。

    一个都找不到就返回 None（新机器还没建工作区是正常状态，不该在开局刷警告）。
    """
    if explicit:
        return Path(explicit).expanduser()
    ws = (cfg.get("paths") or {}).get("workspace") or ""
    if ws and (Path(ws) / "AGENTS.md").exists():
        return Path(ws) / "AGENTS.md"
    for base in [Path.cwd(), *Path.cwd().parents]:
        if (base / "AGENTS.md").exists():
            return base / "AGENTS.md"
    if ws and Path(ws).is_dir():
        return Path(ws) / "AGENTS.md"
    return None


def replace_span(text: str, begin: str, end: str, block: str) -> str | None:
    """块已存在：只换块内内容。返回 None 表示没有这个块。"""
    if begin not in text or end not in text:
        return None
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    return head + block + tail


def _section_bounds(text: str, heading_re: str) -> tuple[int, int] | None:
    """返回某节标题所在行的 [start, end) 字符区间（end 为下一个二级标题或文件尾）。"""
    m = re.search(heading_re, text, flags=re.M)
    if not m:
        return None
    nxt = re.search(r"(?m)^## ", text[m.end():])
    end = m.end() + nxt.start() if nxt else len(text)
    return m.start(), end


def inject_core(text: str, block: str) -> str:
    """CORE 块：优先放 §8 里（保留用户自己的标题与引言），没有 §8 就另起一节。

    已有 §8 但没有托管块 = 手抄的旧清单。手抄版本千奇百怪（条目数不同、
    编号可能是 `10b.`、末尾可能带备注），按「从第一条编号行砍到节尾」整段替换，
    不去猜它的具体格式——上一版只按 `^\\d+\\.` 逐行 pop 时，`10b.` 这种就漏了，
    结果旧 11 条和新 13 条同时留在文件里（2026-10-21 在模拟漂移设备上撞到）。
    """
    done = replace_span(text, BEGIN, END, block)
    if done is not None:
        return done

    bounds = _section_bounds(text, r"^##\s*8\..*$")
    if not bounds:
        if not text.endswith("\n"):
            text += "\n"
        return text + "\n" + TITLE + "\n\n" + block + "\n"

    start, end = bounds
    section = text[start:end]
    m = re.search(r"(?m)^\d+[a-z]?\.\s", section)
    if m:
        keep = section[: m.start()].rstrip("\n")
    else:
        lines = section.rstrip("\n").split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        keep = "\n".join(lines)
    return text[:start] + keep + "\n\n" + block + "\n\n" + text[end:]


def inject_routes(text: str) -> str:
    """ROUTES 块：沿用 routes.py 的渲染，块不存在就补一节（含节标题）。"""
    from routes import BEGIN as RB, END as RE, SECTION_TITLE, render  # noqa: E402

    block = render()
    done = replace_span(text, RB, RE, block)
    if done is not None:
        # 老标题还写着 `tools/routes.py`，跟着换成现在的生成器（只动紧邻块上方那行）
        # 2026-10-21 修：本地 AGENTS.md 用的是 `## 9. 记忆大类表…` 这种编号节标题，
        # 旧正则要求行首就是「## 记忆大类表」，带编号的永远匹配不上，标题就一直停在旧名字。
        def _retitle(m: re.Match) -> str:
            num = m.group(1)
            head = f"## {num}. " if num else "## "
            return head + SECTION_TITLE[3:]

        return re.sub(
            r"(?m)^##(?:\s*(\d+(?:\.\d+)*)\.)?\s*记忆大类表（每轮注入，由 `tools/routes\.py` 生成）$",
            _retitle,
            done,
        )
    if not text.endswith("\n"):
        text += "\n"
    return text + "\n" + SECTION_TITLE + "\n\n" + block + "\n"


def sync(agents: Path, write: bool = True) -> tuple[str, bool]:
    """返回 (新全文, 是否有改动)。write=False 只算不写。"""
    text = read(agents)
    new = inject_core(text, core_block())
    new = inject_routes(new)
    changed = new != text
    if write and changed:
        agents.parent.mkdir(parents=True, exist_ok=True)
        agents.write_text(new, encoding="utf-8")
    return new, changed


def main() -> int:
    argv = sys.argv[1:]
    quiet = "--quiet" in argv
    if "--show" in argv:
        print(core_block())
        print()
        print("（ROUTES 块见 `python tools/routes.py --show`）")
        return 0
    explicit = ""
    if "--agents" in argv:
        i = argv.index("--agents")
        if i + 1 < len(argv):
            explicit = argv[i + 1]
    agents = agents_path(explicit)
    if agents is None:
        if not quiet:
            print("没找到工作区 AGENTS.md：填 memory.config.json 的 paths.workspace，或用 --agents 指定")
        return 0
    new, changed = sync(agents, write=True)
    if quiet:
        return 0
    print(f"{agents}（{len(new.splitlines())} 行）")
    print(f"  硬塞层 {len(core_items())} 条 · " + ("已更新" if changed else "已是最新，没动"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
