#!/usr/bin/env python3
"""decay.py —— 冷热分层（只读侧）：按 updated 算新鲜度，给每个片段打 active / cold / archived。

用法：
  python tools/decay.py           算一遍，写 state/decay.json（同一天重跑，文件字节不变）
  python tools/decay.py --pairs   出 references/924-合并候选清单.md（只列候选，不合并）
  python tools/decay.py --show    只看统计，不写盘
  python tools/decay.py --read F  记一笔「读过」（写 state/reads.json）
  python tools/decay.py --use F   记一笔「用到」（写 index.json 的 seen / last_seen）
  python tools/decay.py --reads   只打印「读过 / 用过」两档对照，不写盘

口径（2026-10-23 定，别自己调）：
  - recency = 2 ** (-days / 90)，半衰期 90 天；`days` = 今天 - 片段 `updated`
  - recency < 0.5 → `cold`；`cold` 且 30 天内没有命中记录 → `archived`（冷到没人碰才归档）
  - `state/decay.json` 是状态的唯一事实源：`frag.py build` 整盘重建 index.json 时从这带回来
  - 幂等 by date：`lastTick` 只到日期，同一天连跑两遍输出完全一致

读 ≠ 用（2026-11-02 批6 · 两档别混）：
  - 一档「读过」= 命中进了上下文。账在 `state/reads.json`，只涨 read_count / read_first /
    read_last，**绝不参与冷热判定**：连读三次不取用，状态 / recency / 排序 / decay.json
    一个字节都不变。
  - 二档「用到」= 之后真的被取用/引用。走现有 last_access 渠道：index 的 `seen` /
    `last_seen`，这条才是 `tick()` 判断 cold / archived 用的时间戳。
  - 一档写法（读过也写 last_seen）会把冷片段推热：旧片段读三次就从 archived 变 cold
    （batch5 c6 对照组实测）。所以读账既不落 index、也不落 decay.json。

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

try:  # Windows 控制台默认 GBK，自检里的 ✓ / ✗ 会 UnicodeEncodeError（2026-10-26 踩到）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import SENSITIVE_MODULE_IDS, SKELETON_DIR  # noqa: E402
import config as _config  # noqa: E402

INDEX = ROOT / "fragments" / "index.json"
STATE = ROOT / "state" / "decay.json"
CANDIDATES = SKELETON_DIR / "924-合并候选清单.md"
READS = ROOT / "state" / "reads.json"   # 「读过」账（2026-11-02 批6）：运行期数据，只落 state/

# 两档开关与上限走 config.py，配置里没写就用那边的默认值（老库没有这一段也照跑）
READS_ON = bool(_config.reads("enabled"))
READS_MAX_FRAGS = int(_config.reads("maxFrags"))
READS_KEEP = int(_config.reads("keep"))

HALF_LIFE_DAYS = 90.0      # 半衰期（Lead 2026-10-23 钉死：历史不满 30 天前不许调）
COLD_BELOW = 0.5           # recency 低于这个值算冷
ARCHIVE_QUIET_DAYS = 30    # 冷了之后多少天没命中就归档
SENSITIVE_MODULES = SENSITIVE_MODULE_IDS   # 合并候选不碰敏感模块，免得她的私事落进运维文档
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


# --------------------------------------------------------------- 读 ≠ 用（两档）
#
# 一档「读过」：命中进了上下文。只落 state/reads.json，谁都不影响。
# 二档「用到」：之后真的被取用/引用。走 index 的 seen / last_seen（现有 last_access 渠道）。
# 两档合起来才分得清「读过」与「用过」；decay.json 里一个字都不加，保住基线的字节一致。

def load_reads() -> dict[str, dict]:
    """「读过」账。缺文件 / 坏文件一律当空账——老库升上来就是这个形态，不许炸。"""
    if not READS.exists():
        return {}
    try:
        data = json.loads(READS.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _save_reads(book: dict[str, dict]) -> Path:
    """写回读账。超上限就按 read_last（并列按 frag id）倒序留最新的 READS_KEEP 条。"""
    if len(book) > READS_KEEP:
        order = sorted(book.items(),
                       key=lambda kv: (str(kv[1].get("read_last") or ""), kv[0]),
                       reverse=True)
        book = dict(order[:READS_KEEP])
    READS.parent.mkdir(parents=True, exist_ok=True)
    READS.write_text(json.dumps(book, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                     encoding="utf-8")
    return READS


def note_read(frags, at: date | None = None) -> int:
    """命中 → 记「读过」（两档第一档）。

    只写 state/reads.json 的 read_count / read_first / read_last，
    **绝不碰** index 的 seen / last_seen——那条留给「用到」。返回真正记账的片段数。
    """
    if not READS_ON:
        return 0
    # 同一轮里同一条只算一次：一轮卡片里重复出现不等于读了两遍。
    # dict.fromkeys 既去重又保序（顺序稳定，账文件 diff 好读）。
    ids = list(dict.fromkeys(str(f) for f in frags if str(f).strip()))[:READS_MAX_FRAGS]
    if not ids:
        return 0
    stamp = (at or date.today()).isoformat()
    book = load_reads()
    for fid in ids:
        cur = book.get(fid)
        if not isinstance(cur, dict):
            cur = {"read_count": 0, "read_first": stamp, "read_last": stamp}
            book[fid] = cur
        cur["read_count"] = int(cur.get("read_count") or 0) + 1
        if not cur.get("read_first"):
            cur["read_first"] = stamp
        cur["read_last"] = stamp
    _save_reads(book)
    return len(ids)


def note_use(frags, at: date | None = None) -> int:
    """取用 → 记「用到」（两档第二档）。

    写 index 的 `seen`（累加）/ `last_seen`（当天）：现有 last_access 渠道，不新增字段。
    """
    ids = {str(f) for f in frags if str(f).strip()}
    if not ids:
        return 0
    stamp = (at or date.today()).isoformat()
    items = _load_index()
    hit = 0
    for item in items:
        if str(item.get("frag")) in ids:
            item["seen"] = int(item.get("seen") or 0) + 1
            item["last_seen"] = stamp
            hit += 1
    if hit:
        INDEX.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return hit


def access_view(frags: dict) -> list[str]:
    """「读过 / 用过」两档的对照文字（只读，不写盘）。"""
    book = load_reads()
    used = sorted(f for f, i in frags.items() if int(i.get("seen") or 0) > 0)
    only_read = sorted(set(book) - set(used))
    newest = max((str(v.get("read_last") or "") for v in book.values()), default="")
    return [
        f"  读过（state/reads.json）：{len(book)} 个片段" + (f"，最近 {newest}" if newest else ""),
        f"  用过（index seen / last_seen）：{len(used)} 个片段"
        + (f"：{'、'.join('`' + f + '`' for f in used[:8])}" if used else ""),
        f"  只读过、没用过：{len(only_read)} 个"
        + (f"：{'、'.join('`' + f + '`' for f in only_read[:8])}" if only_read else ""),
    ]


def _last_use(item: dict, old: dict) -> str | None:
    """「用到」档的时间戳：index 的 `last_seen` > 旧 state 的 `lastSeen`。

    只认「用」。另一档「读过」在 `state/reads.json`（note_read 写），**绝不参与**这里的
    冷热判定——一档写法会把冷片段推热（batch5 c6 对照组实测 archived → cold）。
    """
    return item.get("last_seen") or old.get("lastSeen")


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
        last_seen = _last_use(item, old)
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
                # quiet 只看「用到」档（last_seen）；「读过」不在这里参与，见 _last_use。
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
        # 2026-10-26 引号链：日期不裹引号（`924-合并候选清单.md` 是 decay.py 自己重写的生成物，
        # 带引号会被 memory_check 的 updated 正则判成「没有可解析的 updated 日期」）
        f'updated: {day}',
        "distribute: drop",
        "---",
        "",
        "# 合并候选清单",
        "",
        f"> 由 `python tools/decay.py --pairs` 生成，{day}。**只列候选，不合并**——合不合、怎么合等用户批。",
        "",
        "判据：同模块内 `keywords` 交集 ≥ "
        f"{SAME_MODULE_COMMON}（先剔掉连 80% 片段都带的模块标签词）、标题相似度（difflib）≥ {TITLE_SIMILAR}，"
        "并且**去掉标题尾部「（n）」后的 base 必须不同**——base 相同的算同源切片（`MAX_LINES` 机械切出来的兄弟片段），"
        f"只进切片组、不进候选。按「交集词数 → 相似度」降序，最多 {MAX_PAIRS} 对。敏感模块（`701` `102` `211`）两张表都不进。",
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
    for line in access_view(frags):
        print(line)


def _selftest_two_tier() -> tuple[int, int]:
    """「读 ≠ 用」两档的 c1–c6 语义自检（2026-11-02 批6）。

    全程在临时目录里跑：INDEX / STATE / READS 三个模块级路径都临时指到 tmp，原库的
    index.json / decay.json / reads.json 一个字节都不碰。
    """
    global INDEX, STATE, READS

    import tempfile

    passes = 0
    total = 0

    def check(name, cond, extra=""):
        nonlocal passes, total
        total += 1
        if cond:
            passes += 1
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ {name} {extra}")

    day = date(2026, 11, 2)
    old_day = date.fromordinal(day.toordinal() - 200)   # recency 0.214 → 冷
    new_day = date.fromordinal(day.toordinal() - 5)     # recency 0.96  → 热
    used_day = date.fromordinal(day.toordinal() - 10)   # 30 天内，算「还热过」

    keep = (INDEX, STATE, READS)
    try:
        with tempfile.TemporaryDirectory(prefix="decay-selftest-") as tmp:
            root = Path(tmp)
            INDEX = root / "index.json"
            STATE = root / "decay.json"
            READS = root / "reads.json"
            INDEX.write_text(json.dumps([
                {"frag": "001.001", "module": "001", "updated": old_day.isoformat(),
                 "seen": 0, "status": "active", "last_seen": None},
                {"frag": "001.002", "module": "001", "updated": old_day.isoformat(),
                 "seen": 0, "status": "active", "last_seen": None},
                {"frag": "001.003", "module": "001", "updated": new_day.isoformat(),
                 "seen": 0, "status": "active", "last_seen": None},
            ], ensure_ascii=False), encoding="utf-8")

            # 老库形态：升上来没有 reads.json
            check("老库无 reads.json 时 load_reads() = 空账", load_reads() == {})
            check("老库无 reads.json 时 tick 照跑",
                  tick(day)["frags"]["001.002"]["status"] == "archived")

            # c1：只读过，不取用 → 读账涨，index 一个字节不动
            index_before = INDEX.read_bytes()
            note_read(["001.001"], day)
            check("c1 读过只写 state/reads.json，index 一个字节不动",
                  INDEX.read_bytes() == index_before)
            book = load_reads()
            check("c1 read_count = 1", book.get("001.001", {}).get("read_count") == 1, str(book))
            check("c1 read_first = read_last = 当天",
                  book.get("001.001", {}).get("read_first") == day.isoformat()
                  and book.get("001.001", {}).get("read_last") == day.isoformat())

            # c3：连读三次，状态 / recency / 用账全不变
            note_read(["001.001"], day)
            note_read(["001.001"], day)
            s_read = tick(day)
            check("c3 连读 3 次 read_count = 3", load_reads()["001.001"]["read_count"] == 3)
            check("c3 读过不改 status（仍是 archived）",
                  s_read["frags"]["001.001"]["status"] == "archived",
                  s_read["frags"]["001.001"]["reason"])
            check("c3 读过不写 lastSeen", s_read["frags"]["001.001"]["lastSeen"] is None)
            recency_read = s_read["frags"]["001.001"]["recency"]

            # c2 + c4：真取用之后，用账才动
            check("c2 note_use 命中 1 条", note_use(["001.001"], used_day) == 1)
            raw = json.loads(INDEX.read_text(encoding="utf-8"))
            check("c2 取用写 index seen = 1", raw[0]["seen"] == 1, str(raw[0]))
            check("c2 取用写 index last_seen", raw[0]["last_seen"] == used_day.isoformat())
            s_used = tick(day)
            check("c4 用过的老片段 = cold", s_used["frags"]["001.001"]["status"] == "cold")
            check("c4 没人碰过的老片段 = archived", s_used["frags"]["001.002"]["status"] == "archived")
            check("c4 新片段 = active", s_used["frags"]["001.003"]["status"] == "active")

            # c6：两档必须分得开——读过 ≠ 用过
            check("c6 两档状态不同（读过 archived / 用过 cold）",
                  s_read["frags"]["001.001"]["status"] == "archived"
                  and s_used["frags"]["001.001"]["status"] == "cold")
            check("c6 读过不动用账（seen 仍 0）", s_read["frags"]["001.002"]["seen"] == 0)
            check("c6 读过不改 recency", s_used["frags"]["001.001"]["recency"] == recency_read)

            # 幂等 by date：同一天连跑两遍，state 字节一致
            a = json.dumps(tick(day), ensure_ascii=False, indent=1, sort_keys=True)
            b = json.dumps(tick(day), ensure_ascii=False, indent=1, sort_keys=True)
            check("同一天重跑 tick 输出字节一致", a == b)

            # 坏账不许炸
            READS.write_text("{ 这不是 json", encoding="utf-8")
            check("读账坏掉时 load_reads() 退化成空账", load_reads() == {})
            check("读账坏掉时 tick 照跑", tick(day)["frags"]["001.003"]["status"] == "active")
    finally:
        INDEX, STATE, READS = keep
    return passes, total


def _selftest() -> int:
    """验引号链：`--pairs` 生成的 frontmatter 里 `updated` 不带引号，且体检能解析。"""
    passes = 0
    total = 0

    def check(name, cond, extra=""):
        nonlocal passes, total
        total += 1
        if cond:
            passes += 1
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ {name} {extra}")

    text = pairs_markdown(date(2026, 1, 2))
    head = text.split("---", 2)[1] if text.startswith("---") else text
    check("frontmatter 有 updated 行", "updated: 2026-01-02" in head, head)
    check("updated 不带引号（A3）", 'updated: "2026-01-02"' not in head)

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from memory_check import parse_updated  # noqa: E402

        check("memory_check 能解析这份 frontmatter", parse_updated("---\n" + head + "\n---\n") == "2026-01-02")
    except Exception as exc:  # 体检脚本改名时不当哑巴
        check("memory_check 能解析这份 frontmatter", False, repr(exc))

    p2, t2 = _selftest_two_tier()
    passes += p2
    total += t2

    print(f"selftest {passes}/{total}")
    return 0 if passes == total else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="冷热分层与合并候选（只读侧）")
    ap.add_argument("--pairs", action="store_true", help="只出 references/924-合并候选清单.md")
    ap.add_argument("--show", action="store_true", help="只打印统计，不写 state")
    ap.add_argument("--selftest", action="store_true", help="自检（引号链 + 读≠用两档），不写盘")
    ap.add_argument("--read", nargs="+", metavar="FRAG", help="记一笔「读过」（写 state/reads.json）")
    ap.add_argument("--use", nargs="+", metavar="FRAG",
                    help="记一笔「用到」（写 index 的 seen / last_seen）")
    ap.add_argument("--reads", action="store_true", help="只打印「读过 / 用过」两档对照，不写盘")
    ap.add_argument(
        "--tick",
        action="store_true",
        help="结算一次冷热状态（这是默认动作，留着只是为了跟文档/流程图里的写法对齐）",
    )
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    if args.read:
        print(f"读过 {note_read(args.read)} 个片段 → {READS}")
        return 0

    if args.use:
        print(f"用到 {note_use(args.use)} 个片段 → {INDEX}")
        return 0

    if args.reads:
        show(tick())
        return 0

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
