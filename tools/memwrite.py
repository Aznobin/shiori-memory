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
    [{"module": "90", "op": "add", "keys": ["语气", "道歉"],
      "section": "硬禁忌", "body": "第一行正文\\n第二行正文"}]

field 说明：
    module   必填，模块号（`89`），白名单从 references/ 实际文件名现取
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


def module_file(ref: Path, module: str) -> Path:
    # 2026-10-24：收件箱的文件名就是 `_待确认.md`，不带短横线后缀，单独认一下
    if module.startswith("_"):
        hits = sorted(p for p in ref.glob(f"{module}.md"))
    else:
        hits = sorted(p for p in ref.glob(f"{module}-*.md") if not p.name.startswith("_"))
    if not hits:
        raise SystemExit(f"[拒绝] references/ 下找不到模块 {module} 的文件")
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
    "test-transcript": "（测试记录，非本人原话）",
    "sample": "（样本）",
    "unknown": "（来源不明）",
}


def source_mark(source) -> str:
    if not isinstance(source, str):
        return ""
    return SOURCE_MARKS.get(source.strip(), "")


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
    path = root / "references" / "变更日志.md"
    if not path.is_file():
        return False
    lines, crlf = read_doc(path)
    row = f"| {today} | {files} | {summary} | 程序写入（`tools/memwrite.py`） |"
    pos = None
    for i, line in enumerate(lines[:40]):
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
    ref = root / "references"
    planned: list[dict] = []
    errors: list[dict] = []
    skipped: list[dict] = []
    created: list[str] = []
    # 同一个模块可能有多条：在内存里接力改，避免第二条把第一条覆盖掉
    work: dict[Path, dict] = {}

    for index, item in enumerate(items):
        op = item["op"]
        if op not in ("add", "replace"):
            errors.append(
                {
                    "index": index,
                    "field": "op",
                    "reason": f"op={op} 还没接写入链路，仍走人工；本脚本当前只做 add / replace",
                }
            )
            continue
        try:
            path = module_file(ref, item["module"])
        except SystemExit as exc:
            errors.append({"index": index, "field": "module", "reason": str(exc)})
            continue

        if path not in work:
            lines, crlf = read_doc(path)
            work[path] = {"lines": lines, "crlf": crlf, "before": len(lines)}
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
            new_line = to_bullets(item["body"] + source_mark(item.get("source")))[0]
            new_line = re.sub(r"^(?:-|\*|\|)\s+", "", new_line, count=1)
            hit = hits[0]
            lead = re.match(r"^(\s*(?:-|\*|\|)\s+)", lines[hit])
            prefix = lead.group(1) if lead else "- "
            before_now = len(lines)
            lines[hit] = prefix + new_line
            stamp = item.get("updated") or item.get("date") or today
            anchor = set_updated(lines, stamp)
            planned.append(
                {
                    "index": index,
                    "path": path,
                    "before": before_now,
                    "after": len(lines),
                    "added": 1,
                    "section": f"（替换第 {hit + 1} 行）",
                    "updated_line": anchor,
                    "content": lines,
                }
            )
            continue

        bullets = to_bullets(item["body"] + source_mark(item.get("source")))

        if already_there(lines, bullets):
            skipped.append({"index": index, "file": path.name, "reason": "同样的正文第一行已在文件里"})
            continue

        section = item.get("section")
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
            start = end = len(lines)

        # 2026-10-24：同一批连写同一个模块时，报告里每条都显示「原始行数 → 写入后」，
        # 第二条看起来像没累进（新设备演练第 7 条）。before 取插入前的实际行数才连得上。
        before_now = len(lines)
        new_lines = insert_at(lines, start, end, bullets)
        stamp = item.get("updated") or item.get("date") or today
        anchor = set_updated(new_lines, stamp)
        state["lines"] = new_lines
        planned.append(
            {
                "index": index,
                "path": path,
                "before": before_now,
                "after": len(new_lines),
                "added": len(bullets),
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
            p["after"] = len(st["lines"])

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

    # 2026-10-24：原来拿 references/89-… 当夹具，可 89/08/82 这些模块在分发骨架里
    # 本来就被 drop——换个库跑自测直接「自测跳过」return 1。改成自造最小模块，跟真实库解耦。
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        refs = root / "references"
        refs.mkdir(parents=True, exist_ok=True)
        target = refs / "90-自测模块.md"
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
                "module": "90",
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
            [{"module": "90", "op": "merge", "keys": ["甲"], "body": "正文", "ids": ["89.001", "89.002"]}],
            today,
            dry=True,
        )
        check("op=merge 被明确拒绝", bad["ok"] is False and bad["errors"][0]["field"] == "op")
        weight = apply(
            root,
            [{"module": "90", "op": "weight", "keys": ["甲"], "body": "正文"}],
            today,
            dry=True,
        )
        check("op=weight 被明确拒绝", weight["ok"] is False and weight["errors"][0]["field"] == "op")
        missing = apply(
            root,
            [{"module": "90", "op": "add", "keys": ["甲"], "body": "正文", "section": "不存在的节"}],
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
            {"module": "90", "op": "add", "keys": ["乙"], "section": "硬禁忌", "body": "另一条也要能进"}
        ], today, dry=True)
        check("--dry 一条都不落盘", dry_res["ok"] is True and target.read_bytes() == snap)

        atomic = apply(
            root,
            [
                {"module": "90", "op": "add", "keys": ["丙"], "section": "硬禁忌", "body": "原子性好的这条"},
                {"module": "90", "op": "merge", "keys": ["丁"], "body": "原子性坏的这条",
                 "ids": ["90.001", "90.002"]},
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

        two = apply(
            root,
            [
                {"module": "90", "op": "add", "keys": ["戊"], "section": "硬禁忌", "body": "同批第一条"},
                {"module": "90", "op": "add", "keys": ["己"], "section": "干活纪律", "body": "同批第二条"},
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
    ref = root / "references"
    m = re.match(r"^(\d{2}[a-z]?)-(.+)$", spec.strip())
    if not m:
        print(f"[拒绝] 模块名要写成 NN-名称（例如 00-我是谁），收到 {spec!r}")
        return 1
    mod, name = m.group(1), m.group(2).strip().replace("/", "-").replace("\\", "-")
    hits = sorted(p for p in ref.glob(f"{mod}-*.md") if not p.name.startswith("_"))
    if hits:
        print(f"[跳过] 模块 {mod} 已经存在：{hits[0].name}")
        return 0
    path = ref / f"{mod}-{name}.md"
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
        "> 这个模块是空的：按自己的情况往里加条目，一条只记一个事实，格式见 `_模板.md`。\n"
    )
    if dry:
        print(f"[dry] 会新建 references/{path.name}（{len(text)} 字节）")
        return 0
    path.write_text(text, encoding="utf-8")
    print(f"✅ 新建模块 references/{path.name}")
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
                    help="新建一个自己的模块文件（如 00-我是谁），并登记路由表")
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
        # 原来直接 join 出「`00-我是谁.md`、`00-我是谁.md`、`00-我是谁.md`」三个重名。
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
