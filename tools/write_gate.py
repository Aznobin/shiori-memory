#!/usr/bin/env python3
"""写入闸门（2026-11-02 起是真闸门）：memwrite.py 每次写入前都过它，缺凭据的写入被拒。

证据锚（批 2，2026-11-02）：`add` / `replace` 必须带 `evidence`
（`source_file` / `source_line` / `quote`）。本模块自己去读 `source_file` 的第
`source_line` 行前后 `write.evidenceWindow` 行，要求 `quote` 逐字落在那个窗口里
（至少 `write.evidenceMinQuote` 字）；写入方自报的 `sha256` **只当「声称」比对，
不作为证据**。`retire` 走轻量锚（`reason` 必填，被删行的 sha256 由写入端自己算），
不要求 evidence。开关在 `tools/config.py` 的 `write.evidence`：
`enforce`（默认）/ `warn` / `off`。批次级 `expected_revision` 的形状也在这里校验，
实际比对由 `memwrite.py` 做。payload 也可以是对象形式：
`{"items": [...], "expected_revision": {...}, "idempotency_key": "...", "batch_id": "..."}`。

用法：
    python tools/write_gate.py payload.json     # 校验文件
    type payload.json | python tools/write_gate.py   # 校验 stdin
    python tools/write_gate.py --selftest       # 自带 15 条样本自测（含批 B、批 Q 规则）

输入：JSON 数组，每项形如
    {"module": "813", "op": "add|merge|weight", "keys": ["…"], "body": "…",
     "ids": ["813.012", "813.014"], "date": "2026-10-23"}

输出：{"ok": true, "normalized": [...], "warnings": [...]}
  或  {"ok": false, "errors": [{"index": 2, "field": "body", "reason": "含代码围栏 ```"}]}
退出码：通过 0 / 拒绝 1。WARN 不阻断，只进 `<home>/.agents/state/write_gate_warn.jsonl`。

写文件只有一处：WARN 日志（批 Q 2026-10-27 起）。正文本身不写、不 import frag.py，
模块白名单每次按 `memory/` + `references/` 实际文件名现取。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import write as write_cfg  # noqa: E402
from paths import SUBJECT_MODULE_IDS, module_files, resolve_module  # noqa: E402

# merge / weight 是设计里预留的两档，但写入端（memwrite.py 的 apply）至今没接：
# 闸门放行、写入端拒收，等于墙上贴着「已支持」，照墙办事的人一定撞。
# 2026-10-24 女儿审计第一条就是这个。闸门跟着收成真实支持的值。
#
# retire（2026-10-24 定的口径）：不是把行从 md 里抹掉，是在行尾挂
# 「（已退休 YYYY-MM-DD：理由）」——正文永远留着，frag 切片时跳过，memdb 标 deleted=1。
# 依据 `references/90` §4：本库没有容量压力（理由见 references/903）；她对自己写库的纪律也是
# 「禁用 DELETE，一律 UPDATE status」。理由必填，跟 source 一个逻辑。
OPS = ("add", "replace", "retire")

# source 五档（2026-10-24 shiori-old 定的，专治「会话里说话的人被默认当成库的主人」；
# 2026-10-26 批 B 加 subagent 档，让「子智能体转述」在检索打分里有独立口径）：
#   user-original    她本人原话，含她自己写下的文字（日记正文也算）
#   subagent         子智能体 / teammate 写的记录，不是她本人原话
#   test-transcript  测试 / 演练里的对话记录，说话的是测试员不是她
#   sample           造出来的样本数据
#   unknown          查过了、查不出来——必须同时给 note 说清为什么不知道
# 没有 unknown 这档只有两种下场：被硬标成真的，或者被拒在门外继续烂在旧文件夹里。
SOURCES = ("user-original", "subagent", "test-transcript", "sample", "unknown")
# 按「是不是关于人的断言」定强制范围，不按模块白名单凑数：
#   00/01/31 是人相关模块；11 也要，但它走行尾标（样本混在真日记里，模块级盖不住）。
#   90–99 那批机制件豁免——它们的来源是代码和文档，不是人。
SUBJECT_MODULES = SUBJECT_MODULE_IDS

MAX_KEY_CHARS = 12
MAX_BODY_LINES = 6
MAX_LINE_CHARS = 120
FENCE = "```"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FRAG_RE = re.compile(r"^\d{2}[a-z]?\.\d{3}$")
MOD_FILE_RE = re.compile(r"^(\d{2,3}[a-z]?)-.+\.md$")
# 标点、空白、全角符号：剔完还剩字才算「有实词」
NON_WORD_RE = re.compile(
    r"[\s\u3000-\u303f\uff00-\uffef!-/:-@\[-`{-~]+"
)
HAN_RE = re.compile(r"[\u4e00-\u9fff]")

# 状态延伸句（2026-10-27 批 Q，落的是 `references/902-自维护.md` §9.1「三条整合」的第三半条）：
# 一次观察被写成永久状态（「一直如此」「从来都」……）是最容易烂进库的假事实。
# 口径只做 WARN：附近有日期锚点就放行，没有就记一笔，绝不阻断写入。
STATUS_EXTEND_PHRASES = (
    "一直如此",
    "向来如此",
    "从来都",
    "一直都是",
    "从来都是",
    "永远都",
    "永远不会",
)
STATUS_EXTEND_RE = re.compile("|".join(re.escape(p) for p in STATUS_EXTEND_PHRASES))
DATE_ANCHOR_IN_TEXT_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
ANCHOR_WINDOW = 30
WARN_LOG = Path.home() / ".agents" / "state" / "write_gate_warn.jsonl"
LOG_WARNINGS = True

# ---- 写入侧四道闸的口径（2026-11-02 批 6）-----------------------------------
# 全部走 tools/config.py 的 `write` 段，骨架里不写死：下游改 memory.config.json 就能调，
# 不用改代码、也不会被下一次骨架置换覆盖。证据锚那两个数字是批 2 原型验过的默认值。
EVIDENCE_MODE = str(write_cfg("evidence", "enforce")).strip().lower()
EVIDENCE_WINDOW = int(write_cfg("evidenceWindow", 3))
EVIDENCE_MIN_QUOTE = int(write_cfg("evidenceMinQuote", 8))
MAX_IDEM_KEY_CHARS = 64
HEX16_RE = re.compile(r"^[0-9a-f]{16}$")


def module_ids() -> set[str]:
    """现有模块白名单：从 `memory/` 与 `references/` 实际文件名取前缀，不写死。"""
    ids: set[str] = set()
    for num, path in module_files().items():
        if MOD_FILE_RE.match(path.name):
            ids.add(num)
    # 2026-10-24：收件箱 `922-待确认inbox.md` 不以模块号开头，但它是合法的写入目标
    # （拿不准的内容先进收件箱），手册 §3 教的就是这个。漏了它 → 整批被闸门拒。
    if resolve_module("922") is not None:
        ids.add("922")
    return ids


def err(index: int, field: str, reason: str) -> dict:
    return {"index": index, "field": field, "reason": reason}


def warn(index: int, field: str, reason: str) -> dict:
    return {"index": index, "field": field, "level": "WARN", "reason": reason}


def check_status_extend(text: str, index: int, field: str = "body") -> list[dict]:
    """状态延伸句 + 30 字内无日期锚点 → WARN（批 Q）。只提醒，不拒收。"""
    warns: list[dict] = []
    for m in STATUS_EXTEND_RE.finditer(text):
        lo = max(0, m.start() - ANCHOR_WINDOW)
        hi = min(len(text), m.end() + ANCHOR_WINDOW)
        if DATE_ANCHOR_IN_TEXT_RE.search(text[lo:hi]):
            continue
        warns.append(
            warn(
                index,
                field,
                f"状态延伸句「{m.group(0)}」附近 {ANCHOR_WINDOW} 字内没有日期锚点（只提醒，不阻断）",
            )
        )
    return warns


def log_warnings(warns: list[dict], path: Path | None = None) -> None:
    """WARN 进日志：<home>/.agents/state/write_gate_warn.jsonl。失败绝不阻断写入。"""
    if not warns or not LOG_WARNINGS:
        return
    target = Path(path) if path else WARN_LOG
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        stamp = date.today().isoformat()
        with target.open("a", encoding="utf-8", newline="") as fh:
            for w in warns:
                row = {"at": stamp, **w}
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def has_word(text: str) -> bool:
    return bool(NON_WORD_RE.sub("", text))


def check_date(value, index: int, field: str) -> dict | None:
    if not isinstance(value, str) or not DATE_RE.match(value):
        return err(index, field, f"{field} 必须是 YYYY-MM-DD（拿到 {value!r}）")
    try:
        date.fromisoformat(value)
    except ValueError:
        return err(index, field, f"{field} 不是真实存在的日期（拿到 {value!r}）")
    return None


def check_evidence(item, index: int, root: Path | None = None):
    """证据锚（2026-11-02 批 6；来源 `931` §7-⑬ ／ dream `lib/evidence.js`）。

    口径：**只对来源是文件/文档的写入强制**——`add` / `replace` 必须能回查到**本库里
    的可见原文**：给来源文件、行号、那段原文，本函数自己读文件去核；写入方自报的
    `sha256` 只当「声称」比对，**永远不作为证据本身**。给了 `source`（user-original /
    test-transcript / sample / unknown）的对话来源不要求 evidence，按原有 `source`
    校验放行。`retire` 走轻量锚（`reason` 必填 + 被删行的 sha256 由写入端自己算），
    不要求 evidence。

    `write.evidence` 三档：`enforce`（缺就拒）/ `warn`（缺只提醒）/ `off`（完全不管）。
    返回 `(normalized_evidence | None, errors, warns)`。
    """
    warns: list[dict] = []
    _op = (item.get("op") or "").strip().lower() if isinstance(item.get("op"), str) else ""
    if _op not in ("add", "replace") or EVIDENCE_MODE == "off":
        return None, [], warns

    # `evidence` 只对「来源是文件/文档」的写入强制（2026-11-02 批 6，口径收紧）。
    # 对话来源——payload 给了 `source` 四档之一（user-original / test-transcript /
    # sample / unknown）——按原有 `source` 校验放行，**不强制**证据锚。两条理由都硬：
    #   ① 公开文档里印着不带 evidence 的示例 payload（`AGENTS.md` §2、`SKILL.md` §5：
    #      `{"module":"801","op":"add","keys":["名字"],"section":"我是谁","body":"…",
    #      "source":"user-original"}`），一律强制等于当场改坏已经发布出去、别的模型
    #      正在照抄的契约，违反「可升级置换架构优先」里的向后兼容；
    #   ② 对话里说的话本来就没有 file:line，硬要 evidence 只会逼调用方编一个假来源——
    #      那正是这道闸门要防的事。
    # 给了 `source` 就说明「这条的来源不是文件」；没给才按文件/文档来源要求回查。
    # 对话来源若**仍然**带了 evidence，照样严格核（你自己声称了 file:line 就得对得上）。
    _src = item.get("source")
    conversational = isinstance(_src, str) and _src.strip() in SOURCES

    ev = item.get("evidence")
    if conversational and not isinstance(ev, dict):
        return None, [], warns
    if not isinstance(ev, dict):
        if EVIDENCE_MODE == "warn":
            warns.append(warn(index, "evidence", "缺 evidence（只提醒，不阻断）"))
            return None, [], warns
        return None, [
            err(
                index,
                "evidence",
                "add / replace 必须带 evidence：{source_file, source_line, quote}"
                "——来源文件 + 行号 + 那段原文，闸门自己去读来核（批 2/批 6 口径）",
            )
        ], warns

    sf = ev.get("source_file")
    sl = ev.get("source_line")
    quote = ev.get("quote")
    if not isinstance(sf, str) or not sf.strip():
        return None, [err(index, "evidence", "evidence.source_file 必须是相对库根的字符串路径")], warns
    sf = sf.strip().replace("\\", "/")
    if isinstance(sl, bool) or not isinstance(sl, int) or sl < 1:
        return None, [err(index, "evidence", f"evidence.source_line 必须是 ≥1 的整数（拿到 {sl!r}）")], warns
    if not isinstance(quote, str) or len(quote.strip()) < EVIDENCE_MIN_QUOTE:
        got = len(quote.strip()) if isinstance(quote, str) else 0
        return None, [
            err(index, "evidence", f"evidence.quote 至少 {EVIDENCE_MIN_QUOTE} 个字（拿到 {got} 字）")
        ], warns

    base = Path(root) if root else ROOT
    path = base / sf
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        return None, [err(index, "evidence", f"evidence.source_file {sf!r} 跑到库根外面去了")], warns
    if not path.is_file():
        return None, [err(index, "evidence", f"evidence.source_file {sf!r} 读不到（库里没有这个文件）")], warns
    try:
        src_lines = path.read_text(encoding="utf-8").split("\n")
    except OSError as exc:
        return None, [err(index, "evidence", f"evidence.source_file {sf!r} 读取失败：{exc}")], warns

    lo = max(0, sl - 1 - EVIDENCE_WINDOW)
    hi = min(len(src_lines), sl + EVIDENCE_WINDOW)
    window = "\n".join(src_lines[lo:hi])
    q = quote.strip()
    if q not in window:
        actual = src_lines[sl - 1].strip()[:60] if sl - 1 < len(src_lines) else "(没有这一行)"
        return None, [
            err(
                index,
                "evidence",
                f"evidence.quote 在 {sf} 第 {sl} 行前后 {EVIDENCE_WINDOW} 行里找不到；"
                f"该行实际内容是：{actual}",
            )
        ], warns

    norm: dict = {"source_file": sf, "source_line": sl, "quote": q}
    claimed = ev.get("sha256")
    if isinstance(claimed, str) and claimed.strip():
        real = hashlib.sha256(q.encode("utf-8")).hexdigest()
        if claimed.strip().lower() != real:
            return None, [
                err(index, "evidence", "你自己报的 sha256 跟这段原文实测不一致（自证无效）")
            ], warns
        norm["sha256"] = real
    return norm, [], warns


def validate_item(item, index: int, mods: set[str], root: Path | None = None) -> tuple[dict | None, list[dict], list[dict]]:
    errors: list[dict] = []
    warns: list[dict] = []
    if not isinstance(item, dict):
        return None, [err(index, "", "条目不是 JSON 对象")], warns

    module = item.get("module")
    if module is None or module == "":
        errors.append(err(index, "module", "缺 module，不知道该写进哪个模块"))
    elif not isinstance(module, str):
        errors.append(err(index, "module", "module 必须是字符串模块号"))
    elif module.strip() not in mods:
        errors.append(
            err(index, "module", f"模块号 {module!r} 不在现有白名单里（references/ 下没有以它开头的模块文件）")
        )

    op = item.get("op")
    if not isinstance(op, str) or op.strip().lower() not in OPS:
        errors.append(err(index, "op", f"op 只许 {' / '.join(OPS)}（拿到 {op!r}）"))

    keys = item.get("keys")
    norm_keys: list[str] = []
    # op=retire 是软删（行尾挂「（已退休 …）」），不新增内容，所以不要求 keys / body。
    _op_low = (item.get("op") or "").strip().lower() if isinstance(item.get("op"), str) else ""
    if _op_low == "retire":
        pass
    elif not isinstance(keys, list):
        errors.append(err(index, "keys", "keys 必须是字符串数组"))
    elif not keys:
        errors.append(err(index, "keys", "keys 不能为空，至少要一个主题词"))
    else:
        for k in keys:
            if not isinstance(k, str):
                errors.append(err(index, "keys", "keys 里每一项都得是字符串"))
                continue
            kk = k.strip()
            if not kk:
                errors.append(err(index, "keys", "keys 里有空字符串"))
                continue
            if "\n" in k or "\r" in k:
                errors.append(err(index, "keys", f"keys 里 {kk!r} 带换行"))
                continue
            if len(kk) > MAX_KEY_CHARS:
                errors.append(
                    err(index, "keys", f"keys 里 {kk!r} 有 {len(kk)} 字，超过 {MAX_KEY_CHARS} 字上限")
                )
                continue
            if not has_word(kk):
                errors.append(err(index, "keys", f"keys 里 {kk!r} 只有标点，没有实词"))
                continue
            norm_keys.append(kk)

    body = item.get("body")
    norm_body = ""
    if _op_low == "retire":
        pass
    elif not isinstance(body, str):
        errors.append(err(index, "body", "body 必须是字符串"))
    else:
        text = body.replace("\r\n", "\n").strip("\n")
        lines = text.split("\n")
        if not text.strip():
            errors.append(err(index, "body", "body 不能为空"))
        else:
            if FENCE in text:
                errors.append(err(index, "body", "含代码围栏 ```，会切坏切片"))
            if any(line.strip() == "---" for line in lines):
                errors.append(err(index, "body", "有单独一行的 ---，会切坏 frontmatter"))
            if len(lines) > MAX_BODY_LINES:
                errors.append(
                    err(index, "body", f"body 有 {len(lines)} 行，超过 {MAX_BODY_LINES} 行上限")
                )
            for no, line in enumerate(lines, 1):
                if len(line) > MAX_LINE_CHARS:
                    errors.append(
                        err(
                            index,
                            "body",
                            f"body 第 {no} 行有 {len(line)} 字，超过 {MAX_LINE_CHARS} 字上限",
                        )
                    )
            norm_body = "\n".join(line.rstrip() for line in lines)
            warns.extend(check_status_extend(norm_body, index))

    source = item.get("source")
    norm_source = ""
    if isinstance(source, str) and source.strip():
        ss = source.strip()
        if ss not in SOURCES:
            errors.append(
                err(index, "source", f"source 只许 {' / '.join(SOURCES)}（拿到 {ss!r}）")
            )
        else:
            norm_source = ss
    elif source is not None and not isinstance(source, str):
        errors.append(err(index, "source", "source 必须是字符串"))
    # 人相关模块必须声明来源：这条错不是「存的时候没声明」造成的，
    # 是「内容进上下文时没人说它属于谁」，于是默认归给当前说话的人。
    # 闸门是第二道（第一道在注入卡片的归属行），但来源得在入库这一刻留下。
    if isinstance(module, str) and module.strip() in SUBJECT_MODULES and not norm_source:
        errors.append(
            err(
                index,
                "source",
                f"{module} 装的是关于人的断言，必须带 source（{' / '.join(SOURCES)}）",
            )
        )

    norm_ids: list[str] = []
    if op is not None and isinstance(op, str) and op.strip().lower() == "merge":
        ids = item.get("ids")
        if not isinstance(ids, list) or len(ids) < 2:
            errors.append(err(index, "ids", "op=merge 至少要有两个 frag id"))
        else:
            for fid in ids:
                if not isinstance(fid, str) or not FRAG_RE.match(fid.strip()):
                    errors.append(
                        err(index, "ids", f"ids 里 {fid!r} 不是 frag id（形如 813.012）")
                    )
                else:
                    norm_ids.append(fid.strip())
            if len(norm_ids) != len(set(norm_ids)):
                errors.append(err(index, "ids", "ids 里有重复的 frag id"))

    # 日期锚点（2026-10-26 批 B）：add / replace 的正文条目必须带日期。
    # 口径写死一种：缺 `date` 不拒收，自动补当天（ISO 格式），并标 `date_auto=True`，
    # 写入端按这个日期在正文行尾补 `（YYYY-MM-DD）` 锚点。retire/replace 的 old 定位行
    # 本身已经在库里，不重复补。
    auto_date = ""
    if _op_low in ("add", "replace") and not item.get("date"):
        auto_date = date.today().isoformat()

    for field in ("date", "updated"):
        if field in item:
            bad = check_date(item[field], index, field)
            if bad:
                errors.append(bad)

    # op=retire 必须带理由：删记忆比写记忆危险，不能让「退休」静悄悄发生。
    # 2026-11-02 修：这段原来在下面的 `if errors: return` 之后，error 被 `[]` 吃掉，
    # retire 缺 reason 会**静默放行**；搬到早退之前，它才真的挡得住。
    norm_reason = ""
    if _op_low == "retire":
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(
                err(index, "reason", "retire 必须给 reason：为什么让它退休（会写进行尾标记，以后能看见）")
            )
        else:
            norm_reason = reason.strip()

    # 幂等键（2026-11-02 批 6）：闸门只管形状，命不命中由 memwrite.py 查账。
    norm_ikey = ""
    ikey = item.get("idempotency_key")
    if ikey is not None and not isinstance(ikey, str):
        errors.append(err(index, "idempotency_key", "idempotency_key 必须是字符串"))
    elif isinstance(ikey, str) and ikey.strip():
        if len(ikey.strip()) > MAX_IDEM_KEY_CHARS:
            errors.append(
                err(
                    index,
                    "idempotency_key",
                    f"idempotency_key 有 {len(ikey.strip())} 字，超过 {MAX_IDEM_KEY_CHARS} 字上限",
                )
            )
        else:
            norm_ikey = ikey.strip()

    # 证据锚（2026-11-02 批 6）：前面已经挡住的条目不再多报一条证据错。
    norm_evidence = None
    if not errors:
        norm_evidence, ev_errs, ev_warns = check_evidence(item, index, root)
        errors.extend(ev_errs)
        warns.extend(ev_warns)

    if errors:
        return None, errors, warns

    normalized = {
        "module": module.strip(),
        "op": op.strip().lower(),
        "keys": norm_keys,
        "body": norm_body,
    }
    if norm_ids:
        normalized["ids"] = sorted(set(norm_ids))
    if norm_source:
        normalized["source"] = norm_source
    if auto_date:
        normalized["date"] = auto_date
        normalized["date_auto"] = True
    for field in ("date", "updated"):
        if field in item:
            normalized[field] = item[field]
    if "weight" in item:
        normalized["weight"] = item["weight"]
    # op=replace / retire 要靠 `old` 定位那一行，闸门得原样放行（2026-10-24）。
    old = item.get("old")
    if isinstance(old, str) and old.strip():
        normalized["old"] = old.strip()
    if norm_reason:
        normalized["reason"] = norm_reason
    if norm_ikey:
        normalized["idempotency_key"] = norm_ikey
    if norm_evidence:
        normalized["evidence"] = norm_evidence

    return normalized, [], warns


def validate(payload, root: Path | None = None) -> dict:
    """校验一份 payload：数组，或带 `items` 的对象形式（2026-11-02 批 6 起）。

    `root` 是证据锚读来源文件时用的库根；不给就用本文件所在库的 `ROOT`。
    对象形式另外认三件事：`expected_revision`（形状在这里校验，实际比对由 memwrite.py
    在任何写入之前做）、`idempotency_key`、`batch_id`——后两件原样收进返回体的 `batch`。
    """
    batch: dict = {}
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        items = payload["items"]
        for key in ("idempotency_key", "batch_id"):
            val = payload.get(key)
            if val is not None and not isinstance(val, str):
                return {"ok": False, "errors": [err(-1, key, f"{key} 必须是字符串")]}
            if isinstance(val, str) and val.strip():
                batch[key] = val.strip()
        rev = payload.get("expected_revision")
        if rev is not None:
            if not isinstance(rev, dict) or not rev:
                return {
                    "ok": False,
                    "errors": [
                        err(
                            -1,
                            "expected_revision",
                            "expected_revision 必须是 {相对路径: sha256 前 16 位} 的非空对象",
                        )
                    ],
                }
            for path_key, val in rev.items():
                if not isinstance(val, str) or not HEX16_RE.match(val.strip().lower()):
                    return {
                        "ok": False,
                        "errors": [
                            err(
                                -1,
                                "expected_revision",
                                f"expected_revision[{path_key!r}] 必须是 sha256 的前 16 位十六进制（拿到 {val!r}）",
                            )
                        ],
                    }
            batch["expected_revision"] = {str(k): v.strip().lower() for k, v in rev.items()}
    elif isinstance(payload, list):
        items = payload
    else:
        return {
            "ok": False,
            "errors": [err(-1, "payload", "顶层必须是 JSON 数组，或带 items 的对象")],
        }
    mods = module_ids()
    if not mods:
        return {
            "ok": False,
            "errors": [err(-1, "module", f"{REF} 下找不到任何模块文件，白名单是空的")],
        }
    normalized: list[dict] = []
    errors: list[dict] = []
    warns: list[dict] = []
    for index, item in enumerate(items):
        norm, errs, wns = validate_item(item, index, mods, root)
        warns.extend(wns)
        if errs:
            errors.extend(errs)
        else:
            normalized.append(norm)
    if errors:
        return {"ok": False, "errors": errors}
    log_warnings(warns)
    result: dict = {"ok": True, "normalized": normalized}
    if batch:
        result["batch"] = batch
    if warns:
        result["warnings"] = warns
    return result


GOOD = [
    {
        "module": "813",
        "op": "add",
        "keys": ["语气", "两档表态"],
        "body": "被问意见只有两档：同意 / 不同意。",
    },
    {
        "module": "901",
        "op": "replace",
        "keys": ["台账"],
        "body": "两行合成一行。",
        "old": "旧的那一行",
    },
    {
        "module": "915",
        "op": "add",
        "keys": ["评测"],
        "body": "加一条评测口径。",
        "date": "2026-10-23",
    },
    {
        "module": "903",
        "op": "add",
        "keys": ["检索"],
        "body": "第一行\n第二行\n第三行\n第四行\n第五行\n第六行",
        "updated": "2026-10-23",
    },
    {
        "module": "302",
        "op": "add",
        "keys": ["排障"],
        "body": "一行正文，" + "长" * 100,
        "date": "2026-10-23",
    },
]

BAD = [
    (
        "缺 module",
        {"op": "add", "keys": ["甲"], "body": "正文"},
        "module",
    ),
    (
        "嵌套围栏",
        {"module": "813", "op": "add", "keys": ["甲"], "body": "```text\n正文\n```"},
        "body",
    ),
    (
        "超长",
        {"module": "813", "op": "add", "keys": ["甲"], "body": "长" * 130},
        "body",
    ),
    (
        "keys 空",
        {"module": "813", "op": "add", "keys": [], "body": "正文"},
        "keys",
    ),
    (
        "日期错",
        {"module": "813", "op": "add", "keys": ["甲"], "body": "正文", "date": "2026/10/23"},
        "date",
    ),
    (
        "op 非法",
        {"module": "813", "op": "delete", "keys": ["甲"], "body": "正文"},
        "op",
    ),
    (
        "retire 缺 reason",
        {"module": "903", "op": "retire", "old": "旧的那一行"},
        "reason",
    ),
    (
        "缺证据锚",
        {"module": "903", "op": "add", "keys": ["甲"], "body": "正文"},
        "evidence",
    ),
]


def selftest() -> int:
    global LOG_WARNINGS
    prev_log = LOG_WARNINGS
    LOG_WARNINGS = False  # 自测只算不写：别往真日志里塞样本
    # 2026-10-24：样本里的模块号（89/82/97/90/08）在打包版里大半被 drop 掉了，
    # 原来直接照抄，换个库自测就红一片。改成按当前 references/ 实测白名单落位：
    # 白名单里有这个号就用它，没有就顺序借一个真实存在的模块。
    mods = module_ids()
    ordered = sorted(mods) or ["903"]

    def pick(prefer: str, i: int) -> str:
        return prefer if prefer in mods else ordered[i % len(ordered)]

    # 证据锚（2026-11-02 批 6）：自测自己造一个来源文件当「库里的可见原文」，
    # 不借真实记忆内容，换台机器也一样跑。模块白名单仍按真题的 references/ 取。
    import tempfile

    tmp_root = Path(tempfile.mkdtemp(prefix="write_gate_selftest_"))
    (tmp_root / "references").mkdir(parents=True, exist_ok=True)
    src_rel = "references/903-自测来源.md"
    src_lines = [
        "# 自测来源（write_gate --selftest 现造，不是真记忆）",
        "",
        "证据原文三（本文件第 3 行）：批 6 的证据锚自己去读来源文件。",
        "证据原文四（本文件第 4 行）：写入方自报的 sha256 只当「声称」比对。",
        "证据原文五（本文件第 5 行）：行号 + 原文对得上才算凭据。",
    ]
    (tmp_root / src_rel).write_text("\n".join(src_lines) + "\n", encoding="utf-8")

    def ev(line_no: int = 3) -> dict:
        return {
            "source_file": src_rel,
            "source_line": line_no,
            "quote": src_lines[line_no - 1],
        }

    good = []
    for i, sample in enumerate(GOOD):
        item = dict(sample)
        item["module"] = pick(str(item.get("module", "")), i)
        # 00/01/31 现在强制要 source：借到的模块正好是这三个时补一个默认档，
        # 否则「换个库自测就红」这个坑会再踩一次。
        if item["module"] in SUBJECT_MODULES and not item.get("source"):
            item["source"] = "user-original"
        # add / replace 现在必须带证据锚（批 6），样本统一挂自测来源。
        if item.get("op") in ("add", "replace") and not item.get("evidence"):
            item["evidence"] = ev()
        good.append(item)
    bad = []
    for i, (name, sample, field) in enumerate(BAD):
        item = dict(sample)
        if "module" in item:
            item["module"] = pick(str(item["module"]), i)
        bad.append((name, item, field))

    ok_count = 0
    total = 0
    failures: list[str] = []
    for no, sample in enumerate(good, 1):
        total += 1
        res = validate([sample], tmp_root)
        if res.get("ok") is True and res.get("normalized"):
            ok_count += 1
            print(f"  ✓ 好样本 {no} 通过")
        else:
            failures.append(f"好样本 {no} 应通过但被拒：{res.get('errors')}")
            print(f"  ✗ 好样本 {no} 被拒")
    for no, (name, sample, field) in enumerate(bad, 1):
        total += 1
        res = validate([sample], tmp_root)
        errs = res.get("errors") or []
        reasons = [e.get("reason", "") for e in errs]
        chinese = all(HAN_RE.search(r) for r in reasons) if reasons else False
        right_field = any(e.get("field") == field for e in errs)
        if res.get("ok") is False and right_field and chinese:
            ok_count += 1
            print(f"  ✓ 坏样本 {no}（{name}）被拒：{reasons[0]}")
        else:
            failures.append(
                f"坏样本 {no}（{name}）没按预期被拒：ok={res.get('ok')} errors={errs}"
            )
            print(f"  ✗ 坏样本 {no}（{name}）没按预期被拒")
    # 批 B 规则用例 ①：缺日期锚点 → 自动补当天，并真的落到正文行尾。
    total += 1
    today = date.today().isoformat()
    probe = {
        "module": pick("903", 0),
        "op": "add",
        "keys": ["日期锚点"],
        "body": "批 B 日期锚点自测。",
        "evidence": ev(),
    }
    res = validate([probe], tmp_root)
    norm = (res.get("normalized") or [{}])[0]
    try:
        import memwrite
        written = memwrite.body_with_marks(probe["body"], norm, today)
    except Exception:
        written = ""
    if (
        res.get("ok") is True
        and norm.get("date") == today
        and norm.get("date_auto") is True
        and f"（{today}）" in written
    ):
        ok_count += 1
        print(f"  ✓ 规则样本 1（缺日期锚点）自动补当天并落正文：{today}")
    else:
        failures.append(
            f"规则样本 1 应自动补当天 {today} 并落正文：ok={res.get('ok')} normalized={norm} written={written!r}"
        )
        print("  ✗ 规则样本 1（缺日期锚点）没自动补当天")

    # 批 B 规则用例 ②：低于出口阈的候选过一级闸、进日志，但不进上下文。
    total += 1
    try:
        import recall
        low = recall.ENTRY_SCORE + (recall.EXIT_SCORE - recall.ENTRY_SCORE) / 2
        two_level = recall.passes_entry(low) and not recall.passes_exit(low)
        card, fps = recall.build_card(
            [(low, "903", 1, "低于出口阈的候选行，不该进上下文。")],
            {"turn": 9, "seen": [], "recent": []},
            "selftest",
            no_random=True,
        )
        leaked = ("低于出口阈" in card) or bool(fps)
    except Exception as exc:
        two_level, leaked, low = False, True, "?"
        print(f"  ! 规则样本 2 加载 recall 失败：{exc}")
    if two_level and not leaked:
        ok_count += 1
        print(
            f"  ✓ 规则样本 2（{low:.3f}）过一级 {recall.ENTRY_SCORE}、"
            f"被二级 {recall.EXIT_SCORE} 挡住，只进日志"
        )
    else:
        failures.append(
            f"规则样本 2 应过一级、被二级挡住：two_level={two_level} leaked={leaked}"
        )
        print("  ✗ 规则样本 2（低于出口阈）没被挡住")

    # 批 Q 规则用例 ③：状态延伸句 + 附近无日期锚点 → 只记 WARN，不阻断写入。
    total += 1
    probe = {
        "module": pick("903", 0),
        "op": "add",
        "keys": ["状态延伸句"],
        "body": "她对甜食的好感一直都是这样。",
        "evidence": ev(),
    }
    res = validate([probe], tmp_root)
    got = res.get("warnings") or []
    if res.get("ok") is True and len(got) == 1 and got[0].get("level") == "WARN":
        ok_count += 1
        print(f"  ✓ 规则样本 3（状态延伸句无锚点）放行 + 1 条 WARN：{got[0].get('reason')}")
    else:
        failures.append(f"规则样本 3 应放行并报 1 条 WARN：ok={res.get('ok')} warnings={got}")
        print("  ✗ 规则样本 3（状态延伸句无锚点）没按预期报 WARN")

    # 批 Q 规则用例 ④：同一类句子带日期锚点 → 不报 WARN。
    total += 1
    probe = {
        "module": pick("903", 1),
        "op": "add",
        "keys": ["状态延伸句"],
        "body": "2026-10-27 核：她对甜食的好感一直都是这样。",
        "evidence": ev(),
    }
    res = validate([probe], tmp_root)
    got = res.get("warnings") or []
    if res.get("ok") is True and not got:
        ok_count += 1
        print("  ✓ 规则样本 4（带日期锚点）不报 WARN")
    else:
        failures.append(f"规则样本 4 不该报 WARN：ok={res.get('ok')} warnings={got}")
        print("  ✗ 规则样本 4（带日期锚点）误报了 WARN")

    # 批 6 规则用例 ⑤：add 不给证据锚 → 拒（field=evidence）。
    total += 1
    res = validate(
        [{"module": pick("903", 0), "op": "add", "keys": ["证据"], "body": "没有证据锚的正文。"}],
        tmp_root,
    )
    errs = res.get("errors") or []
    if res.get("ok") is False and any(e.get("field") == "evidence" for e in errs):
        ok_count += 1
        print(f"  ✓ 规则样本 5（无证据锚）被拒：{errs[0].get('reason')}")
    else:
        failures.append(f"规则样本 5 应因缺证据被拒：ok={res.get('ok')} errors={errs}")
        print("  ✗ 规则样本 5（无证据锚）没被拒")

    # 批 6 规则用例 ⑥：真证据锚 → 过，且 normalized 里原样带着它。
    total += 1
    res = validate(
        [
            {
                "module": pick("903", 0),
                "op": "add",
                "keys": ["证据"],
                "body": "带真证据锚的正文。",
                "evidence": ev(),
            }
        ],
        tmp_root,
    )
    norm = (res.get("normalized") or [{}])[0]
    if res.get("ok") is True and (norm.get("evidence") or {}).get("source_line") == 3:
        ok_count += 1
        print("  ✓ 规则样本 6（真证据锚）通过，并原样进 normalized")
    else:
        failures.append(f"规则样本 6 应通过并带 evidence：ok={res.get('ok')} normalized={norm}")
        print("  ✗ 规则样本 6（真证据锚）没通过")

    # 批 6 规则用例 ⑦：自报 sha256 编造 → 拒（自证无效）；报真值 → 放行。
    total += 1
    forged = ev()
    forged["sha256"] = "0" * 64
    res_forged = validate(
        [
            {
                "module": pick("903", 0),
                "op": "add",
                "keys": ["证据"],
                "body": "编造 sha256 的正文。",
                "evidence": forged,
            }
        ],
        tmp_root,
    )
    real_ev = ev()
    real_ev["sha256"] = hashlib.sha256(real_ev["quote"].encode("utf-8")).hexdigest()
    res_real = validate(
        [
            {
                "module": pick("903", 1),
                "op": "add",
                "keys": ["证据"],
                "body": "报真 sha256 的正文。",
                "evidence": real_ev,
            }
        ],
        tmp_root,
    )
    f_errs = res_forged.get("errors") or []
    if (
        res_forged.get("ok") is False
        and any("自证无效" in (e.get("reason") or "") for e in f_errs)
        and res_real.get("ok") is True
    ):
        ok_count += 1
        print(f"  ✓ 规则样本 7（自报 sha256）编造的被拒、真值放行：{f_errs[0].get('reason')}")
    else:
        failures.append(
            "规则样本 7 编造 sha256 应被拒（自证无效）、真值应放行："
            f"forged_ok={res_forged.get('ok')} forged={f_errs} real_ok={res_real.get('ok')}"
        )
        print("  ✗ 规则样本 7（自报 sha256）没按预期处理")

    # 批 6 规则用例 ⑧：retire 缺 reason → 拒。
    # 2026-11-02 修：原来这条 error 在 `if errors: return` 之后被丢掉，静默放行。
    total += 1
    res = validate([{"module": pick("903", 0), "op": "retire", "old": "随便一行"}], tmp_root)
    errs = res.get("errors") or []
    if res.get("ok") is False and any(e.get("field") == "reason" for e in errs):
        ok_count += 1
        print(f"  ✓ 规则样本 8（retire 缺 reason）被拒：{errs[0].get('reason')}")
    else:
        failures.append(f"规则样本 8 retire 缺 reason 应被拒：ok={res.get('ok')} errors={errs}")
        print("  ✗ 规则样本 8（retire 缺 reason）没被拒")

    # 批 6 规则用例 ⑨：对象形式 → batch 收下 expected_revision / idempotency_key / batch_id。
    total += 1
    rev_ok = {"references/903-自测来源.md": "0123456789abcdef"}
    res = validate(
        {
            "items": [
                {
                    "module": pick("903", 0),
                    "op": "add",
                    "keys": ["对象形式"],
                    "body": "对象形式的正文。",
                    "evidence": ev(),
                }
            ],
            "expected_revision": dict(rev_ok),
            "idempotency_key": "k-selftest-1",
            "batch_id": "b-selftest-1",
        },
        tmp_root,
    )
    batch = res.get("batch") or {}
    if (
        res.get("ok") is True
        and batch.get("idempotency_key") == "k-selftest-1"
        and batch.get("batch_id") == "b-selftest-1"
        and batch.get("expected_revision") == rev_ok
    ):
        ok_count += 1
        print("  ✓ 规则样本 9（对象形式）通过，batch 收下 expected_revision / idempotency_key")
    else:
        failures.append(
            f"规则样本 9 对象形式没按预期：ok={res.get('ok')} batch={batch} errors={res.get('errors')}"
        )
        print("  ✗ 规则样本 9（对象形式）没按预期")

    # 批 6 规则用例 ⑩：expected_revision 形状错 → 拒。
    total += 1
    res = validate(
        {
            "items": [
                {
                    "module": pick("903", 0),
                    "op": "add",
                    "keys": ["对象形式"],
                    "body": "正文。",
                    "evidence": ev(),
                }
            ],
            "expected_revision": {"references/903-自测来源.md": "NOT-A-SHA"},
        },
        tmp_root,
    )
    errs = res.get("errors") or []
    if res.get("ok") is False and any(e.get("field") == "expected_revision" for e in errs):
        ok_count += 1
        print(f"  ✓ 规则样本 10（expected_revision 形状错）被拒：{errs[0].get('reason')}")
    else:
        failures.append(f"规则样本 10 形状错应被拒：ok={res.get('ok')} errors={errs}")
        print("  ✗ 规则样本 10（expected_revision 形状错）没被拒")

    # 批 6 规则用例 ⑪：quote 对不上（原文不在窗口里）→ 拒，并打出该行实际内容。
    total += 1
    res = validate(
        [
            {
                "module": pick("903", 0),
                "op": "add",
                "keys": ["证据"],
                "body": "证据对不上的正文。",
                "evidence": {
                    "source_file": src_rel,
                    "source_line": 3,
                    "quote": "这段原文根本不在来源文件里，是编的。",
                },
            }
        ],
        tmp_root,
    )
    errs = res.get("errors") or []
    if res.get("ok") is False and any("找不到" in (e.get("reason") or "") for e in errs):
        ok_count += 1
        print(f"  ✓ 规则样本 11（quote 对不上）被拒：{errs[0].get('reason')}")
    else:
        failures.append(f"规则样本 11 quote 对不上应被拒：ok={res.get('ok')} errors={errs}")
        print("  ✗ 规则样本 11（quote 对不上）没被拒")

    # 规则样本 12（2026-11-02 口径收紧后的向后兼容判据）：对话来源、带 `source`、
    # 不带 evidence 的 payload **必须通过**——公开文档里印的就是这种形状
    # （`AGENTS.md` §2、`SKILL.md` §5 的示例 payload 没有 evidence）。
    res = validate(
        [
            {
                "module": "903",
                "op": "add",
                "keys": ["对话来源"],
                "body": "对话里她随口说的一句话，没有 file:line。",
                "source": "user-original",
            }
        ],
        tmp_root,
    )
    if res.get("ok") is True:
        ok_count += 1
        total += 1
        print("  ✓ 规则样本 12（对话来源带 source、不带 evidence）通过——向后兼容判据")
    else:
        total += 1
        failures.append(
            f"规则样本 12 对话来源应放行：ok={res.get('ok')} errors={res.get('errors')}"
        )
        print("  ✗ 规则样本 12（对话来源带 source、不带 evidence）被拒了")

    LOG_WARNINGS = prev_log
    print(f"selftest {ok_count}/{total}")
    if failures:
        for line in failures:
            print("  ! " + line)
        LOG_WARNINGS = prev_log
        return 1
    LOG_WARNINGS = prev_log
    return 0


def usage() -> str:
    return __doc__ or "python tools/write_gate.py payload.json"


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = [a for a in argv[1:] if a not in ("--selftest",)]
    if "--selftest" in argv:
        return selftest()
    if "-h" in argv or "--help" in argv:
        print(usage())
        return 0
    path = next((a for a in args if not a.startswith("-")), None)
    if path:
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            print(json.dumps(
                {"ok": False, "errors": [err(-1, "file", f"读不了输入文件：{exc}")]},
                ensure_ascii=False,
                indent=2,
            ))
            return 1
    else:
        raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps(
            {
                "ok": False,
                "errors": [err(-1, "json", f"不是合法 JSON：第 {exc.lineno} 行第 {exc.colno} 列 {exc.msg}")],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 1
    result = validate(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
