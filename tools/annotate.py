#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""片段三段短标注：tag / caption / keywords（公开工具，build 与 dry-run 共用）。

口径（2026-10-27 定，`references/906` §片段标注）：

- tag      ：按模块编号分区推导，分区表在 `tools/paths.py`（`PARTITIONS`）；
             非模块产物（meme-pack-list / inspect-log / merge-candidates / 任何非数字 id）一律 `aux`
- caption  ：只取单行（禁止两行拼接）；时间轴行当噪音跳过；表格型片段用「小节标题：前两行首列内容」；
             **坏**只算「空 / 纯路径 / 纯时间轴」；短于 `CAPTION_SHORT`（10 字）算 needs_human 而非坏；
             超 30 字只按顿号/分号边界收口
- keywords ：手写 frontmatter keywords（只收正文里真出现的）→ 正文命中的主题词 → 正文内容词
             （凑不满 3 个时才用）→ 标题拆词 → 模块 keywords 兜底；
             泛词剔除只在已有 ≥3 个具体词时生效，不足 3 个按顺序补回；模块兜底最多 2 个；上限 8、去重

覆盖层：源文件里 `<!-- S:tag=…;caption=…;kw=… -->`（字段可只写一个）覆盖自动值，
写在被覆盖片段的首行上方，解析与剥离在 `tools/frag.py`。
"""
from __future__ import annotations

import json
import re

import paths

CAPTION_MAX = 30
CAPTION_MIN = 8      # 候选词先按这个长度筛，短于它的候选尽量不用
CAPTION_SHORT = 10   # 已写出的 caption 短于这个 → needs_human（不计坏）
KEYWORD_MAX = 8
KEYWORD_FALLBACK_MAX = 2   # 模块 keywords 兜底最多补 2 个
GENERIC_RATIO = 0.8

# 行首 markdown 标记：标题、引用、列表、表格竖线、有序列表号
LEAD_MARK = re.compile(r"^\s*(?:#{1,6}\s*|>\s*|[-*+]\s+|\d+[.)]\s+|\|)")
TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
CODE_FENCE = re.compile(r"^\s*```")
INLINE_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
INLINE_MARK = re.compile(r"[*_`~]")
SENT_END = re.compile(r"[。！？!?；;]")
TOKEN_SPLIT = re.compile(r"[^\w\u4e00-\u9fff]+")
BARE_FILE = re.compile(r"^[\w./\\-]+\.(?:md|jpg|jpeg|png|gif|webp|json|db|ps1|py|mjs|txt)$", re.IGNORECASE)
# 行首日期：`2026-10-19`、`2026-10-15（周四）：` 整段（含括号里的星期与跟在后面的冒号）一起剥
DATE_HEAD = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[（(][^）)]*[）)])?[：:、,，.。;；\s]*")
QUOTE_EDGE = re.compile(r"^[「『“\"'》>｜|\-–—\s]+|[」』”\"'《]+$")
PURE_NUM = re.compile(r"^[\d\s.．、,，()（）#\-]+$")
COUNT_SUFFIX = re.compile(r"[（(]\s*\d+\s*[）)]\s*$")
PATHISH = re.compile(r"[\\/]|[\w-]+\.(?:md|jpg|jpeg|png|gif|webp|json|db|ps1|py|mjs|txt)\b", re.IGNORECASE)
HTTP_LINK = re.compile(r"https?://|www\.", re.IGNORECASE)
# 行首带时间的括号前缀：`（2026-10-19 16:10）`、`（2026-10-26）`
TIME_PREFIX = re.compile(r"^[（(]\d{4}-\d{2}-\d{2}[^）)]*[）)][：:、,，.。;；\s]*")
PAIRS = (("（", "）"), ("(", ")"), ("「", "」"), ("『", "』"), ("《", "》"))
TAIL_FUNCTION = ("的", "了", "把", "和", "与", "在", "是", "就", "也", "都", "而", "并", "或", "你", "我", "她", "他", "它", "这", "那", "很")
STOPWORDS = {"的", "了", "和", "与", "或", "是", "在", "她", "他", "我", "你", "一", "这", "那", "个", "把", "被", "对", "从", "到"}
AUX_MODULES = {"meme-pack-list", "inspect-log", "merge-candidates"}
# meta 短语：这些不是关键词，是索引自己的说法（2026-10-27 她点的：「全表」「按…顺序」「她的名字全表」一律不要）
META_LEAD = ("她", "我", "你")
META_EXACT = {"全表", "全量", "按顺序"}

OVERRIDE_RE = re.compile(r"<!--\s*S:([^>]*?)-->")
OVERRIDE_FIELDS = {"tag", "caption", "kw"}


def tag_for(mod: str) -> str:
    """模块 id → 分区标签；非模块产物统一 aux。"""
    if mod in AUX_MODULES or not mod.isdigit():
        return "aux"
    return paths.partition_tag(mod)


def clean_line(line: str) -> str:
    s = line.strip()
    while LEAD_MARK.match(s):
        s = LEAD_MARK.sub("", s, count=1).strip()
    s = INLINE_LINK.sub(r"\1", s)
    s = INLINE_MARK.sub("", s)
    s = QUOTE_EDGE.sub("", s)
    s = TIME_PREFIX.sub("", s)
    s = DATE_HEAD.sub("", s)
    return s.strip().rstrip("：:").strip()


def unbalanced(s: str) -> bool:
    """括号/引号没成对（`（2026-10-26）当天实测（DSH…` 这类半截）判不合格。"""
    for op, cl in PAIRS:
        if s.count(op) != s.count(cl):
            return True
    if s.count("“") != s.count("”"):
        return True
    return False


def quote_risk(s: str) -> bool:
    """对话链（`…」→「…`）或长引文截断出来必是半句，不能当 caption。"""
    return bool(re.search(r"[→]", s)) or (len(s) > CAPTION_MAX and bool(re.search(r"[「」『』]", s)))


def is_time_only(s: str) -> bool:
    """纯时间轴行（`01:30～02:00+ 02:05～02:10`）当噪音，往下取第一条内容行。"""
    if not re.search(r"\d{1,2}[:：]\d{2}", s):
        return False
    return not re.sub(r"[\d:：\s~～+\-—－、,，./]", "", s)


def pure_path(s: str) -> bool:
    """整行只是路径/文件名/扩展名（没有中文主体）——这种才算坏。

    `<笔记库>\\日本語 这个文件夹放日语笔记` 有中文主体，是正常 caption，不许判坏。
    """
    if not s:
        return False
    if BARE_FILE.match(s):
        return True
    if not PATHISH.search(s):
        return False
    return len(re.findall(r"[\u4e00-\u9fff]", s)) < 2


def table_cells(raw: str) -> list[str]:
    s = clean_line(raw)
    if "|" not in s:
        return []
    return [c.strip() for c in s.strip("|").split("|") if c.strip()]


def citation_trim(s: str) -> str:
    """切到句子边界；无边界的整行只有 ≤30 字才直接可用，否则判为半句。"""
    m = SENT_END.search(s)
    if m:
        return s[: m.start() + 1].strip()
    return s if len(s) <= CAPTION_MAX else ""


def sentence_trim(s: str) -> str:
    return citation_trim(s)


def boundary_trim(s: str) -> str:
    """收口到 ≤30 字：从前往后取第一个完整分句（先强边界，再弱边界）。"""
    s = s.strip()
    if len(s) <= CAPTION_MAX:
        return s
    for chars, min_len in (("。！？；;：:", 4), ("，,、", 8)):
        for i, ch in enumerate(s):
            if i + 1 > CAPTION_MAX:
                break
            if ch in chars and i + 1 >= min_len:
                return s[:i].strip()
    # 最后退路：截到 30 字并剥掉尾部虚词；括号没闭合、剥完太短的都不用
    cut = s[:CAPTION_MAX].strip()
    while cut and cut[-1] in " 的了吧和与在是就也都而并或你我她他它这那很":
        cut = cut[:-1]
    if len(cut) >= 8 and not unbalanced(cut):
        return cut
    return ""


def pick_cell(row: list[str]) -> str:
    """表格行里挑一个能当 caption 的单元格：跳过日期/时间/编号/文件名。"""
    for i, raw in enumerate(row):
        if i == 0 and BARE_FILE.match(raw):
            continue
        c = QUOTE_EDGE.sub("", raw).strip()
        c = TIME_PREFIX.sub("", c)
        c = DATE_HEAD.sub("", c).strip()
        if not c or is_time_only(c) or PURE_NUM.match(c) or BARE_FILE.match(c) or unbalanced(c):
            continue
        if quote_risk(c):
            continue
        return c
    return ""


def table_caption(lines: list[str]) -> str:
    """表格型片段：`小节标题：前两行首列内容`，例 `硬禁忌：括号动作、AI 腔`。"""
    heading = ""
    cells: list[str] = []
    for raw in lines:
        if CODE_FENCE.match(raw) or TABLE_SEP.match(raw):
            continue
        row = table_cells(raw)
        if row:
            if not heading:
                continue  # 表前没有小标题，不当表格型处理
            c = pick_cell(row)
            if c:
                cells.append(c)
            if len(cells) >= 2:
                break
            continue
        s = clean_line(raw)
        if not s:
            continue
        if cells:  # 表格结束
            break
        heading = s if len(s) <= 20 and "。" not in s and not is_time_only(s) else ""
    if not heading or not cells:
        return ""
    return boundary_trim(f"{heading}：{'、'.join(cells)}")


def caption_candidate(raw: str) -> str:
    """一行 → 可用 caption 候选；不合格返回空串。"""
    if CODE_FENCE.match(raw) or TABLE_SEP.match(raw):
        return ""
    if HTTP_LINK.search(raw):
        return ""
    s = clean_line(raw)
    if not s:
        return ""
    if "|" in s:
        cells = [c.strip() for c in s.strip("|").split("|") if c.strip()]
        if not cells:
            return ""
        # 表格行：首格是文件名时用第二格，否则用第一格（不再糊成整行）
        s = cells[1] if BARE_FILE.match(cells[0]) and len(cells) > 1 else cells[0]
        s = QUOTE_EDGE.sub("", s).strip()
        s = DATE_HEAD.sub("", s).strip()
    if "|" in s:
        return ""
    if not s or BARE_FILE.match(s):
        return ""
    if unbalanced(s) or quote_risk(s):      # 括号/引号没成对的半截行当不了 caption
        return ""
    if re.match(r"^[\w.\-]*[\\/]", s):      # 以路径开头（`llm-deepseek\、skills\…`）当不了 caption
        return ""
    if pure_path(s):                        # 纯路径/文件名当不了 caption；夹在正文里的不算
        return ""
    if PURE_NUM.match(s) or is_time_only(s):
        return ""
    if len(re.sub(r"[^\u4e00-\u9fff\w]", "", s)) < 2:
        return ""
    return s


def caption_problem(cap: str) -> str:
    """硬坏：只有「空 / 纯路径 / 纯时间轴」三类（外加符号/链接/机器标记这类明显废行）。

    长度短不算硬坏——短于 `CAPTION_SHORT` 的交给 `caption_needs_human()` 标人工过一眼。
    """
    if not cap:
        return "空"
    if not re.search(r"[\w\u4e00-\u9fff]", cap):
        return "只剩符号"
    if HTTP_LINK.search(cap):
        return "含链接"
    if re.search(r"(BEGIN|END|AUTO:)", cap):
        return "机器标记"
    if unbalanced(cap):
        return "括号未闭合"
    if "|" in cap:
        return "残留表格竖线"
    if pure_path(cap):
        return "纯路径"
    if PURE_NUM.match(cap):
        return "只剩编号"
    m = DATE_HEAD.match(cap)
    if m and len(re.sub(r"[^\u4e00-\u9fff\w]", "", cap[m.end():])) < 4:
        return "只剩日期"
    if is_time_only(cap):
        return "纯时间轴"
    if len(cap) > CAPTION_MAX:
        return "超长"
    return ""


def bad_caption(cap: str) -> str:
    return caption_problem(cap)


def caption_needs_human(cap: str) -> bool:
    """短 caption（<10 字）也算要人过一眼，但不计坏。"""
    return bool(cap) and len(cap) < CAPTION_SHORT


def soft_bad(cap: str) -> str:
    """已写出的 caption 里值得人看一眼的软标（不是硬错，统计单列）。"""
    if not cap:
        return ""
    if len(cap) < CAPTION_SHORT:
        return "偏短"
    if pure_path(cap):
        return "纯路径"
    if is_time_only(cap):
        return "纯时间轴"
    if cap.endswith(("：", ":")):
        return "以冒号收尾"
    if len(cap) > CAPTION_MAX:
        return "超长"
    return ""


def make_caption(body: str) -> tuple[str, bool]:
    """返回 (caption, needs_human)。只取单行，宁可留空也不截半句、不拼接。"""
    lines = body.splitlines()
    cap = table_caption(lines)
    if cap and not caption_problem(cap):
        return cap, caption_needs_human(cap)

    cands: list[str] = []
    headings: list[str] = []
    for raw in lines:
        if CODE_FENCE.match(raw) or TABLE_SEP.match(raw):
            continue
        if "|" in raw:  # 表格行没有小标题时，退到能用的首格内容
            cell = pick_cell(table_cells(raw))
            if cell:
                cands.append(cell)
            if len(cands) >= 12:
                break
            continue
        s = caption_candidate(raw)
        if not s or is_time_only(s):
            continue
        (headings if re.match(r"^\s*#{1,6}\s", raw) else cands).append(s)
        if len(cands) + len(headings) >= 12:
            break
    cands.extend(headings)
    if not cands:
        return "", True

    for min_len in (CAPTION_MIN, 4):
        pool = cands if min_len == CAPTION_MIN else sorted(cands, key=len, reverse=True)
        for s in pool:
            if len(s) < min_len:
                continue
            if PURE_NUM.match(s) or pure_path(s):
                continue
            trimmed = sentence_trim(s)
            if not trimmed or len(trimmed) > CAPTION_MAX:
                trimmed = boundary_trim(s)
            trimmed = trimmed.rstrip("；;，,、").strip()
            if not trimmed or len(trimmed) > CAPTION_MAX:
                continue
            if caption_problem(trimmed):
                continue
            return trimmed, caption_needs_human(trimmed)
    return "", True


def body_terms(body: str, limit: int = 4) -> list[str]:
    """正文表格首格里的短词：只在前面凑不满 3 个关键词时兜底用。"""
    out: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        cells = table_cells(line)
        if not cells:
            continue
        head = INLINE_MARK.sub("", INLINE_LINK.sub(r"\1", cells[0])).strip()
        for piece in re.split(r"[／/、+\s]+", head):
            piece = norm_token(piece.strip().strip("：:"))
            if not (2 <= len(piece) <= 8):
                continue
            if re.search(r"[\d，,。；;：:（）()]", piece):
                continue
            if piece[0] in "的了和与把被在是就也都很" or piece[-1] in "的了把是在有会要被让给用做跑看说要想能来去上下里耗排前后":
                continue
            if not re.search(r"[\u4e00-\u9fff]", piece) and len(piece) < 4:
                continue
            if piece not in out:
                out.append(piece)
            if len(out) >= limit:
                return out
    return out


def norm_token(tok: str) -> str:
    t = (tok or "").strip().strip("`*_#").strip()
    # 「5. 硬边界」这类小标题带序号，序号剥掉（纯编号的那条在 push 里会被剔）
    t = re.sub(r"^\d+[.)、]\s*", "", t)
    return t.strip()


def meta_phrase(t: str) -> bool:
    """索引自己的说法（不是关键词）：「她的名字全表」「按她给的顺序」这类一律不要。"""
    if not t:
        return True
    if t in META_EXACT or "全表" in t:
        return True
    if "按" in t and "顺序" in t:
        return True
    if t.startswith(META_LEAD):
        return True
    if "的" in t:
        return True
    return False


def acceptable_token(tok: str) -> str:
    """规范 + 剔掉纯编号 / 计数后缀 / 路径扩展名 / meta 短语；不合格返回空串。"""
    t = norm_token(tok)
    if not t or t in STOPWORDS or len(t) < 2:
        return ""
    if meta_phrase(t):
        return ""
    if PURE_NUM.match(t) or COUNT_SUFFIX.search(t) or PATHISH.search(t):
        return ""
    if t.startswith((".", "/", "\\")) and not re.search(r"[\u4e00-\u9fff]{2,}", t):
        return ""
    if unbalanced(t):  # `记忆大类表（由` 这类被标题拆坏的括号残片
        return ""
    return t


def candidate_tokens(meta: dict, mod_kws: list[str]) -> list[str]:
    """泛词统计用：片段 keywords + 标题拆词 + 模块 keywords。"""
    out = [str(k) for k in meta.get("keywords") or []]
    out += TOKEN_SPLIT.split(str(meta.get("title", "")))
    out += TOKEN_SPLIT.split(str(meta.get("module_title", "")))
    out += mod_kws
    return out


def generic_tokens(metas: list[dict], mod_kws: list[str]) -> set[str]:
    """模块级泛词：该模块 ≥80% 片段都出现的候选词（片段 <3 个的模块不判）。"""
    from collections import Counter

    if len(metas) < 3:
        return set()
    counter: Counter[str] = Counter()
    for meta in metas:
        toks = {norm_token(t) for t in candidate_tokens(meta, mod_kws)}
        for t in toks:
            if t:
                counter[t] += 1
    threshold = GENERIC_RATIO * len(metas)
    return {t for t, c in counter.items() if c >= threshold}


def make_keywords(meta: dict, body: str, generic: set[str],
                  mod_kws: list[str], stats: dict | None = None) -> list[str]:
    """手写真出现的 → 正文命中主题词 → 内容词（凑不满 3 个时）→ 标题拆词 → 模块兜底（最多 2 个）。"""
    stats = stats if stats is not None else {}
    hits = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
    pool: list[str] = []

    def add(tok: str) -> None:
        t = acceptable_token(tok)
        if t and t not in pool:
            pool.append(t)

    for k in meta.get("keywords") or []:
        t = norm_token(str(k))
        if t and t in hits:      # 手写 keywords 只收正文里真出现过的
            add(str(k))
    for k in mod_kws + TOKEN_SPLIT.split(str(meta.get("module_title", ""))) + TOKEN_SPLIT.split(str(meta.get("title", ""))):
        t = norm_token(k)
        if t and t in hits:      # 正文里命中的主题词
            add(k)
    if len(pool) < 3:            # 内容词只在前面凑不满 3 个时补，免得把半句话当关键词
        for k in body_terms(body):
            add(k)
    for k in TOKEN_SPLIT.split(str(meta.get("title", ""))):
        add(k)

    concrete = [t for t in pool if t not in generic]
    if len(concrete) >= 3:
        kept = concrete
    else:                        # 泛词剔除只在已有 ≥3 个具体词时生效，否则按顺序补回来
        kept = pool
        stats["kw_泛词补回"] = stats.get("kw_泛词补回", 0) + sum(1 for t in kept if t in generic)
    out = kept[:KEYWORD_MAX]

    if len([t for t in out if t not in generic]) < 3:   # 兜底最多补 2 个模块 keywords
        added = 0
        for k in mod_kws:
            if added >= KEYWORD_FALLBACK_MAX or len(out) >= KEYWORD_MAX:
                break
            t = acceptable_token(k)
            if not t or t in out:
                continue
            out.append(t)
            added += 1
            stats["kw_模块兜底"] = stats.get("kw_模块兜底", 0) + 1
    if not out:                  # 硬下限：保证非空
        fallback = norm_token(str(meta.get("module_title") or meta.get("title") or meta.get("frag")))
        out = [fallback] if fallback else []
    return out[:KEYWORD_MAX]


def fallback_caption(title: str) -> str:
    """内容行都取不到 caption 时的最后兜底：用片段标题（≤30 字），拿不到就留空。"""
    s = clean_line(str(title or ""))
    s = re.sub(r"^[#>\-\*\s]+", "", s).strip()
    if not s:
        return ""
    return boundary_trim(s) if len(s) > CAPTION_MAX else s


def parse_override(text: str) -> dict:
    """解析 `<!-- S:tag=…;caption=…;kw=… -->`，字段可只写一个。"""
    out: dict = {}
    for m in OVERRIDE_RE.finditer(text):
        for piece in m.group(1).split(";"):
            if "=" not in piece:
                continue
            k, v = piece.split("=", 1)
            k = k.strip().lower()
            v = v.strip().strip('"').strip()
            if k in OVERRIDE_FIELDS and v:
                out[k] = v
    return out


def merge_override(auto: dict, override: dict) -> dict:
    """覆盖值优先；`kw` 支持顿号/逗号分隔。"""
    merged = dict(auto)
    if override.get("tag"):
        merged["tag"] = override["tag"].strip()
    if override.get("caption"):
        merged["caption"] = override["caption"].strip()
    if override.get("kw"):
        kws = [p.strip() for p in re.split(r"[、,，;；]+", override["kw"]) if p.strip()]
        if kws:
            merged["keywords"] = kws[:KEYWORD_MAX]
    return merged
