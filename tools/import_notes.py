#!/usr/bin/env python3
"""import_notes.py —— 把现成的 md / txt 笔记批量扫成记忆草稿

为什么有它：`memwrite.py` 一次只收一条，靠手写攒到几百条没人愿意干。
这个工具把「写」降级成「审」——扫一个目录，按标题和日期切块，关键词粗路由到
现有模块，全部落成一张草稿表；人只把认可的行标成 `OK`，再一次性灌进模块。

用法：
    python tools/import_notes.py <目录或文件>              # 只预览，不落盘
    python tools/import_notes.py <目录> --write            # 草稿写到 state/imports/
    python tools/import_notes.py --apply state/imports/x.md --dry   # 预演
    python tools/import_notes.py --apply state/imports/x.md         # 真写
    python tools/import_notes.py --selftest

草稿一律落在 `state/imports/`——`state/` 是机器状态区，不进记忆索引，也不进发行包。
真正落盘时才过 `write_gate` 闸门，和 `memwrite.py` 同一套校验，不会绕过。

零第三方依赖，只 import 同目录的 `memwrite.py` / `write_gate.py`。
退出码：成功 0 / 被闸门拒或写失败 1。
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from datetime import date, datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
REF = ROOT / "references"
IMPORT_DIR = ROOT / "state" / "imports"

SKIP_DIRS = {".git", "dist", "state", "fragments", "archive", "__pycache__", "node_modules", ".obsidian"}
SUFFIXES = {".md", ".markdown", ".txt"}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_BODY_LINES = 6
# 台账/清单类模块：内容是工具自己写进去的，不该当「你写的笔记」的路由目标（2026-10-24）
# 00 是身份页（我是谁 / 怎么称呼 / 想要什么语气），只许人自己写，导入一律不进。
ROUTE_SKIP = {"82", "00"}
# 日记里出现的技术名词会被这些模块吸走（排障、巡检、架构、迁移），但它们不是「日记该去的地方」
DIARY_NOISE = {"08", "83", "90", "91", "91a", "92", "92a", "93", "94", "95", "96", "97", "97a", "98", "99"}
DIARY_MODULE = "11"        # 日常记录：日记形态的内容默认归这里
DIARY_CONF = 1.6           # 低于这个置信度，就不跟「更像的模块」抢
MAX_LINE_CHARS = 120
MIN_BLOCK_CHARS = 12
# 「Git: 1 次」「字数: 3200」这类统计行不是记忆，别进库
SKIP_STAT_RE = re.compile(r"^.{0,14}[:：]\s*\d+(?:[.,]\d+)?\s*(?:次|条|个|分钟|小时|天|页|字|行|%|ms|s)?\s*$")
# 「翻译来源:」这种只有标签没有内容的半截行
SKIP_LABEL_RE = re.compile(r"^.{0,14}[:：]\s*$")

DATE_HEAD_RE = re.compile(r"^\s*#{0,6}\s*(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})")
HEAD_RE = re.compile(r"^\s{0,3}(#{1,4})\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
QUOTE_RE = re.compile(r"^\s*>\s?")
LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
WIKI_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
INLINE_CODE_RE = re.compile(r"`([^`]*)`")
BOLD_RE = re.compile(r"(\*\*|__)(.*?)\1")
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
ASCII_WORD_RE = re.compile(r"[a-z][a-z0-9_+#.\-]{1,}")
HAN_RE = re.compile(r"[\u4e00-\u9fff]")
PUNCT_RE = re.compile(r"[\s\u3000-\u303f\uff00-\uffef!-/:-@\[-`{-~]+")

STOP_HAN = set("的了是我你他用户它们在和有就不都也这那什么怎么如何可以一个我们他们你们它们还但而然后所以因为如果虽然可是已经正在要把被给对从到与及或又很更最太没不")
TABLE_SEP_RE = re.compile(r"^\|\s*:?-{3,}")


# ---------- 文本处理 ----------

def fold(text: str) -> str:
    """全角转半角，顺便去掉零宽字符。"""
    text = unicodedata.normalize("NFKC", text)
    return text.replace("\u200b", "").replace("\ufeff", "")


def tokens_of(text: str) -> list[str]:
    """中文 2-gram + 英文/数字词。够用，不引第三方分词。"""
    text = fold(text).lower()
    out: list[str] = []
    for word in ASCII_WORD_RE.findall(text):
        if len(word) >= 2:
            out.append(word)
    hz = "".join(HAN_RE.findall(text))
    for i in range(len(hz) - 1):
        gram = hz[i : i + 2]
        if gram[0] in STOP_HAN or gram[1] in STOP_HAN:
            continue
        out.append(gram)
    return out


def clean_line(line: str) -> str:
    """去掉 markdown 装饰，留下能读的正文。"""
    line = fold(line)
    line = LINK_RE.sub(lambda m: m.group(1), line)
    line = WIKI_RE.sub(lambda m: m.group(2) or m.group(1), line)
    line = INLINE_CODE_RE.sub(lambda m: m.group(1), line)
    line = BOLD_RE.sub(lambda m: m.group(2), line)
    line = QUOTE_RE.sub("", line)
    line = BULLET_RE.sub("", line)
    line = EMOJI_RE.sub("", line)
    line = line.replace("|", "／").strip()
    line = re.sub(r"\s{2,}", " ", line)
    if len(line) > MAX_LINE_CHARS:
        line = line[: MAX_LINE_CHARS - 1] + "…"
    return line


def strip_frontmatter(lines: list[str]) -> tuple[list[str], int]:
    """返回 (去 frontmatter 后的行, 起始行号 1-based)。"""
    if lines and lines[0].strip() == "---":
        for j in range(1, min(len(lines), 400)):
            if lines[j].strip() == "---":
                return lines[j + 1 :], j + 2
    return lines, 1


def date_from_title(title: str) -> str | None:
    m = DATE_HEAD_RE.match(title)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


# ---------- 切块 ----------

def split_blocks(path: Path) -> list[dict]:
    """一个文件 → 若干块：{date, text_lines, source}。"""
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        return []
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
    lines, base_no = strip_frontmatter(text.split("\n"))

    blocks: list[dict] = []
    cur_head = ""
    cur_date: str | None = None
    cur: list[tuple[int, str]] = []
    # 文件名带日期的，整篇都按日记处理（日记里的小标题不带日期，只看标题会漏）
    file_diary = bool(re.search(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})", path.name))

    def flush():
        nonlocal cur
        if cur:
            blocks.append(
                {
                    "head": cur_head,
                    "date": cur_date or fallback_date(path),
                    "diary": file_diary or cur_date is not None,
                    "lines": [t for _, t in cur],
                    "source": f"{path.name}:{cur[0][0]}",
                }
            )
        cur = []

    for offset, line in enumerate(lines):
        no = base_no + offset
        stripped = line.strip()
        if not stripped:
            continue
        m = HEAD_RE.match(line)
        if m:
            flush()
            cur_head = m.group(2)
            cur_date = date_from_title(cur_head)
            continue
        d = DATE_HEAD_RE.match(line)
        if d and len(BULLET_RE.sub("", stripped)) < 40:
            flush()
            cur_head = stripped
            cur_date = date_from_title(stripped)
            continue
        cleaned = clean_line(stripped)
        if cleaned and not SKIP_STAT_RE.match(cleaned) and not SKIP_LABEL_RE.match(cleaned):
            cur.append((no, cleaned))
    flush()
    return blocks


def frontmatter_date(path: Path) -> str | None:
    """读文件头 frontmatter 里的 date / updated / created。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    for line in text.split("\n")[1:60]:
        if line.strip() == "---":
            break
        m = re.match(r"^(date|updated|created)\s*:\s*['\"]?(\d{4}-\d{2}-\d{2})", line.strip())
        if m:
            return m.group(2)
    return None


def fallback_date(path: Path) -> str:
    """文件名里的日期 > frontmatter 的 date/updated > 修改时间。

    2026-10-24：原来只认文件名和 mtime —— 「十一月计划.md」这种没日期名字的旧笔记，
    导入后日期全变成「扫描当天」，日记流和检索时间线都错位（新设备演练第 3 条）。
    """
    m = re.search(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})", path.name)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    fm = frontmatter_date(path)
    if fm:
        return fm
    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()


def collect_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    out: list[Path] = []
    for p in sorted(target.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        out.append(p)
    return out


# ---------- 路由 ----------

def build_module_index() -> dict[str, dict]:
    """每个模块一份词袋：frontmatter keywords + 标题 + 正文。"""
    index: dict[str, dict] = {}
    for path in sorted(REF.glob("*.md")):
        if path.name.startswith("_"):
            continue
        m = re.match(r"^(\d{2}[a-z]?)", path.name)
        if not m:
            continue
        mod = m.group(1)
        # 2026-10-24：系统区（90-99）是协议与架构文档，不是「你写的笔记」该去的地方。
        # 以前日记类内容因为 90-99 词袋巨大被打到 99-架构大纲，等于把日常日记塞进设计文档
        # （新设备演练里 5 篇日记全进了 99/96，就是这个原因）。
        if mod.startswith("9"):
            continue
        # 2026-10-24 第二刀：82-重复问题台账也是「工具往里写」的台账，不是目录。
        # 它词袋最大，实测把 20 块日记吸走 13 块（82×13 · 11×4 · 12b×2 · 31×1），
        # 日记全进了问题台账，等于导入等于白导。
        if mod in ROUTE_SKIP:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        kw = re.search(r"^keywords:\s*$(.*?)(?=^\S|\Z)", text, re.M | re.S)
        kws = re.findall(r"^\s*-\s*(.+?)\s*$", kw.group(1), re.M) if kw else []
        title = re.search(r"^title:\s*(.+?)\s*$", text, re.M)
        bag: Counter = Counter()
        for tok in tokens_of(" ".join(kws)):
            bag[tok] += 8  # keywords 是最硬的信号
        if title:
            for tok in tokens_of(title.group(1)):
                bag[tok] += 5
        body = text.split("---", 2)[-1] if text.startswith("---") else text
        for tok in tokens_of(body[:20000]):
            bag[tok] += 1
        index[mod] = {"title": title.group(1) if title else path.stem, "bag": bag}
    return index


def build_df(index: dict[str, dict]) -> dict[str, int]:
    df: Counter = Counter()
    for item in index.values():
        for tok in item["bag"]:
            df[tok] += 1
    return dict(df)


def route(text_tokens: list[str], index: dict[str, dict], df: dict[str, int]) -> tuple[str | None, float, list[tuple[str, float]]]:
    """返回 (模块号, 置信度, top3)。置信度是最高分与第二名的比值。"""
    import math

    n = max(len(index), 1)
    tfs = Counter(text_tokens)
    scored: list[tuple[str, float]] = []
    for mod, item in index.items():
        s = 0.0
        for tok, cnt in tfs.items():
            if tok not in item["bag"]:
                continue
            idf = math.log((n + 1) / (df.get(tok, 1) + 0.5))
            s += idf * (1 + math.log(cnt)) * math.log(1 + item["bag"][tok])
        if s > 0:
            scored.append((mod, round(s, 2)))
    scored.sort(key=lambda x: -x[1])
    if not scored:
        return None, 0.0, []
    top = scored[0]
    second = scored[1][1] if len(scored) > 1 else 0.0
    conf = top[1] / second if second else 99.0
    return top[0], round(conf, 2), scored[:3]


def keys_of(text: str, limit: int = 3) -> list[str]:
    """挑最长的几个高频词当主题词，超 12 字截断。"""
    toks = tokens_of(text)
    if not toks:
        return []
    c = Counter(toks)
    picked: list[str] = []
    for tok, _ in c.most_common(12):
        if len(tok) > 12:
            continue
        picked.append(tok)
        if len(picked) >= limit:
            break
    return picked


# ---------- 草稿表 ----------

def to_rows(blocks: list[dict], index: dict[str, dict], df: dict[str, int], limit: int | None) -> list[dict]:
    rows: list[dict] = []
    for block in blocks:
        buf: list[str] = []
        for line in block["lines"]:
            buf.append(line)
            if len(buf) >= MAX_BODY_LINES:
                rows.append(make_row(block, buf, index, df))
                buf = []
        if buf:
            rows.append(make_row(block, buf, index, df))
        if limit and len(rows) >= limit:
            rows = rows[:limit]
            break
    return rows


def make_row(block: dict, body: list[str], index: dict[str, dict], df: dict[str, int]) -> dict:
    text = "\n".join(body)
    if len(PUNCT_RE.sub("", text)) < MIN_BLOCK_CHARS:
        return {"skip": True}
    mod, conf, top3 = route(tokens_of(text), index, df)
    # 2026-10-24：日记形态的块，弱置信或命中「排障/巡检/架构」这类噪音模块时归到日常记录。
    # 以前它们按词频散进 83×6 · 08×3 · 99×2 · 91 · 90，导入完日记全变成技术台账。
    if block.get("diary") and DIARY_MODULE in index and mod != DIARY_MODULE:
        if mod is None or mod in DIARY_NOISE or conf < DIARY_CONF:
            mod, conf = DIARY_MODULE, 1.2
    return {
        "skip": False,
        "date": block["date"],
        "module": mod or "_待确认",
        "conf": conf,
        "alt": top3[1][0] if len(top3) > 1 else "",
        "keys": keys_of(text),
        "body": body,
        "source": block["source"],
        "state": "待审" if (mod and conf >= 1.15) else "低置信",
    }


def esc(text: str) -> str:
    # 表格单元格里不能有 `|`；`‖` 是正文分隔符，正文自己带了要换个字，否则回读时被切开
    return text.replace("|", "／").replace("‖", "∣")


# 2026-10-24：正文原来用 `" / "` 拼进表格、回读时再 split —— 笔记里本来就常有
# `A / B` 这种写法，20 条一 apply 变成 40 个 bullet（新设备演练第 4 条）。
# 换成罕见字符对，正文里出现也只会被 esc() 换成 ∣，不会再当分隔符。
BODY_SEP = " ‖ "


def render_draft(rows: list[dict], source: str, scanned: int, files: int) -> str:
    today = date.today().isoformat()
    head = [
        "---",
        "id: imports",
        f"title: 导入草稿 {source}",
        "keywords:",
        "  - 导入",
        "  - 草稿",
        "sensitive: false",
        "always: false",
        f"updated: {today}",
        "distribute: reset",
        "---",
        "",
        f"# 导入草稿 · {source}",
        "",
        f"> 扫了 {files} 个文件、切出 {scanned} 块，路由结果如下。审完把要留的行「状态」改成 `OK`，然后：",
        "> `python tools/import_notes.py --apply <本文件> --dry` 先预演，去掉 `--dry` 再真写。",
        "> `低置信` = 最高分与第二名太接近，路由可能错，改模块号再标 `OK`。",
        "",
        "| # | 日期 | 模块 | keys | 内容 | 来源 | 状态 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines = list(head)
    n = 0
    for row in rows:
        if row.get("skip"):
            continue
        n += 1
        body = BODY_SEP.join(esc(x) for x in row["body"])
        keys = ", ".join(row["keys"])
        mod = row["module"] + (f" ({row['alt']})" if row["alt"] else "")
        lines.append(
            f"| {n} | {row['date']} | {mod} | {esc(keys)} | {body} | {esc(row['source'])} | {row['state']} |"
        )
    if n == 0:
        lines.append("| — | — | — | — | 没有切出可用内容 | — | — |")
    lines.append("")
    return "\n".join(lines)


def parse_draft(path: Path) -> list[dict]:
    """读草稿表，只返回状态为 OK 的行。"""
    items: list[dict] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if not line.startswith("|") or TABLE_SEP_RE.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7 or cells[0] in ("#", "—"):
            continue
        if cells[6].lower() not in ("ok", "✓", "通过", "已审"):
            continue
        cell_mod = cells[2].strip()
        m = re.match(r"^(\d{2}[a-z]?)", cell_mod)
        if m:
            module = m.group(1)
        elif cell_mod in ("_待确认", "待确认", "待定", "?"):
            # 2026-10-24：模块列允许写 `_待确认`（拿不准的内容先进收件箱，手册 §3 就是这么教的）。
            # 原来这里只认数字模块号，非数字直接 continue —— 标了 OK 的行凭空消失，
            # 27 条标 OK 只落地 26 条，而且一声不响（新设备演练发现的丢数据坑）。
            module = "_待确认"
        else:
            print(f"  [跳过] #{cells[0]} 模块列「{cell_mod}」既不是模块号也不是 _待确认")
            continue
        keys = [k.strip() for k in re.split(r"[,，]", cells[3]) if k.strip()]
        body = [b.strip() for b in cells[4].split(BODY_SEP) if b.strip()]
        items.append(
            {
                "module": module,
                "op": "add",
                "keys": keys,
                "body": "\n".join(body),
                "date": cells[1] if re.match(r"^\d{4}-\d{2}-\d{2}$", cells[1]) else None,
                "source": cells[5],
            }
        )
    for it in items:
        if not it["date"]:
            it.pop("date")
    return items


# ---------- 动作 ----------

def do_scan(target: Path, limit: int | None) -> tuple[list[dict], int, int]:
    files = collect_files(target)
    index = build_module_index()
    df = build_df(index)
    blocks: list[dict] = []
    for p in files:
        blocks.extend(split_blocks(p))
    rows = to_rows(blocks, index, df, limit)
    return rows, len(blocks), len(files)


def cmd_scan(target: Path, args) -> int:
    rows, blocks_n, files_n = do_scan(target, args.limit)
    live = [r for r in rows if not r.get("skip")]
    print(f"[扫描] {files_n} 个文件 → {blocks_n} 块 → {len(live)} 条草稿")
    if not live:
        return 0
    hit = Counter(r["module"] for r in live)
    print("[路由] " + " · ".join(f"{m or '?'}×{c}" for m, c in hit.most_common(8)))
    for r in live[: args.show]:
        tag = "" if r["conf"] >= 1.15 else " [低置信]"
        print(f"  {r['date']}  {r['module'] or '?':>3}  {r['body'][0][:60]}{tag}")
    if args.write:
        IMPORT_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{date.today().isoformat()}-{target.name or 'import'}.md"
        out = IMPORT_DIR / name
        out.write_text(render_draft(live, target.name or str(target), blocks_n, files_n), encoding="utf-8")
        print(f"[落盘] 草稿 {out.relative_to(ROOT)}（{len(live)} 行，审完用 --apply）")
    else:
        print("[提示] 加 --write 才会写草稿；--apply 只在审完标 OK 后用。")
    return 0


def cmd_apply(path: Path, dry: bool, mem_source: str = "user-original") -> int:
    import importlib.util

    spec = importlib.util.spec_from_file_location("_importer_memwrite", TOOLS / "memwrite.py")
    memwrite = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(memwrite)

    items = parse_draft(path)
    if not items:
        print(f"[跳过] {path.name} 里没有状态为 OK 的行（先把要留的行改成 OK）")
        return 0
    # 2026-10-24：以前 source（`文件名:行号`）只活在草稿里，apply 时被整条丢掉 ——
    # 结果「十一月计划.md」的正文是进了 11 模块，可全库 grep「十一月计划」零命中，
    # 用户根本追不回来源文件（新设备演练就是这么发现的）。现在把来源拼进条目末尾，
    # 跟库里「（源：日记）」「（源：本会话）」的老写法保持一致。
    payload = []
    for it in items:
        body = it["body"].rstrip()
        src = (it.get("source") or "").strip()
        if src and "（源：" not in body:
            body = f"{body}（源：{src}）"
        row = {"module": it["module"], "op": "add", "keys": it["keys"], "body": body,
               "source": mem_source}
        if "date" in it:
            row["date"] = it["date"]
        payload.append(row)
    gate, normalized = memwrite.prepare(payload)
    if gate.get("ok") is not True:
        print(f"[拒绝] 闸门没过，一条都没写（{len(gate.get('errors', []))} 处问题）：")
        for e in gate.get("errors", [])[:10]:
            print(f"  #{e.get('index')} {e.get('field')}: {e.get('reason')}")
        return 1
    result = memwrite.apply(ROOT, normalized, date.today().isoformat(), dry)
    memwrite.report(result, dry)
    if result.get("planned") and not dry:
        # 2026-10-24：apply 只写盘，不重建派生层 —— 写完检索还是旧的，用户以为没生效。
        # 跟 memwrite 主流程保持一致：build + index 并发跑。
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(memwrite.run, [sys.executable, "tools/frag.py", "build"], ROOT)
            f2 = ex.submit(memwrite.run, [sys.executable, "tools/memdb.py", "index"], ROOT)
            c1, o1 = f1.result()
            c2, o2 = f2.result()
        print(f"  {'✓' if c1 == 0 else '✗'} frag.py build：{(o1 or '').strip().splitlines()[-1] if o1 else ''}")
        print(f"  {'✓' if c2 == 0 else '✗'} memdb.py index：{(o2 or '').strip().splitlines()[-1] if o2 else ''}")
        if c1 != 0 or c2 != 0:
            return 1
    return 0 if not result.get("failed") else 1


def selftest() -> int:
    import tempfile

    ok = True
    sample = "# 2026-10-24 今日\n- 装了 Node 22，顺手把 pnpm 也升了\n- 修好了 recall 的 `expand_query`，权重降到 0.45\n\n## 随手记\n今天试了下新的检索打分，效果还行\n"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "2026-10-24-日记.md"
        p.write_text(sample, encoding="utf-8")
        blocks = split_blocks(p)
        assert blocks, "切块失败：一条都没切出来"
        assert blocks[0]["date"] == "2026-10-24", f"日期解析错：{blocks[0]['date']}"
        index = build_module_index()
        assert index, "模块索引为空"
        rows = to_rows(blocks, index, build_df(index), None)
        live = [r for r in rows if not r.get("skip")]
        assert live, "没有产出草稿行"
        r = live[0]
        assert r["keys"], "keys 为空"
        print(f"[selftest] 块 {len(blocks)} · 草稿 {len(live)} · 首条路由 {r['module']} keys={r['keys']}")
        draft = Path(td) / "draft.md"
        draft.write_text(render_draft(live, "selftest", len(blocks), 1), encoding="utf-8")
        parsed = parse_draft(draft)
        assert parsed == [], "未标 OK 却解析出条目"
        text = draft.read_text(encoding="utf-8").replace("| 待审 |", "| OK |").replace("| 低置信 |", "| OK |")
        draft.write_text(text, encoding="utf-8")
        parsed = parse_draft(draft)
        assert parsed, "标了 OK 却解析不出条目"
        assert parsed[0]["module"], "解析出的条目没模块号"
        print(f"[selftest] 草稿回读 OK：{len(parsed)} 条 · 首条 {parsed[0]['module']} keys={parsed[0]['keys']}")
    print("[selftest] 全部通过")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="把 md / txt 笔记批量扫成记忆草稿")
    ap.add_argument("target", nargs="?", help="要扫的目录或文件")
    ap.add_argument("--write", action="store_true", help="把草稿写到 state/imports/")
    ap.add_argument("--apply", metavar="草稿文件", help="把标了 OK 的草稿批量写进模块")
    ap.add_argument("--dry", action="store_true", help="配合 --apply 预演，不落盘")
    ap.add_argument(
        "--source",
        default="user-original",
        choices=["user-original", "test-transcript", "sample", "unknown"],
        help="导入的条目按哪种来源写（默认 user-original＝用户本人写的字；演练/样本转来的要改）",
    )
    ap.add_argument("--limit", type=int, default=None, help="最多产出多少条草稿")
    ap.add_argument("--show", type=int, default=12, help="预览前几条")
    ap.add_argument("--selftest", action="store_true", help="跑内置自检，不碰真实数据")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.apply:
        return cmd_apply(Path(args.apply), args.dry, args.source)
    if not args.target:
        ap.print_help()
        return 1
    target = Path(args.target).expanduser()
    if not target.exists():
        print(f"[拒绝] 路径不存在：{target}")
        return 1
    return cmd_scan(target, args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
