#!/usr/bin/env python3
"""写入闸门（Stage 3-shadow）：只读校验器，不接任何写入流程。

用法：
    python tools/write_gate.py payload.json     # 校验文件
    type payload.json | python tools/write_gate.py   # 校验 stdin
    python tools/write_gate.py --selftest       # 自带 11 条样本自测

输入：JSON 数组，每项形如
    {"module": "89", "op": "add|merge|weight", "keys": ["…"], "body": "…",
     "ids": ["89.012", "89.014"], "date": "2026-10-23"}

输出：{"ok": true, "normalized": [...]}
  或  {"ok": false, "errors": [{"index": 2, "field": "body", "reason": "含代码围栏 ```"}]}
退出码：通过 0 / 拒绝 1。

零依赖、只读：不写文件、不 import frag.py，模块白名单每次从 references/ 实际文件名现取。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "references"

OPS = ("add", "merge", "weight")
MAX_KEY_CHARS = 12
MAX_BODY_LINES = 6
MAX_LINE_CHARS = 120
FENCE = "```"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FRAG_RE = re.compile(r"^\d{2}[a-z]?\.\d{3}$")
MOD_FILE_RE = re.compile(r"^(\d{2}[a-z]?)-.+\.md$")
# 标点、空白、全角符号：剔完还剩字才算「有实词」
NON_WORD_RE = re.compile(
    r"[\s\u3000-\u303f\uff00-\uffef!-/:-@\[-`{-~]+"
)
HAN_RE = re.compile(r"[\u4e00-\u9fff]")


def module_ids() -> set[str]:
    """现有模块白名单：从 references/*.md 文件名取前缀，不写死。"""
    ids: set[str] = set()
    for path in REF.glob("*.md"):
        m = MOD_FILE_RE.match(path.name)
        if m:
            ids.add(m.group(1))
    return ids


def err(index: int, field: str, reason: str) -> dict:
    return {"index": index, "field": field, "reason": reason}


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


def validate_item(item, index: int, mods: set[str]) -> tuple[dict | None, list[dict]]:
    errors: list[dict] = []
    if not isinstance(item, dict):
        return None, [err(index, "", "条目不是 JSON 对象")]

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
        errors.append(err(index, "op", f"op 只许 add / merge / weight（拿到 {op!r}）"))

    keys = item.get("keys")
    norm_keys: list[str] = []
    if not isinstance(keys, list):
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
    if not isinstance(body, str):
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

    norm_ids: list[str] = []
    if op is not None and isinstance(op, str) and op.strip().lower() == "merge":
        ids = item.get("ids")
        if not isinstance(ids, list) or len(ids) < 2:
            errors.append(err(index, "ids", "op=merge 至少要有两个 frag id"))
        else:
            for fid in ids:
                if not isinstance(fid, str) or not FRAG_RE.match(fid.strip()):
                    errors.append(
                        err(index, "ids", f"ids 里 {fid!r} 不是 frag id（形如 89.012）")
                    )
                else:
                    norm_ids.append(fid.strip())
            if len(norm_ids) != len(set(norm_ids)):
                errors.append(err(index, "ids", "ids 里有重复的 frag id"))

    for field in ("date", "updated"):
        if field in item:
            bad = check_date(item[field], index, field)
            if bad:
                errors.append(bad)

    if errors:
        return None, errors

    normalized = {
        "module": module.strip(),
        "op": op.strip().lower(),
        "keys": norm_keys,
        "body": norm_body,
    }
    if norm_ids:
        normalized["ids"] = sorted(set(norm_ids))
    for field in ("date", "updated"):
        if field in item:
            normalized[field] = item[field]
    if "weight" in item:
        normalized["weight"] = item["weight"]
    return normalized, []


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
    for index, item in enumerate(payload):
        norm, errs = validate_item(item, index, mods)
        if errs:
            errors.extend(errs)
        else:
            normalized.append(norm)
    if errors:
        return {"ok": False, "errors": errors}
    return {"ok": True, "normalized": normalized}


GOOD = [
    {
        "module": "89",
        "op": "add",
        "keys": ["语气", "两档表态"],
        "body": "被问意见只有两档：同意 / 不同意。",
    },
    {
        "module": "82",
        "op": "merge",
        "keys": ["台账"],
        "body": "两行合成一行。",
        "ids": ["82.003", "82.004"],
    },
    {
        "module": "97",
        "op": "weight",
        "keys": ["评测"],
        "body": "加一条评测口径。",
        "date": "2026-10-23",
    },
    {
        "module": "90",
        "op": "add",
        "keys": ["检索"],
        "body": "第一行\n第二行\n第三行\n第四行\n第五行\n第六行",
        "updated": "2026-10-23",
    },
    {
        "module": "08",
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
        {"module": "89", "op": "add", "keys": ["甲"], "body": "```text\n正文\n```"},
        "body",
    ),
    (
        "超长",
        {"module": "89", "op": "add", "keys": ["甲"], "body": "长" * 130},
        "body",
    ),
    (
        "keys 空",
        {"module": "89", "op": "add", "keys": [], "body": "正文"},
        "keys",
    ),
    (
        "日期错",
        {"module": "89", "op": "add", "keys": ["甲"], "body": "正文", "date": "2026/10/23"},
        "date",
    ),
    (
        "op 非法",
        {"module": "89", "op": "delete", "keys": ["甲"], "body": "正文"},
        "op",
    ),
]


def selftest() -> int:
    ok_count = 0
    total = 0
    failures: list[str] = []
    for no, sample in enumerate(GOOD, 1):
        total += 1
        res = validate([sample])
        if res.get("ok") is True and res.get("normalized"):
            ok_count += 1
            print(f"  ✓ 好样本 {no} 通过")
        else:
            failures.append(f"好样本 {no} 应通过但被拒：{res.get('errors')}")
            print(f"  ✗ 好样本 {no} 被拒")
    for no, (name, sample, field) in enumerate(BAD, 1):
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
    print(f"selftest {ok_count}/{total}")
    if failures:
        for line in failures:
            print("  ! " + line)
        return 1
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
