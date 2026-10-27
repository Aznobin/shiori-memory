#!/usr/bin/env python3
"""写入闸门（Stage 3-shadow）：只读校验器，不接任何写入流程。

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

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
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


def validate_item(item, index: int, mods: set[str]) -> tuple[dict | None, list[dict], list[dict]]:
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

    # op=retire 必须带理由：删记忆比写记忆危险，不能让「退休」静悄悄发生。
    op_norm = (item.get("op") or "").strip().lower()
    if op_norm == "retire":
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(
                err(index, "reason", "retire 必须给 reason：为什么让它退休（会写进行尾标记，以后能看见）")
            )
        else:
            normalized["reason"] = reason.strip()

    return normalized, [], warns


def validate(payload) -> dict:
    if not isinstance(payload, list):
        return {
            "ok": False,
            "errors": [err(-1, "payload", "顶层必须是 JSON 数组")],
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
    for index, item in enumerate(payload):
        norm, errs, wns = validate_item(item, index, mods)
        warns.extend(wns)
        if errs:
            errors.extend(errs)
        else:
            normalized.append(norm)
    if errors:
        return {"ok": False, "errors": errors}
    log_warnings(warns)
    result: dict = {"ok": True, "normalized": normalized}
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

    good = []
    for i, sample in enumerate(GOOD):
        item = dict(sample)
        item["module"] = pick(str(item.get("module", "")), i)
        # 00/01/31 现在强制要 source：借到的模块正好是这三个时补一个默认档，
        # 否则「换个库自测就红」这个坑会再踩一次。
        if item["module"] in SUBJECT_MODULES and not item.get("source"):
            item["source"] = "user-original"
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
        res = validate([sample])
        if res.get("ok") is True and res.get("normalized"):
            ok_count += 1
            print(f"  ✓ 好样本 {no} 通过")
        else:
            failures.append(f"好样本 {no} 应通过但被拒：{res.get('errors')}")
            print(f"  ✗ 好样本 {no} 被拒")
    for no, (name, sample, field) in enumerate(bad, 1):
        total += 1
        res = validate([sample])
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
    }
    res = validate([probe])
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
    }
    res = validate([probe])
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
    }
    res = validate([probe])
    got = res.get("warnings") or []
    if res.get("ok") is True and not got:
        ok_count += 1
        print("  ✓ 规则样本 4（带日期锚点）不报 WARN")
    else:
        failures.append(f"规则样本 4 不该报 WARN：ok={res.get('ok')} warnings={got}")
        print("  ✗ 规则样本 4（带日期锚点）误报了 WARN")

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
