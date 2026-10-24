#!/usr/bin/env python3
"""decay.py —— 冷热分层（只读侧）：按 updated 算新鲜度，给每个片段打 active / cold / archived。

用法：
  python tools/decay.py           算一遍，写 state/decay.json（同一天重跑，文件字节不变）
  python tools/decay.py --pairs   出 references/_合并候选.md（只列候选，不合并）
  python tools/decay.py --show    只看统计，不写盘

口径（2026-10-23 定，别自己调）：
  - recency = 2 ** (-days / 90)，半衰期 90 天；`days` = 今天 - 片段 `updated`
  - recency < 0.5 → `cold`；`cold` 且 30 天内没有命中记录 → `archived`（冷到没人碰才归档）
  - `state/decay.json` 是状态的唯一事实源：`frag.py build` 整盘重建 index.json 时从这带回来
  - 幂等 by date：`lastTick` 只到日期，同一天连跑两遍输出完全一致

零依赖：只用标准库，不联网、不引向量库。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "fragments" / "index.json"
STATE = ROOT / "state" / "decay.json"
CANDIDATES = ROOT / "references" / "_合并候选.md"

HALF_LIFE_DAYS = 90.0      # 半衰期（Lead 2026-10-23 钉死：历史不满 30 天前不许调）
COLD_BELOW = 0.5           # recency 低于这个值算冷
ARCHIVE_QUIET_DAYS = 30    # 冷了之后多少天没命中就归档
SENSITIVE_MODULES = {"10", "22", "23"}   # 合并候选不碰敏感模块，免得用户的私事落进运维文档
WORDFUL = re.compile(r"[\u4e00-\u9fffA-Za-z]")   # 交集词里只留带字面的，纯编号 / 标点不算内容重叠

SAME_MODULE_COMMON = 2     # 合并候选：同模块内 keys 交集至少这么多（模块级公共词先剔掉再数）
TITLE_SIMILAR = 0.45       # 合并候选：标题相似度下限（difflib ratio）
UNIVERSAL_SHARE = 0.8      # 一个词被同模块 ≥80% 的片段都带着，就是模块标签，不算内容重叠
MAX_PAIRS = 40             # 合并候选：最多列这么多对


def _load_index() -> list[dict]:
    if not INDEX.exists():
        print("还没有切片，先跑 python tools/frag.py build", file=sys.stderr)
        raise SystemExit(1)
    return json.loads(INDEX.read_text(encoding="utf-8"))


def _load_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _days_since(today: date, stamp: str | None) -> int | None:
    if not stamp:
        return None
    try:
        return (today - date.fromisoformat(str(stamp)[:10])).days
    except ValueError:
        return None


def tick(day: date | None = None) -> dict:
    """算一遍状态，纯函数（只读 index.json + 旧 state，不写盘）。"""
    today = day or date.today()
    prev = _load_state().get("frags")
    prev_frags = prev if isinstance(prev, dict) else {}
    frags: dict[str, dict] = {}

    for item in sorted(_load_index(), key=lambda x: str(x.get("frag"))):
        fid = str(item.get("frag"))
        old = prev_frags.get(fid) if isinstance(prev_frags.get(fid), dict) else {}
        updated = str(item.get("updated") or "")
        last_seen = item.get("last_seen") or old.get("lastSeen")
        days = _days_since(today, updated)

        if days is None:
            recency, status = None, "active"
            reason = "updated 缺失或不合法，先保持 active"
        else:
            recency = 2 ** (-max(days, 0) / HALF_LIFE_DAYS)
            if recency >= COLD_BELOW:
                status = "active"
                reason = f"recency {recency:.2f} ≥ {COLD_BELOW}（{days} 天前改过）"
            else:
                quiet = _days_since(today, last_seen)
                if quiet is None or quiet > ARCHIVE_QUIET_DAYS:
                    status = "archived"
                    reason = (f"recency {recency:.2f} < {COLD_BELOW} 且 "
                              + ("从来没有命中记录" if quiet is None else f"已 {quiet} 天没命中"))
                else:
                    status = "cold"
                    reason = f"recency {recency:.2f} < {COLD_BELOW}，但 {quiet} 天内还命中过"

        frags[fid] = {
            "module": str(item.get("module")),
            "updated": updated,
            "days": days,
            "recency": None if recency is None else round(recency, 4),
            "status": status,
            "seen": int(item.get("seen") or 0),
            "lastSeen": last_seen,
            "reason": reason,
        }

    counts = {"active": 0, "cold": 0, "archived": 0}
    for info in frags.values():
        counts[info["status"]] = counts.get(info["status"], 0) + 1

    return {
        "version": 1,
        "halfLifeDays": HALF_LIFE_DAYS,
        "coldBelow": COLD_BELOW,
        "archiveQuietDays": ARCHIVE_QUIET_DAYS,
        "lastTick": today.isoformat(),
        "counts": counts,
        "frags": frags,
    }


def write_state(state: dict) -> Path:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                     encoding="utf-8")
    return STATE


def module_cap_report() -> list[tuple[str, int]]:
    per: dict[str, int] = {}
    for item in _load_index():
        mod = str(item.get("module"))
        per[mod] = per.get(mod, 0) + 1
    return sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))


def _universal(items: list[dict]) -> set[str]:
    """同模块里几乎人人都带着的词（模块标签）剔出去，否则「交集 ≥2」等于白送。"""
    seen: dict[str, int] = {}
    for item in items:
        for kw in set(item.get("keywords") or []):
            seen[kw] = seen.get(kw, 0) + 1
    need = max(2, math.ceil(len(items) * UNIVERSAL_SHARE))
    return {kw for kw, n in seen.items() if n >= need}


PART_TAIL_RE = re.compile(r"^(?P<base>.*?)[\s]*[（(][\s]*(?P<seq>[0-9０-９]+)[\s]*[)）][\s]*$")


def base_of(title: str) -> str:
    """去掉标题尾部「（n）」得到同源 base：base 相同 = 同一段被 MAX_LINES 切出来的兄弟片段。"""
    text = (title or "").strip()
    m = PART_TAIL_RE.match(text)
    if not m:
        return text
    return (m.group("base").strip() or text)


def base_of_item(item: dict) -> str:
    """优先用 build 写进 index 的 part.base，老索引没这字段时退回标题去尾。"""
    part = item.get("part")
    if isinstance(part, dict) and part.get("base"):
        return str(part["base"])
    return base_of(str(item.get("title") or ""))


def _pairs_doc(today: date | None = None) -> tuple[str, dict]:
    """同模块内、base 不同、keys 交集 ≥2、标题相似的对；另出切片组与 TOP 10。只出清单，一个字都不改。"""
    idx = _load_index()
    per_module: dict[str, list[dict]] = {}
    for item in idx:
        per_module.setdefault(str(item.get("module")), []).append(item)

    found: list[dict] = []
    groups: list[dict] = []
    for mod, items in per_module.items():
        if mod in SENSITIVE_MODULES:
            continue
        items = sorted(items, key=lambda x: str(x.get("frag")))
        by_base: dict[str, list[dict]] = {}
        for item in items:
            by_base.setdefault(base_of_item(item), []).append(item)
        for base, members in by_base.items():
            if len(members) > 1:
                groups.append({
                    "module": mod,
                    "base": base,
                    "n": len(members),
                    "ids": [str(m.get("frag")) for m in members],
                    "chars": sum(int(m.get("chars") or 0) for m in members),
                })
        common_tag = _universal(items)
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                ta, tb = str(a.get("title") or ""), str(b.get("title") or "")
                ba, bb = base_of_item(a), base_of_item(b)
                if ba == bb:
                    continue      # 同源切片，不是重复：只进切片组
                common = sorted(k for k in (set(a.get("keywords") or []) & set(b.get("keywords") or []))
                                - common_tag if WORDFUL.search(k))
                if len(common) < SAME_MODULE_COMMON:
                    continue
                ratio = SequenceMatcher(None, ta, tb).ratio()
                if ratio < TITLE_SIMILAR:
                    continue
                found.append({
                    "module": mod,
                    "a": str(a.get("frag")), "b": str(b.get("frag")),
                    "ba": ba, "bb": bb,
                    "chars": (int(a.get("chars") or 0), int(b.get("chars") or 0)),
                    "common": common,
                    "ratio": round(ratio, 3),
                })

    found.sort(key=lambda d: (-len(d["common"]), -d["ratio"], d["module"], d["a"], d["b"]))
    top = found[:MAX_PAIRS]
    groups.sort(key=lambda g: (-g["n"], g["module"], g["base"]))
    groups_top = groups[:10]
    max_parts = groups[0]["n"] if groups else 0
    covered = sum(g["n"] for g in groups)
    caps = module_cap_report()
    over = [(m, n) for m, n in caps if n > 40]
    day = (today or date.today()).isoformat()
    if over:
        cap_line = "- 40 片段上限：**超标** " + "、".join(f"`{m}`={n}" for m, n in over) + "，该拆了"
    elif caps:
        cap_line = f"- 40 片段上限：**没有超标**，最大的还是 `{caps[0][0]}` = {caps[0][1]} 个片段"
    else:
        cap_line = "- 40 片段上限：还没有片段"
    if groups:
        biggest = groups[0]
        thin_line = (f"- 最碎 base：`{biggest['module']}` {biggest['base']} 切了 {biggest['n']} 片"
                     f"（{biggest['chars']} 字）")
    else:
        thin_line = "- 最碎 base：没有 base 被切成两片以上"

    out = [
        "---",
        'id: "merge-candidates"',
        'title: "合并候选清单"',
        "keywords:",
        "  - 合并候选",
        "  - 片段去重",
        'sensitive: "false"',
        'always: "false"',
        'maintain_only: "true"',
        f'updated: "{day}"',
        "distribute: drop",
        "---",
        "",
        "# 合并候选清单",
        "",
        f"> 由 `python tools/decay.py --pairs` 生成，{day}。**只列候选，不合并**——合不合、怎么合等助手批。",
        "",
        "判据：同模块内 `keywords` 交集 ≥ "
        f"{SAME_MODULE_COMMON}（先剔掉连 80% 片段都带的模块标签词）、标题相似度（difflib）≥ {TITLE_SIMILAR}，"
        "并且**去掉标题尾部「（n）」后的 base 必须不同**——base 相同的算同源切片（`MAX_LINES` 机械切出来的兄弟片段），"
        f"只进切片组、不进候选。按「交集词数 → 相似度」降序，最多 {MAX_PAIRS} 对。敏感模块（`10` `22` `23`）两张表都不进。",
        "",
        f"- 真重复候选：**{len(found)} 对**（本次列 {len(top)} 对）",
        f"- 切片组：**{len(groups)} 组**，共 {covered} 个片段（{len(idx)} 条里的 {covered * 100 // max(1, len(idx))}%）",
        thin_line,
        f"- 模块数：{len(caps)}；片段最多的五个：" + "、".join(f"`{m}`={n}" for m, n in caps[:5]),
        cap_line,
        "",
        "## 真重复候选",
        "",
        "| 模块 | frag A | frag B | base A | base B | 字数 | 交集词 | 标题相似度 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for d in top:
        words = "、".join(d["common"]).replace("|", "\\|")
        out.append(f"| `{d['module']}` | `{d['a']}` | `{d['b']}` | {d['ba'].replace('|', '/')} | "
                   f"{d['bb'].replace('|', '/')} | {d['chars'][0]} / {d['chars'][1]} | {words} | {d['ratio']} |")
    if not top:
        out.append("| — | — | — | 0 对 | — | — | — | — |")

    out += [
        "",
        "## 切片组",
        "",
        "> base 相同的片段是同一段被切开的兄弟片段，**不是重复**；合并候选一律不含它们。",
        "",
        "| 模块 | base | 片数 | frag id | 总字数 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for g in groups:
        ids = " ".join(f"`{i}`" for i in g["ids"])
        out.append(f"| `{g['module']}` | {g['base'].replace('|', '/')} | {g['n']} | {ids} | {g['chars']} |")
    if not groups:
        out.append("| — | 没有 base 相同的组 | 0 | — | — |")

    out += [
        "",
        "## 切片组 TOP 10",
        "",
        "> 按片数降序，用来判 `MAX_LINES` 要不要调（本轮不动）。",
        "",
        "| # | 模块 | base | 片数 | 总字数 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for no, g in enumerate(groups_top, 1):
        out.append(f"| {no} | `{g['module']}` | {g['base'].replace('|', '/')} | {g['n']} | {g['chars']} |")
    if not groups_top:
        out.append("| — | — | 没有切片组 | 0 | — |")
    out.append("")

    stats = {"pairs": len(found), "listed": len(top), "groups": len(groups),
             "max_parts": max_parts, "covered": covered}
    return "\n".join(out), stats


def pairs_markdown(today: date | None = None) -> str:
    text, _ = _pairs_doc(today)
    return text


def write_pairs(day: date | None = None) -> tuple[Path, dict]:
    text, stats = _pairs_doc(day)
    CANDIDATES.write_text(text, encoding="utf-8")
    return CANDIDATES, stats


def show(state: dict) -> None:
    frags = state.get("frags") or {}
    counts = state.get("counts") or {}
    print(f"冷热分层（{state.get('lastTick')}，半衰期 {HALF_LIFE_DAYS:g} 天）")
    print(f"  片段 {len(frags)} 个：active {counts.get('active', 0)} / "
          f"cold {counts.get('cold', 0)} / archived {counts.get('archived', 0)}")
    cold = [f for f, i in frags.items() if i.get("status") != "active"]
    if cold:
        print("  非 active：" + "、".join(f"`{f}`({frags[f]['status']})" for f in cold[:14]))
    aged = sorted((i for i in frags.values() if i.get("days") is not None),
                  key=lambda x: -x["days"])[:3]
    if aged:
        print("  最老的三个：" + "、".join(f"{i['days']} 天（recency {i['recency']}）" for i in aged))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="冷热分层与合并候选（只读侧）")
    ap.add_argument("--pairs", action="store_true", help="只出 references/_合并候选.md")
    ap.add_argument("--show", action="store_true", help="只打印统计，不写 state")
    ap.add_argument(
        "--tick",
        action="store_true",
        help="结算一次冷热状态（这是默认动作，留着只是为了跟文档/流程图里的写法对齐）",
    )
    args = ap.parse_args(argv)

    if args.pairs:
        path, stats = write_pairs()
        print(f"真重复候选 {stats['pairs']} 对 / 切片组 {stats['groups']} 组"
              f"（最碎 {stats['max_parts']} 片）→ {path}")
        return 0

    state = tick()
    if not args.show:
        path = write_state(state)
        show(state)
        print(f"状态：{path}")
    else:
        show(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
