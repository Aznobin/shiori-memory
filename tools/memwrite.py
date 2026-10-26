#!/usr/bin/env python3
"""程序写记忆（Stage 3 接入）：把 write_gate 校验过的 payload 真正插进模块文件。

模型只负责产出 payload（模块号 / 小标题 / 正文几行），剩下全由本脚本干：
定位模块文件 → 找小节 → 去重 → 插入 → 改 frontmatter 的 updated →
跑 frag.py build → 跑 memdb.py index → （可选）补一行变更日志。

用法：
    python tools/memwrite.py payload.json              # 写入 + build + index
    python tools/memwrite.py payload.json --dry        # 只报会写什么，不落盘
    python tools/memwrite.py payload.json --no-build   # 写但不跑 frag/index
    python tools/memwrite.py payload.json --changelog "摘要文案"
    type payload.json | python tools/memwrite.py       # 从 stdin 读
    python tools/memwrite.py --selftest                # 临时目录里自测

payload 形状（数组，每项一条）：
    [{"module": "903", "op": "add", "keys": ["语气", "道歉"],
      "section": "硬禁忌", "body": "第一行正文\\n第二行正文"}]

field 说明：
    module   必填，模块号（`813`），白名单从 references/ 实际文件名现取
    op       必填，当前只支持 add（merge / weight 仍走人工，脚本会明确拒绝）
    keys     必填，1~12 字的主题词，进 keys 校验
    section  可选，`## 小节标题` 的名字；不给就落在文件末尾
    body     必填，1~6 行、单行 ≤120 字，一行一个要点（自动补 `- ` 前缀）
    date / updated  可选，YYYY-MM-DD；不给就取今天（`updated` 优先，其次 `date`）

原子性：整批里只要有一条出错，一条都不写（先全部规划，规划完才落盘）。
换行：读时记录原文件是 CRLF 还是 LF，写回时照原样还原，不制造全文件 diff。
退出码：全部写入 0 / 被闸门拒或写入失败 1 / 有跳过（已存在）但没出错 0。
零依赖，只 import 同目录的 write_gate.py。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
DEFAULT_ROOT = TOOLS.parent
FRONT_UPDATED_RE = re.compile(r"^updated:\s*(\S+)\s*$")
HEAD2_RE = re.compile(r"^##\s+(.*?)\s*$")
TABLE_SEP_RE = re.compile(r"^\|\s*:?-{3,}")
BULLET_PREFIXES = ("-", "*", "|", "#", ">")


def load_gate():
    """按路径加载同目录的 write_gate.py（不靠 sys.path，避免撞名）。"""
    spec = importlib.util.spec_from_file_location("_mem_write_gate", TOOLS / "write_gate.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def prepare(payload) -> tuple[dict, list[dict]]:
    """过闸门 + 把闸门不认识的字段（如 `section`）按序号接回去。

    `write_gate.validate()` 的 normalized 只保留 module / op / keys / body / ids /
    date / updated / weight，`section` 会被丢掉——不接回来，所有条目都会落到文件末尾。
    """
    gate_result = load_gate().validate(payload)
    if gate_result.get("ok") is not True:
        return gate_result, []
    normalized = gate_result["normalized"]
    for src, dst in zip(payload, normalized):
        if isinstance(src, dict) and isinstance(src.get("section"), str):
            section = src["section"].strip()
            if section:
                dst["section"] = section
    return gate_result, normalized


def frontmatter_end(lines: list[str]) -> int:
    """返回 frontmatter 结束行（第二个 `---`）的下标，没有就 0。"""
    if not lines or lines[0].strip() != "---":
        return 0
    for j in range(1, len(lines)):
        if lines[j].strip() == "---":
            return j
    return 0


def module_dirs(root: Path) -> list[Path]:
    """模块可能待的两个目录：`memory/` 优先，`references/` 兼容一层（阶段3a 双根读取）。"""
    return [root / "memory", root / "references"]


def skeleton_dir(root: Path) -> Path:
    """骨架与协议件目录（900–999 与生成物 921–929）。"""
    return root / "references"


def target_dir(root: Path, module: str) -> Path:
    """新模块该落在哪：内容（000–899）进 `memory/`，骨架（900+）进 `references/`。"""
    m = re.match(r"^(\d+)", module)
    if m and int(m.group(1)) >= 900:
        return skeleton_dir(root)
    return root / "memory"


def module_file(root: Path, module: str) -> Path:
    # 末位为 0 的编号是骨架节点：只能由 tools/skeleton_nodes.py 生成，agent 不许自动填充
    if module.isdigit() and module.endswith("0"):
        raise SystemExit(
            f"[拒绝] {module} 是骨架节点，只能由 tools/skeleton_nodes.py 生成；内容请写到末位非 0 的模块"
        )
    # 2026-10-24：收件箱的文件名就是 `922.md`，不带短横线后缀，单独认一下
    pattern = f"{module}.md" if module.startswith("_") else f"{module}-*.md"
    hits: list[Path] = []
    for base in module_dirs(root):
        if not base.is_dir():
            continue
        found = sorted(base.glob(pattern))
        if not module.startswith("_"):
            found = [p for p in found if not p.name.startswith("_")]
        hits += found
    if not hits:
        raise SystemExit(f"[拒绝] memory/ 与 references/ 下都找不到模块 {module} 的文件")
    if len(hits) > 1:
        names = " / ".join(p.name for p in hits)
        raise SystemExit(f"[拒绝] 模块号 {module} 撞了多个文件：{names}")
    return hits[0]


def read_doc(path: Path) -> tuple[list[str], bool]:
    """返回 (按 LF 切好的行, 原文件是不是 CRLF)。"""
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")
    return text.split("\n"), crlf


def read_lines(path: Path) -> list[str]:
    return read_doc(path)[0]


def count_lines(lines: list[str]) -> int:
    """行数按 `wc -l` 的口径算：末尾那个 split 出来的空元素不算。

    2026-10-24 新设备：`--dry` 报「23 → 24 行」，用编辑器/`wc -l` 数是 23——
    因为 `text.split("\\n")` 在文件以换行结尾时多给一个空串。报告里的数字得跟人对得上。
    """
    return len(lines) - 1 if lines and lines[-1] == "" else len(lines)


def write_doc(path: Path, lines: list[str], crlf: bool) -> None:
    """按原换行风格写回；newline='' 关掉自动翻译，字节就是我们要的样子。"""
    body = "\n".join(lines)
    if not body.endswith("\n"):
        body += "\n"
    if crlf:
        body = body.replace("\n", "\r\n")
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(body)


def write_lines(path: Path, lines: list[str]) -> None:
    write_doc(path, lines, b"\r\n" in path.read_bytes())


def section_bounds(lines: list[str], name: str) -> tuple[int, int] | None:
    """返回 `## name` 这一节的正文范围 [start, end)，不含标题行与下一个 `##`。"""
    want = name.strip()
    for i, line in enumerate(lines):
        m = HEAD2_RE.match(line)
        if m and m.group(1).strip() == want:
            start = i + 1
            for j in range(start, len(lines)):
                if HEAD2_RE.match(lines[j]):
                    return start, j
            return start, len(lines)
    return None


# 来源标记（2026-10-24 shiori-old 定的）：只有「不是她本人原话」的才在行尾标出来，
# 因为 frag 切片只带正文，说明性文字在切片面前是流沙——标了才进上下文，只写文件头没用。
# user-original 不标（默认真实）；unknown 靠 note 承载，不在正文里写黑话。
SOURCE_MARKS = {
    "subagent": "（子智能体记录）",
    "test-transcript": "（测试记录，非本人原话）",
    "sample": "（样本）",
    "unknown": "（来源不明）",
}

# 日期锚点（2026-10-26 批 B）：正文条目必须带日期，闸门缺 date 时自动补当天，
# 这里把日期落到正文行尾（行尾已有任意 `（YYYY-MM-DD）` 就不重复挂）。
DATE_ANCHOR_RE = re.compile(r"（\d{4}-\d{2}-\d{2}）")
DATE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 退休标记：软删的唯一可见痕迹（2026-10-24）。`frag.py` 切片段时按这个前缀整行跳过。
RETIRE_MARK = "（已退休 "


def source_mark(source) -> str:
    if not isinstance(source, str):
        return ""
    return SOURCE_MARKS.get(source.strip(), "")


def date_mark(value) -> str:
    """把 YYYY-MM-DD 转成行尾日期锚点；不是合法 ISO 日期就返回空串。"""
    text = str(value or "").strip()
    if not DATE_ISO_RE.match(text):
        return ""
    return f"（{text}）"


def body_with_marks(body: str, item: dict, today: str) -> str:
    """正文 + 来源标记 + 日期锚点（已有日期锚点的正文不再追加）。"""
    text = str(body or "")
    marks = source_mark(item.get("source"))
    stamp = item.get("date") or item.get("updated") or today
    if not DATE_ANCHOR_RE.search(text):
        marks += date_mark(stamp)
    return text + marks


def to_bullets(body: str) -> list[str]:
    out: list[str] = []
    for raw in body.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith(BULLET_PREFIXES):
            out.append(line)
        else:
            out.append("- " + line.lstrip())
    return out


def already_there(lines: list[str], bullets: list[str]) -> bool:
    """去重判据：正文第一行（去首尾空白与列表号）已作为独立一行存在。"""
    probe = bullets[0].lstrip("-*|> ").strip()
    for line in lines:
        if line.lstrip("-*|> ").strip() == probe:
            return True
    return False


def set_updated(lines: list[str], today: str) -> int:
    """把 frontmatter 的 updated 改成 today，返回改了第几行（0 = 没找到）。"""
    if not lines or lines[0].strip() != "---":
        return 0
    for j in range(1, len(lines)):
        if lines[j].strip() == "---":
            break
        if FRONT_UPDATED_RE.match(lines[j]):
            lines[j] = f"updated: {today}"
            return j + 1
    return 0


def insert_at(lines: list[str], start: int, end: int, bullets: list[str]) -> list[str]:
    """在 [start, end) 的内容末尾插入 bullets，插在最后一行正文之后、空行之前。

    不额外补空行：模块里正文都是连续列表项，中间插一个空行会把列表割断。
    """
    pos = end
    while pos > start and not lines[pos - 1].strip():
        pos -= 1
    return lines[:pos] + bullets + lines[pos:]


def changelog_row(root: Path, today: str, files: str, summary: str) -> bool:
    """插在变更日志表头分隔线下面（表是新的在上、旧的下沉），不是文件末尾。"""
    path = skeleton_dir(root) / "921.md"
    if not path.is_file():
        return False
    lines, crlf = read_doc(path)
    row = f"| {today} | {files} | {summary} | 程序写入（`tools/memwrite.py`） |"
    pos = None
    for i, line in enumerate(lines):
        if TABLE_SEP_RE.match(line.strip()):
            pos = i + 1
            break
    if pos is None:
        pos = len(lines)
        while pos > 0 and not lines[pos - 1].strip():
            pos -= 1
    lines.insert(pos, row)
    write_doc(path, lines, crlf)
    return True


def run(cmd: list[str], root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def apply(root: Path, items: list[dict], today: str, dry: bool) -> dict:
    planned: list[dict] = []
    errors: list[dict] = []
    skipped: list[dict] = []
    created: list[str] = []
    # 同一个模块可能有多条：在内存里接力改，避免第二条把第一条覆盖掉
    work: dict[Path, dict] = {}

    for index, item in enumerate(items):
        op = item["op"]
        if op not in ("add", "replace", "retire"):
            errors.append(
                {
                    "index": index,
                    "field": "op",
                    "reason": f"op={op} 还没接写入链路，仍走人工；本脚本当前只做 add / replace",
                }
            )
            continue
        try:
            path = module_file(root, item["module"])
        except SystemExit as exc:
            errors.append({"index": index, "field": "module", "reason": str(exc)})
            continue

        if path not in work:
            lines, crlf = read_doc(path)
            work[path] = {"lines": lines, "crlf": crlf, "before": count_lines(lines)}
        state = work[path]
        lines = state["lines"]

        # 2026-10-24：`add` 只能加不能改——女儿给自己改名（三三 → 綴）时旧行删不掉，
        # 只能手改文件、日志还得人工补。`replace` 补这一环：`old` 唯一定位一行，正文换掉。
        if op == "replace":
            old = (item.get("old") or "").strip()
            if not old:
                errors.append(
                    {
                        "index": index,
                        "field": "old",
                        "reason": "replace 必须给 old：写能唯一定位那一行的片段",
                    }
                )
                continue
            hits = [i for i, ln in enumerate(lines) if old in ln]
            if not hits:
                errors.append(
                    {"index": index, "field": "old", "reason": f"replace 找不到要换的行：{old[:40]}"}
                )
                continue
            if len(hits) > 1:
                errors.append(
                    {
                        "index": index,
                        "field": "old",
                        "reason": f"replace 的 old 匹配到 {len(hits)} 行，写长一点只留一行",
                    }
                )
                continue
            new_line = to_bullets(body_with_marks(item["body"], item, today))[0]
            new_line = re.sub(r"^(?:-|\*|\|)\s+", "", new_line, count=1)
            hit = hits[0]
            lead = re.match(r"^(\s*(?:-|\*|\|)\s+)", lines[hit])
            prefix = lead.group(1) if lead else "- "
            before_now = count_lines(lines)
            lines[hit] = prefix + new_line
            # 2026-10-24：导入旧内容不该把 updated 拉回去（shiori-new5 报的：写「旧笔记」把 01 的
            # updated 从 2026-10-24 变成 2026-10-12）。条目自己的日期照样记在 body/date 里，只保证
            # 模块的 updated 单调不回退。
            stamp = max(item.get("updated") or item.get("date") or today, today)
            anchor = set_updated(lines, stamp)
            planned.append(
                {
                    "index": index,
                    "path": path,
                    "before": before_now,
                    "after": count_lines(lines),
                    "added": 1,
                    "section": f"（替换第 {hit + 1} 行）",
                    "updated_line": anchor,
                    "content": lines,
                }
            )
            continue

        # op=retire：软删（2026-10-24 定的口径，理由见 references/903）。
        # 不抹掉正文，只在行尾挂退休标记；`frag` 切片段时跳过标记行，`memdb` 对应标 deleted。
        # 依据 `903` §4 本库没有容量压力（理由见 references/903）+ 她对自己写库的纪律「禁用 DELETE，一律 UPDATE status」。
        if op == "retire":
            old = (item.get("old") or "").strip()
            if not old:
                errors.append({"index": index, "field": "old", "reason": "retire 必须给 old，指明哪一行退休"})
                continue
            hits = [i for i, ln in enumerate(lines) if old in ln]
            if not hits:
                errors.append(
                    {"index": index, "field": "old", "reason": f"retire 找不到要退休的行：{old[:40]}"}
                )
                continue
            if len(hits) > 1:
                errors.append(
                    {
                        "index": index,
                        "field": "old",
                        "reason": f"retire 的 old 匹配到 {len(hits)} 行，写长一点只留一行",
                    }
                )
                continue
            hit = hits[0]
            if RETIRE_MARK in lines[hit]:
                skipped.append(
                    {"index": index, "file": path.name, "reason": "这行已经退休过，不重复挂标记"}
                )
                continue
            before_now = count_lines(lines)
            lines[hit] = lines[hit].rstrip() + f"（已退休 {today}：{item['reason']}）"
            stamp = max(item.get("updated") or item.get("date") or today, today)
            anchor = set_updated(lines, stamp)
            planned.append(
                {
                    "index": index,
                    "path": path,
                    "before": before_now,
                    "after": count_lines(lines),
                    "added": 0,
                    "section": f"（第 {hit + 1} 行标退休）",
                    "updated_line": anchor,
                    "content": lines,
                }
            )
            continue

        bullets = to_bullets(body_with_marks(item["body"], item, today))

        if already_there(lines, bullets):
            skipped.append({"index": index, "file": path.name, "reason": "同样的正文第一行已在文件里"})
            continue

        section = item.get("section")
        # 2026-10-24：before 必须在「建小节 / 插条目」之前取——建节会先加 2 行，
        # 那 2 行被算进 before 就会报 16 → 17，而磁盘实际 14 → 17（新设备演练第 4 条）。
        before_now = count_lines(lines)
        if section:
            bounds = section_bounds(lines, section)
            if bounds is None:
                # 2026-10-24 新设备第一写：空模块里根本没有 `## 我是谁`，原来这里直接整批拒，
                # 女儿建人设的第一条记忆就被卡住。改成自动补一节（只改内存，有错时整批都不落盘）。
                if lines and lines[-1].strip():
                    lines.append("")
                lines.extend([f"## {section}", ""])
                created.append(f"{path.name} → ## {section}")
                bounds = section_bounds(lines, section)
            start, end = bounds
        else:
            # 落在文件末尾时用「真实行数」当锚点，别用 split 出来的长度——
            # 那个多一个末尾空元素，插进去会在 bullet 前多出一行空行（2026-10-24 dry 报 121 → 123）。
            start = end = count_lines(lines)

        # 2026-10-24：同一批连写同一个模块时，报告里每条都显示「原始行数 → 写入后」，
        # 第二条看起来像没累进（新设备演练第 7 条）。before 上面已经取过了（在建小节之前）。
        new_lines = insert_at(lines, start, end, bullets)
        # 同上：导入旧内容不该把模块的 updated 拉回去。
        stamp = max(item.get("updated") or item.get("date") or today, today)
        anchor = set_updated(new_lines, stamp)
        state["lines"] = new_lines
        planned.append(
            {
                "index": index,
                "path": path,
                "before": before_now,
                "after": count_lines(new_lines),
                "added": count_lines(new_lines) - before_now,
                "section": section or "（文件末尾）",
                "updated_line": anchor,
                "content": new_lines,
            }
        )

    if errors:
        return {"ok": False, "errors": errors, "skipped": skipped,
                "planned": planned, "created": created}

    # 2026-10-24：`--new-module` 和分发骨架建出来的空模块，开头都挂着一句
    # 「> 这个模块是空的：按自己的情况往里加条目…」。第一次真写进去之后它就不成立了，
    # 可原来一直留着 —— 打开文件的人以为自己还没写过（新设备演练第 5 条）。
    placeholder_re = re.compile(r"^>\s*这个模块是空的")
    for path in {p["path"] for p in planned}:
        st = work.get(path)
        if not st:
            continue
        kept = [ln for ln in st["lines"] if not placeholder_re.match(ln.strip())]
        if len(kept) == len(st["lines"]):
            continue
        out: list[str] = []
        for ln in kept:
            if not ln.strip() and out and not out[-1].strip():
                continue
            out.append(ln)
        st["lines"] = out
    for p in planned:
        st = work.get(p["path"])
        if st:
            # 这里原来用 len(st["lines"])，把 split 出来的末尾空元素也算进去，
            # 于是 dry 报「121 → 123」而文件其实 122 行（2026-10-24 新设备）。
            p["after"] = count_lines(st["lines"])
            p["added"] = p["after"] - p["before"]

    if not dry:
        for path, state in work.items():
            write_doc(path, state["lines"], state["crlf"])

    return {"ok": True, "errors": [], "skipped": skipped, "planned": planned, "created": created}


def report(result: dict, dry: bool) -> None:
    if not result["ok"]:
        print("被拒绝，一条都没写：")
        for e in result["errors"]:
            print(f"  ✗ [{e['index']}] {e['field']}: {e['reason']}")
        return
    planned = result["planned"]
    for c in result.get("created") or []:
        print(f"  + 自动新建小节：{c}")
    if not planned:
        print("没有需要写入的条目。")
    for item in planned:
        verb = "将写入" if dry else "已写入"
        print(
            f"  ✓ {verb} {item['path'].name} → ## {item['section']}  "
            f"+{item['added']} 行（{item['before']} → {item['after']} 行）"
        )
        if item["updated_line"]:
            print(f"      frontmatter updated 改在第 {item['updated_line']} 行")
    for s in result["skipped"]:
        print(f"  - 跳过 [{s['index']}] {s['file']}：{s['reason']}")


def selftest() -> int:
    import shutil  # noqa: F401  保留：老夹具曾经用过，留给后续扩展

    today = date.today().isoformat()

    passes = 0
    total = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal passes, total
        total += 1
        if cond:
            passes += 1
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ {name} {extra}")

    # 2026-10-24：原来拿 memory/813-… 当夹具，可 89/08/82 这些模块在分发骨架里
    # 本来就被 drop——换个库跑自测直接「自测跳过」return 1。改成自造最小模块，跟真实库解耦。
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        refs = skeleton_dir(root)
        refs.mkdir(parents=True, exist_ok=True)
        target = refs / "903-自测模块.md"
        target.write_text(
            "---\ntitle: 自测模块\nupdated: 2000-01-01\n---\n\n"
            "# 自测模块\n\n> 自测用，跑完即删。\n\n"
            "## 硬禁忌\n\n- 旧条目一\n\n"
            "## 干活纪律\n\n- 旧条目二\n\n"
            "末尾段落，不属于任何小节。\n",
            encoding="utf-8",
            newline="",       # 不关掉翻译，Windows 上会被写成 CRLF，换行风格用例直接假红
        )
        before = len(read_lines(target))
        payload = [
            {
                "module": "903",
                "op": "add",
                "keys": ["自测", "程序写入"],
                "section": "硬禁忌",
                "body": "自测专用条目，跑完即删\n第二行要能一起进去",
            }
        ]
        gate = load_gate()
        g = gate.validate(payload)
        check("闸门放行合法 payload", g.get("ok") is True, str(g.get("errors")))
        g2, normalized = prepare(payload)
        # 2026-10-24：这条原来写的是「闸门会丢掉 section（已知）」——等于把缺陷固化成预期，
        # 哪天有人把闸门修好了，自测反而变红。改成只断言对外契约：prepare 之后一定还在。
        check("prepare 之后 section 还在", normalized[0].get("section") == "硬禁忌")
        res = apply(root, normalized, today, dry=False)
        check("写入成功", res["ok"] is True, str(res["errors"]))
        lines = read_lines(target)
        check("行数 +2", len(lines) == before + 2, f"{before} → {len(lines)}")
        check("正文在文件里", any("自测专用条目" in ln for ln in lines))
        check("frontmatter updated 已改", f"updated: {today}" in "\n".join(lines[:40]))
        bounds = section_bounds(lines, "硬禁忌")
        inside = False
        if bounds:
            seg = "\n".join(lines[bounds[0] : bounds[1]])
            inside = "自测专用条目" in seg
        check("落在『硬禁忌』小节内", inside)
        check(
            "原换行风格保持不变",
            b"\r\n" not in target.read_bytes(),
            "写回时换行风格被改写（LF 被改成 CRLF）",
        )

        again = apply(root, normalized, today, dry=False)
        check("重复写入被去重", again["ok"] is True and len(again["skipped"]) == 1)
        check("去重后行数不变", len(read_lines(target)) == before + 2)

        bad = apply(
            root,
            [{"module": "903", "op": "merge", "keys": ["甲"], "body": "正文", "ids": ["813.001", "813.002"]}],
            today,
            dry=True,
        )
        check("op=merge 被明确拒绝", bad["ok"] is False and bad["errors"][0]["field"] == "op")
        weight = apply(
            root,
            [{"module": "903", "op": "weight", "keys": ["甲"], "body": "正文"}],
            today,
            dry=True,
        )
        check("op=weight 被明确拒绝", weight["ok"] is False and weight["errors"][0]["field"] == "op")
        missing = apply(
            root,
            [{"module": "903", "op": "add", "keys": ["甲"], "body": "正文", "section": "不存在的节"}],
            today,
            dry=True,
        )
        check(
            "小节不存在时自动新建（不再整批拒）",
            missing["ok"] is True and bool(missing.get("created")),
            str(missing.get("errors")),
        )
        nope = apply(
            root,
            [{"module": "7z", "op": "add", "keys": ["甲"], "body": "正文"}],
            today,
            dry=True,
        )
        check(
            "module 不存在时拒绝",
            nope["ok"] is False and nope["errors"][0]["field"] == "module",
            str(nope["errors"]),
        )

        snap = target.read_bytes()
        dry_res = apply(root, normalized + [
            {"module": "903", "op": "add", "keys": ["乙"], "section": "硬禁忌", "body": "另一条也要能进"}
        ], today, dry=True)
        check("--dry 一条都不落盘", dry_res["ok"] is True and target.read_bytes() == snap)

        atomic = apply(
            root,
            [
                {"module": "903", "op": "add", "keys": ["丙"], "section": "硬禁忌", "body": "原子性好的这条"},
                {"module": "903", "op": "merge", "keys": ["丁"], "body": "原子性坏的这条",
                 "ids": ["903.001", "903.002"]},
            ],
            today,
            dry=False,
        )
        after_atomic = target.read_bytes()
        check(
            "一条失败时整批都不写（原子性）",
            atomic["ok"] is False
            and after_atomic == snap
            and not any("原子性好的这条" in ln for ln in read_lines(target)),
            str(atomic["errors"]),
        )

        # 2026-10-26 批A-A1：变更日志表头不再只扫前 40 行——表头前有超过 40 行
        # 说明/前言时也要插在分隔线下面，不许退回文件末尾。
        cl_dir = root / "references"
        cl_dir.mkdir(parents=True, exist_ok=True)
        cl_path = cl_dir / "921.md"
        cl_pad = [f"| 旧行 {i} | x | y | z |" for i in range(45)]
        cl_lines = ["# 变更日志", "", "| 日期 | 文件 | 改动 | 触发 |",
                    "| --- | --- | --- | --- |"] + cl_pad
        cl_path.write_text("\n".join(cl_lines), encoding="utf-8", newline="")
        check(
            "表头超过 40 行仍插对位（A1）",
            changelog_row(root, today, "`922.md`", "只改待确认的说明") is True
            and next(
                (i for i, ln in enumerate(read_lines(cl_path)) if "只改待确认的说明" in ln),
                -1,
            )
            == 4,
        )

        two = apply(
            root,
            [
                {"module": "903", "op": "add", "keys": ["戊"], "section": "硬禁忌", "body": "同批第一条"},
                {"module": "903", "op": "add", "keys": ["己"], "section": "干活纪律", "body": "同批第二条"},
            ],
            today,
            dry=False,
        )
        both = "\n".join(read_lines(target))
        check(
            "同模块两条不互相覆盖",
            two["ok"] is True and "同批第一条" in both and "同批第二条" in both,
            "第二条把第一条覆盖了",
        )

    print(f"selftest {passes}/{total}")
    return 0 if passes == total else 1


def new_module(root: Path, spec: str, dry: bool) -> int:
    """按模板新建一个模块文件（已存在就跳过），并登记路由表。

    2026-10-24：新设备演练暴露的第一号卡点——memwrite 只接受白名单里已有的模块，
    而「建第一个自己的模块」是每个新用户做的第一件事，文档却让人手写 md。
    """
    m = re.match(r"^(\d{2,3}[a-z]?)-(.+)$", spec.strip())
    if not m:
        print(f"[拒绝] 模块名要写成 NNN-名称（例如 001-我是谁），收到 {spec!r}")
        return 1
    mod, name = m.group(1), m.group(2).strip().replace("/", "-").replace("\\", "-")
    if mod.isdigit() and mod.endswith("0"):
        print(f"[拒绝] {mod} 是骨架节点，只能由 tools/skeleton_nodes.py 生成")
        return 1
    hits: list[Path] = []
    for base in module_dirs(root):
        if base.is_dir():
            hits += [p for p in sorted(base.glob(f"{mod}-*.md")) if not p.name.startswith("_")]
    if hits:
        print(f"[跳过] 模块 {mod} 已经存在：{hits[0].name}")
        return 0
    dest = target_dir(root, mod)
    path = dest / f"{mod}-{name}.md"
    today = date.today().isoformat()
    text = (
        "---\n"
        f'id: "{mod}"\n'
        f"title: {name}\n"
        "keywords: []\n"
        "sensitive: false\n"
        "always: false\n"
        f"updated: {today}\n"
        "distribute: keep\n"
        "---\n\n"
        f"# {name}\n\n"
        "> 这个模块是空的：按自己的情况往里加条目，一条只记一个事实，格式见 `926.md`。\n"
    )
    shown = path.relative_to(root).as_posix()
    if dry:
        print(f"[dry] 会新建 {shown}（{len(text)} 字节）")
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"✅ 新建模块 {shown}")
    routes = root / "tools" / "routes.py"
    if routes.exists():
        r = subprocess.run([sys.executable, str(routes)], cwd=str(root),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        print("   路由表：", (r.stdout or r.stderr or "").strip().splitlines()[-1][:100] if (r.stdout or r.stderr) else "已更新")
    # 2026-10-24：新设备演练里建完 07 模块，CATALOG 还是旧的 21 个，`memread --list` 看不到新模块。
    # 建模块是「让库里多一份东西」，顺手把目录刷了才对得上用户的预期。
    catalog = root / "tools" / "catalog.py"
    if catalog.exists():
        c = subprocess.run([sys.executable, str(catalog)], cwd=str(root),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        print("   目录：", (c.stdout or c.stderr or "").strip().splitlines()[-1][:100] if (c.stdout or c.stderr) else "已更新")
    print("   接着直接 memwrite 写这条模块的第一条记忆即可")
    return 0


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(add_help=True, description="程序写记忆（Stage 3 接入）")
    ap.add_argument("payload", nargs="?", help="payload JSON 文件；省略则读 stdin")
    ap.add_argument("--dry", action="store_true", help="只报会写什么，不落盘")
    ap.add_argument("--no-build", action="store_true", help="写完不跑 frag.py build / memdb.py index")
    ap.add_argument("--changelog", metavar="摘要", help="写完补一行变更日志")
    ap.add_argument("--root", metavar="PATH", help="记忆库根目录（默认脚本上一级）")
    ap.add_argument("--selftest", action="store_true", help="在临时目录里自测")
    ap.add_argument("--json", action="store_true", help="输出 JSON 结果")
    ap.add_argument("--new-module", metavar="NN-名字",
                    help="新建一个自己的模块文件（如 801-我是谁），并登记路由表")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT
    if args.new_module:
        return new_module(root, args.new_module, args.dry)

    if args.payload:
        raw = Path(args.payload).read_text(encoding="utf-8-sig")
    else:
        raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[拒绝] payload 不是合法 JSON：{exc}")
        return 1
    if not isinstance(payload, list):
        payload = [payload]

    gate_result, normalized = prepare(payload)
    if gate_result.get("ok") is not True:
        if args.json:
            print(json.dumps(gate_result, ensure_ascii=False, indent=2))
        else:
            print("闸门拒绝，一条都没写：")
            for e in gate_result.get("errors", []):
                print(f"  ✗ [{e['index']}] {e['field']}: {e['reason']}")
        return 1

    today = date.today().isoformat()
    result = apply(root, normalized, today, bool(args.dry))
    if args.json:
        print(
            json.dumps(
                {
                    "ok": result["ok"],
                    "errors": result["errors"],
                    "skipped": result["skipped"],
                    "written": [
                        {"file": p["path"].name, "section": p["section"], "lines": p["added"]}
                        for p in result["planned"]
                    ],
                    "dry": bool(args.dry),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        report(result, bool(args.dry))

    if not result["ok"]:
        return 1
    if args.dry or not result["planned"]:
        return 0

    if args.changelog:
        # 2026-10-24：同一批里连写同一个模块时，planned 会有多条同 path 的记录，
        # 原来直接 join 出「`801-我是谁.md`、`801-我是谁.md`、`801-我是谁.md`」三个重名。
        seen = []
        for p in result["planned"]:
            name = p["path"].name
            if name not in seen:
                seen.append(name)
        files = "、".join(f"`{n}`" for n in seen)
        if changelog_row(root, today, files, args.changelog):
            print("  ✓ 变更日志补了一行")

    if not args.no_build:
        # 2026-10-24：这几个重建互不依赖——frag build 切 references 生成 index.json，
        # memdb index 直接读 references 建 sqlite，catalog 只数行数，谁也不吃谁的产物。
        # 串行要 630ms，其中一半是白白等 Python 进程启动。改成并发，压到 ~400ms。
        # catalog 也是这轮加进来的：原来只有 `--new-module` 才刷 CATALOG，普通写入后
        # 目录页的行数就停在旧值，拿它当导航会数错（新设备演练第 6 条）。
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=3) as ex:
            f1 = ex.submit(run, [sys.executable, "tools/frag.py", "build"], root)
            f2 = ex.submit(run, [sys.executable, "tools/memdb.py", "index"], root)
            f3 = ex.submit(run, [sys.executable, "tools/catalog.py"], root)
            code, out = f1.result()
            code2, out2 = f2.result()
            code3, out3 = f3.result()
        print(f"  {'✓' if code == 0 else '✗'} frag.py build：{out.splitlines()[-1] if out else ''}")
        print(f"  {'✓' if code2 == 0 else '✗'} memdb.py index：{out2.splitlines()[-1] if out2 else ''}")
        print(f"  {'✓' if code3 == 0 else '✗'} catalog.py：{out3.splitlines()[-1] if out3 else ''}")
        if code != 0 or code2 != 0 or code3 != 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
