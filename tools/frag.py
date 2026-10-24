#!/usr/bin/env python3
"""frag.py —— 记忆模块切片：一个片段一个文件，靠 INDEX 列表读取。

用法：
  python tools/frag.py build            重建 fragments/ 与 fragments/INDEX.md
  python tools/frag.py list [--module 02]
  python tools/frag.py search 关键词 [关键词...] [--k 8]
  python tools/frag.py pack --query 关键词 [--main 3] [--random 2] [--chars 4000]
  python tools/frag.py pack --query "" --main 3 --random 2   # 无查询，只抽主片段
  python tools/frag.py lint             检查碎片过大 / 空片段

设计约定：
  - 一个片段 = 一个文件，放在 fragments/<模块>/<id>_<序号>_<短标题>.md
  - 主片段（weight=main）：always 模块、语气、学习习惯、项目主线
  - 次片段（weight=second）：其余；sensitive 模块默认不进 pack
  - 读取协议：先读 INDEX.md 列表 → 主片段精读 → 非主片段随机抽读
"""

from __future__ import annotations

import argparse
import hashlib
import os
import json
import math
import random
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synonyms  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "references"
FRAG = ROOT / "fragments"

MAX_LINES = int(os.environ.get("FRAG_MAX_LINES", "4"))   # 单片段上限，可用环境变量覆盖做 A/B
# 「一个整体」的模块：身份（00）必须整片到达。人设被切成 3+2 时，检索只捞得回半个人，
# 而 00 一共就十几行——放大上限比让下一个人手工凑到 4 条更划算（2026-10-24 shiori-old 实测）。
WHOLE_MODULES = {"00"}
WHOLE_MAX_LINES = int(os.environ.get("FRAG_WHOLE_MAX_LINES", "40"))

# 退休行（软删，2026-10-24 用户定的「删是软删」）：正文留在 md 里，但不进片段、不被检索命中。
# 依据 `references/90` §4「本库没有容量压力，删等于丢」。
RETIRED_RE = re.compile(r"（已退休 \d{4}-\d{2}-\d{2}：")
MIN_CHARS = 20          # 太短的段落并入上一片段
SENSITIVE_MODULES = {"10", "22", "23"}
MAIN_MODULES = {"89", "87", "88", "02", "02a", "07", "83", "86", "85", "90", "77", "97", "98", "99"}
SKIP_FILES = {"CATALOG.md", "变更日志.md"}
# 2026-10-24：`stats` 报的「模块数」改成文件口径，与 catalog.py / memory_check.py 相等（23）；
# 本文件只切其中能切的那部分（`变更日志` 按日志处理、不切片），所以另报一个「有片段」的数。
# 排除表与 catalog.py 的 EXCLUDE 逐项对齐，改一处就一起改。
MODULE_FILES_EXCLUDE = ("_模板.md", "CATALOG.md", "_复核清单.md", "_巡检记录.md", "_路由详表.md",
                        "_合并候选.md")
SLICE_UNDERSCORE = {"_待确认.md"}   # inbox 里的半句话也要搜得到，否则写进去等于没写（2026-10-20）

DATE_CACHE = FRAG / ".dates.json"   # 行级日期缓存；build 只删 *.md，这份 json 留着（2026-10-23）
RECENT_HALF_LIFE = 90.0             # 时间乘子的半衰期：90 天新鲜度掉一半
RECENT_BASE, RECENT_W = 0.7, 0.3    # final = score * (0.7 + 0.3 * recency)，混合式不是纯乘
DECAY_STATE = ROOT / "state" / "decay.json"   # 冷热状态的唯一事实源；build 从这里把 status/seen 带回来


def _sliceable(p) -> bool:
    """_ 开头的是运维文件，默认不切；唯独 inbox 例外。"""
    if p.name in SKIP_FILES:
        return False
    return (not p.name.startswith("_")) or (p.name in SLICE_UNDERSCORE)

CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
ASCII_RE = re.compile(r"[a-z0-9_][a-z0-9_\-\.]*")


def tokens_of(text: str) -> set[str]:
    """中文按字符二元组切，英文按词切；不加依赖，够检索用。"""
    low = text.lower()
    out = set(ASCII_RE.findall(low))
    for run in CJK_RE.findall(low):
        if len(run) <= 2:
            out.add(run)
            continue
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    return out


def parse_front(text: str) -> tuple[dict, list[str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, lines
    meta: dict[str, str] = {}
    kw_list: list[str] = []
    last_key = ""
    i = 1
    while i < len(lines) and lines[i].strip() != "---":
        raw = lines[i]
        m = re.match(r"^([A-Za-z_]+):\s*(.*)$", raw)
        if m:
            key, val = m.group(1), m.group(2).strip()
            last_key = key
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            meta[key] = val
        elif last_key == "keywords" and re.match(r"^\s*-\s+(.+)$", raw):
            kw_list.append(re.match(r"^\s*-\s+(.+)$", raw).group(1).strip())
        i += 1
    if kw_list:
        meta["keywords"] = " ".join(kw_list)
    return meta, lines[i + 1:]


def slug(text: str, limit: int = 10) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s，。、；：！？（）()\[\]【】·]', "", text)
    return (cleaned or "片段")[:limit]


def split_paragraphs(body: list[str], whole: bool = False) -> list[tuple[str, list[str]]]:
    """按空行切段，并把空行前后的小段合并；返回 [(标题, 行列表)]。

    whole=True 的模块（身份 00）用更大的单片上限，保证整篇落在一个片段里。
    """
    limit = WHOLE_MAX_LINES if whole else MAX_LINES
    blocks: list[tuple[str, list[str]]] = []
    heading = ""
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        keep = [x for x in buf if x.strip()]
        if keep:
            title = heading
            first = keep[0].strip()
            if first.startswith("#"):
                title = first.lstrip("#").strip()
            blocks.append((title, keep))
        buf = []

    for raw in body:
        line = raw.rstrip()
        if not line.strip():
            if not whole and len(buf) >= 4:
                flush()
            continue
        if line.startswith("#"):
            if not whole:
                flush()
                heading = line.lstrip("#").strip()
            buf.append(line)
            continue
        buf.append(line)
    flush()

    # 合并过短的片段，直到达到 MIN_CHARS 或遇到标题
    merged: list[tuple[str, list[str]]] = []
    for title, lines in blocks:
        text = "".join(lines)
        if merged and len(text) < MIN_CHARS and not lines[0].lstrip().startswith("#") \
                and len(merged[-1][1]) < limit // 2:
            merged[-1][1].extend(lines)
        else:
            merged.append((title, lines))

    # 过长的片段在中间切
    out: list[tuple[str, list[str]]] = []
    for title, lines in merged:
        if len(lines) <= limit:
            out.append((title, lines))
            continue
        chunk: list[str] = []
        part = 1
        for line in lines:
            chunk.append(line)
            if len(chunk) >= limit:
                out.append((f"{title}（{part}）", chunk))
                chunk = []
                part += 1
        if chunk:
            out.append((f"{title}（{part}）" if part > 1 else title, chunk))
    # 尾巴太短就并回上一片
    merged_tail: list[tuple[str, list[str]]] = []
    for title, lines in out:
        base = re.sub(r"（\d+）$", "", title)
        if merged_tail and len(lines) <= 1:
            prev_title, prev_lines = merged_tail[-1]
            if re.sub(r"（\d+）$", "", prev_title) == base and len(prev_lines) < limit + 3:
                prev_lines.extend(lines)
                continue
        merged_tail.append((title, lines))
    return merged_tail


def keywords_for(meta: dict, title: str, text: str) -> list[str]:
    kws = [k.strip() for k in meta.get("keywords", "").split() if k.strip() and not k.startswith("-")]
    if isinstance(meta.get("keywords"), str):
        pass
    extra = re.findall(r"`([^`]{2,20})`", text)[:6]
    words = [w for w in title.replace("：", " ").replace("，", " ").split() if 2 <= len(w) <= 12]
    seen: list[str] = []
    for k in kws + [title] + words + extra:
        k = k.strip()
        if k and k not in seen:
            seen.append(k)
    return seen[:10]


GIT_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _date_cache_load() -> dict:
    try:
        got = json.loads(DATE_CACHE.read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except Exception:
        return {}


def _date_cache_save(cache: dict) -> None:
    FRAG.mkdir(parents=True, exist_ok=True)
    DATE_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True),
                          encoding="utf-8")


def git_line_dates(rel: str, n_lines: int) -> list[str]:
    """按 `git log -p` 顺序扫，给每行标上它最后一次改动的日期（YYYY-MM-DD）。

    新提交先出现、先到先得，老提交只补空缺；单文件约 0.03 s，49 个模块一次性约 1.5 s。
    """
    dates = [""] * n_lines
    try:
        p = subprocess.run(
            ["git", "log", "--follow", "--date=short", "--format=@@@%cI", "-p", "--", rel],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    except Exception:
        return dates
    if p.returncode != 0:
        return dates
    cur, no, in_hunk = "", 0, False
    for raw in (p.stdout or "").splitlines():
        if raw.startswith("@@@"):
            cur = raw[3:].strip()[:10]
            in_hunk = False
            continue
        m = GIT_HUNK_RE.match(raw)
        if m:
            no, in_hunk = int(m.group(1)), True
            continue
        if not in_hunk:
            continue
        if raw.startswith(("diff ", "index ", "--- ", "+++ ")):
            in_hunk = False
            continue
        if raw.startswith("\\"):
            continue
        if raw[:1] in ("+", " "):
            if 1 <= no <= n_lines and not dates[no - 1] and cur:
                dates[no - 1] = cur
            no += 1
    return dates


def cached_line_dates(rel: str, body: list[str], cache: dict) -> list[str]:
    """源文件哈希没变就直接用缓存；变了才重扫 git 历史（增量靠文件粒度）。"""
    sig = hashlib.sha1("\n".join(body).encode("utf-8")).hexdigest()
    hit = cache.get(rel)
    if (isinstance(hit, dict) and hit.get("sig") == sig
            and isinstance(hit.get("dates"), list) and len(hit["dates"]) == len(body)):
        return hit["dates"]
    dates = git_line_dates(rel, len(body))
    cache[rel] = {"sig": sig, "dates": dates}
    return dates


def locate_spans(parts: list, body: list[str]) -> list[tuple[int | None, int | None]]:
    """把片段映射回源文件的行区间（0 基、含首尾）；游标顺延，防止重复行错位。"""
    norm = [x.strip() for x in body]
    spans: list[tuple[int | None, int | None]] = []
    cur = 0
    for _title, lines in parts:
        first = last = None
        for ln in lines:
            needle = ln.strip()
            j = cur
            while j < len(norm) and norm[j] != needle:
                j += 1
            if j >= len(norm):
                break
            if first is None:
                first = j
            last = j
            cur = j + 1
        spans.append((first, last))
    return spans


def frag_updated(span: tuple[int | None, int | None], dates: list[str], fallback: str) -> str:
    """片段级 updated：区间里最新那次改动的日期；对不上就退回模块级。"""
    first, last = span
    if first is None or last is None:
        return fallback
    got = [d for d in dates[first:last + 1] if d]
    return max(got) if got else fallback


PART_TAIL_RE = re.compile(r"^(?P<base>.*?)[\s]*[（(][\s]*(?P<seq>[0-9０-９]+)[\s]*[)）][\s]*$")
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def part_of(title: str) -> dict:
    """标题尾部「（n）」= 同一段被 MAX_LINES 切出来的第 n 片；没有就 base=原标题、seq=1。"""
    text = (title or "").strip()
    m = PART_TAIL_RE.match(text)
    if not m:
        return {"base": text, "seq": 1}
    seq = int(m.group("seq").translate(FULLWIDTH_DIGITS))
    return {"base": m.group("base").strip() or text, "seq": seq}


def carry_decay_state() -> dict[str, dict]:
    """build() 会整盘重建 index.json，status / seen / last_seen 必须原样带回来。

    优先级：`state/decay.json`（decay.py 写的，权威）> 旧 `index.json` > 默认 `active` / 0。
    这条错了冷热层等于白做——每 build 一次所有片段诈尸回 active（2026-10-23）。
    """
    carried: dict[str, dict] = {}
    old = FRAG / "index.json"
    if old.exists():
        try:
            for item in json.loads(old.read_text(encoding="utf-8")):
                carried[str(item.get("frag"))] = {
                    "seen": int(item.get("seen") or 0),
                    "status": str(item.get("status") or "active"),
                    "last_seen": item.get("last_seen") or None,
                }
        except (ValueError, TypeError):
            pass
    if DECAY_STATE.exists():
        try:
            state = json.loads(DECAY_STATE.read_text(encoding="utf-8"))
            for frag_id, info in (state.get("frags") or {}).items():
                if not isinstance(info, dict):
                    continue
                cur = carried.setdefault(frag_id, {"seen": 0, "status": "active", "last_seen": None})
                if info.get("status"):
                    cur["status"] = str(info["status"])
                if info.get("seen") is not None:
                    cur["seen"] = int(info["seen"])
                if info.get("lastSeen"):
                    cur["last_seen"] = str(info["lastSeen"])
        except (ValueError, TypeError):
            pass
    return carried


def build() -> int:
    if FRAG.exists():
        for old in FRAG.rglob("*.md"):
            old.unlink()
    index: list[dict] = []
    today = date.today().isoformat()
    date_cache = _date_cache_load()
    carried = carry_decay_state()

    files = sorted(p for p in REF.glob("*.md") if _sliceable(p))
    for path in files:
        meta, body = parse_front(path.read_text(encoding="utf-8"))
        mod_id = str(meta.get("id") or path.name.split("-")[0])
        mod_id = mod_id.strip('"')
        mod_title = meta.get("title") or path.stem
        weight = "main" if mod_id in MAIN_MODULES else "second"
        sensitive = (mod_id in SENSITIVE_MODULES) or meta.get("sensitive", "false").lower() == "true"
        distribute = meta.get("distribute", "keep")
        mod_kw = tokens_of(" ".join(
            k.strip() for k in meta.get("keywords", "").splitlines() if k.strip()))
        # 退休行（软删，2026-10-24）：正文留在 md 里可追溯，但不进片段、不被检索命中。
        # 换成空串而不是删行，`locate_spans` 还要靠原始下标对齐行号。
        body = ["" if RETIRED_RE.search(ln) else ln for ln in body]
        parts = split_paragraphs(body, whole=mod_id in WHOLE_MODULES)
        rel = path.relative_to(ROOT).as_posix()
        line_dates = cached_line_dates(rel, body, date_cache)
        spans = locate_spans(parts, body)
        mod_dir = FRAG / f"{mod_id}-{slug(mod_title, 8)}"
        mod_dir.mkdir(parents=True, exist_ok=True)
        entries: list[dict] = []
        for seq, (title, lines) in enumerate(parts, start=1):
            text = "\n".join(lines).strip()
            frag_id = f"{mod_id}.{seq:03d}"
            upd = frag_updated(spans[seq - 1], line_dates, meta.get("updated", today))
            name = f"{mod_id}_{seq:03d}_{slug(title or mod_title)}.md"
            head = [
                "---",
                f'frag: "{frag_id}"',
                f'module: "{mod_id}"',
                f'module_title: "{mod_title}"',
                f'title: "{title or mod_title}"',
                f'weight: "{weight}"',
                f'sensitive: "{str(sensitive).lower()}"',
                f'distribute: "{distribute}"',
                f'updated: "{upd}"',
                "keywords: " + json.dumps(keywords_for(meta, title or mod_title, text), ensure_ascii=False),
                "---",
                "",
            ]
            (mod_dir / name).write_text("\n".join(head) + text + "\n", encoding="utf-8")
            summary = re.sub(r"\s+", " ", text.lstrip("#-*> |").strip())[:70]
            ttl = title or mod_title
            kws = keywords_for(meta, ttl, text)
            entries.append({
                "frag": frag_id,
                "module": mod_id,
                "module_title": mod_title,
                "title": ttl,
                "weight": weight,
                "sensitive": sensitive,
                "updated": upd,
                "part": part_of(ttl),
                "seen": int((carried.get(frag_id) or {}).get("seen") or 0),
                "status": (carried.get(frag_id) or {}).get("status") or "active",
                "last_seen": (carried.get(frag_id) or {}).get("last_seen"),
                "file": str((mod_dir / name).relative_to(ROOT)).replace("\\", "/"),
                "lines": len(lines),
                "chars": len(text),
                "keywords": kws,
                "summary": summary,
                "refs": sorted({r for r in re.findall(r"`(\d\d[a-z]?)-", text) if r != mod_id}),
                "tokens": sorted(tokens_of(text + " " + " ".join(kws))),
                "title_tokens": sorted(tokens_of(ttl)),
                "module_tokens": sorted(tokens_of(mod_title)),
                "module_kw": sorted(mod_kw),
            })

        by_base: dict[str, list[dict]] = {}
        for entry in entries:
            by_base.setdefault(entry["part"]["base"], []).append(entry)
        for group in by_base.values():
            group.sort(key=lambda e: e["frag"])
            for no, entry in enumerate(group, 1):
                entry["part"]["seq"] = no
        index.extend(entries)

    _date_cache_save(date_cache)
    (FRAG / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    # 人读的列表：INDEX.md 只放标题（便宜，一眼扫完），INDEX-full.md 带摘要
    def render(with_summary: bool) -> str:
        md = ["# 片段总表（INDEX）", "",
              f"> 自动生成，共 {len(index)} 个片段。别整篇读模块，按这张表挑片段读：主片段精读，非主片段随机抽。",
              "> 重建：`python tools/frag.py build`；检索：`python tools/frag.py search 关键词`",
              "> 需要摘要看 `INDEX-full.md`（大四倍，只在挑不准时才翻）", ""]
        cur = None
        for item in index:
            if item["module"] != cur:
                cur = item["module"]
                md.append(f"\n## {cur} · {item['module_title']}")
            mark = "★" if item["weight"] == "main" else "·"
            if with_summary:
                md.append(f"- {mark} `{item['frag']}` {item['title']} — {item['summary']}")
            else:
                md.append(f"- {mark} `{item['frag']}` {item['title']}")
        return "\n".join(md) + "\n"

    (FRAG / "INDEX.md").write_text(render(False), encoding="utf-8")
    (FRAG / "INDEX-full.md").write_text(render(True), encoding="utf-8")

    print(f"切片完成：{len(files)} 个模块 → {len(index)} 个片段")
    print(f"总表：{FRAG / 'INDEX.md'}")
    return 0


# 2026-10-24：进程内缓存。recall_eval 一个进程里要排几十条查询，每次都重新
# 解析 index.json（650 片段）、重算 idf 和 avg —— 1571ms 里大半花在这上面，
# 跟检索算法本身没关系。索引按 mtime 失效（build 之后自动重读）。
_IDX_CACHE: dict = {"mtime": -1, "idx": []}
_STATS_CACHE: dict = {"key": -1, "idf": {}, "avg": 1.0}


def load_index() -> list[dict]:
    p = FRAG / "index.json"
    if not p.exists():
        # 2026-10-24（用户定的）：解压就能用，不许再让人先跑一条 build。
        # 第一次读到这里自动建一次，建不出来才报错。
        print("（第一次用，先建一次切片……）", file=sys.stderr)
        try:
            rc = build()
        except SystemExit as e:  # build 内部可能直接 sys.exit
            rc = e.code if isinstance(e.code, int) else 1
        except Exception as e:  # noqa: BLE001
            print(f"自动建切片失败：{type(e).__name__}: {e}", file=sys.stderr)
            rc = 1
        if rc != 0 or not p.exists():
            print("自动建切片没成功，手动跑一次看报错：python tools/frag.py build", file=sys.stderr)
            raise SystemExit(1)
    mtime = p.stat().st_mtime_ns
    if _IDX_CACHE["mtime"] == mtime and _IDX_CACHE["idx"]:
        return _IDX_CACHE["idx"]
    idx = json.loads(p.read_text(encoding="utf-8"))
    _IDX_CACHE.update(mtime=mtime, idx=idx)
    _STATS_CACHE.update(key=-1, idf={}, avg=1.0)
    return idx


def _ensure_stats(idx: list[dict]) -> None:
    key = len(idx)
    if _STATS_CACHE["key"] == key and _STATS_CACHE["idf"]:
        return
    df: dict[str, int] = {}
    for item in idx:
        for t in set(item.get("tokens", [])):
            df[t] = df.get(t, 0) + 1
    n = len(idx)
    idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
    lens = [len(i.get("tokens", [])) for i in idx]
    avg = (sum(lens) / len(lens)) if lens else 1.0
    _STATS_CACHE.update(key=key, idf=idf, avg=avg)


def idf_map(idx: list[dict]) -> dict[str, float]:
    _ensure_stats(idx)
    return _STATS_CACHE["idf"]


STOPWORDS = {
    "哪个", "在哪", "个目", "目录", "哪里", "什么", "怎么", "为什么", "多久", "多少",
    "说话", "没说", "才算", "话才", "可以", "是不是", "要不要", "一下",
    "现在", "这个", "那个", "我们", "你们", "他们", "以及", "然后", "还是", "就是",
    "因为", "所以", "但是", "这样", "那样", "如何", "有没有", "在吗",
}


def query_tokens(q: str) -> set[str]:
    return {t for t in tokens_of(q) if t not in STOPWORDS}


EXPAND_W = 0.45   # 同义扩展词的权重：只加召回，不许盖过用户原话


def expand_query(q: str) -> tuple[set[str], dict[str, float]]:
    """原话切词 + 同义扩展词（权重打折）。返回 (全部查询词, 每词权重)。"""
    base = query_tokens(q)
    extra = query_tokens(" ".join(synonyms.expand(q))) - base
    weights = {t: 1.0 for t in base}
    for t in extra:
        weights[t] = EXPAND_W
    return base | extra, weights


def avg_len(idx: list[dict]) -> float:
    _ensure_stats(idx)
    return _STATS_CACHE["avg"]


def recency_factor(item: dict) -> float:
    """2 ** (-days / 90)：今天 1.0，90 天前 0.5，180 天前 0.25；没有 updated 就当 1.0。"""
    upd = str(item.get("updated") or "")[:10]
    try:
        days = (date.today() - date.fromisoformat(upd)).days
    except Exception:
        return 1.0
    return 2 ** (-max(0, days) / RECENT_HALF_LIFE)


def score(item: dict, q: set[str], idf: dict[str, float], avgdl: float | None = None,
          wq: dict[str, float] | None = None) -> float:
    """BM25：命中按稀有度累加，长片段做长度惩罚，标题命中双倍。wq = 查询词权重。"""
    toks = item.get("tokens", [])
    if not toks:
        return 0.0
    tf: dict[str, int] = {}
    for t in toks:
        tf[t] = tf.get(t, 0) + 1
    ttl = set(item.get("title_tokens", []))
    modt = set(item.get("module_tokens", []))
    modkw = set(item.get("module_kw", []))
    dl = len(toks)
    avg = avgdl or dl or 1.0
    k1, b = 1.2, 0.75
    s = 0.0
    for t in q:
        qw = (wq or {}).get(t, 1.0)
        w = idf.get(t, 0.0) * qw
        c = tf.get(t, 0)
        if c:
            s += w * (c * (k1 + 1)) / (c + k1 * (1 - b + b * dl / avg))
        if t in ttl:
            s += w * 2.0
        if t in modt:
            s += w * 1.0   # 模块标题命中：用户是按模块记事的，标题就是主题
        if t in modkw:
            s += w * 0.8   # 模块级 keywords：整个模块的主题标签，比单片段标题弱一档
    # 时间混合式：只让新鲜度占 30%，老但最相关的片段不会被顶掉（precision 优先）
    return s * (RECENT_BASE + RECENT_W * recency_factor(item))


# ---------------------------------------------------------------- 人设分边
#
# 2026-10-22 用户报的：人设部分的命中率低，用户自己的和我自己的都低。实测确实是——
# 模块 `78`/`85`/`86`（我）与 `01`/`31`/`29`（用户）的 keywords 高度重叠，
# 「性格」「人格」「脾气」这些词两边都写了，纯 BM25 分不开，于是：
#   问「你是什么性格」→ 榜首是 `31`（用户的侧写）
#   问「我是个什么样的人」→ 榜首是 `78`（我自己的性格）
# 词表再补也治不了，因为两边的词是**同一批**。唯一可靠的信号是**人称**：
# 谁紧贴在主题词前面，问的就是谁。

SELF_MODULES = {"78", "85", "86"}     # 我：性格 / 账 / 立场
USER_MODULES = {"01", "31", "29"}     # 用户：背景 / 侧写 / 命名

SUBJECT_TOPICS = (
    "性格", "人格", "人设", "脾气", "侧写", "口味", "雷点", "原则", "立场",
    "毛病", "变化", "什么样", "什么人", "自我", "设定", "以前",
    "这个人", "那个人", "顺着", "讨好", "附和",
    "喜欢", "爱好", "兴趣", "在意", "讨厌", "烦",
)
SELF_PRON = ("你自己", "你", "助手", "妳")
USER_PRON = ("我自己", "我", "俺", "窝", "咱")

SUBJECT_BOOST = 1.35      # 同一侧：抬
SUBJECT_PENALTY = 0.72    # 反侧：压
SUBJECT_TOP = 1.40        # 分边时，同侧最高模块被顶到原榜首的这个倍数
SUBJECT_FLOOR = 0.15      # 同侧模块至少要够原榜首的这么多，才允许被顶上第一
PRON_WINDOW = 7           # 主题词前面看几个字（5 太窄：「你有没有独立人格」的「你」差一格）


def subject_of(query: str) -> str | None:
    """这句话在问谁：`self`（我）/ `user`（用户）/ `None`（跟人设无关）。

    判据是**人称离主题词的距离**，不是出现次数：`你怎么看我的性格` 里两个人都出现了，
    但「我」紧贴「性格」，所以问的是用户。
    """
    low = (query or "").lower()
    if not low:
        return None
    # 裸称谓（2026-10-24 新设备演练补，零新常数）：整句除了称谓没有别的实词时判到用户侧。
    # 「用户」「本人」「库主人」→ user；「用户登录接口怎么写」还有实词，不触发。
    # 为什么必须单独一档：这几个词在 90–99 的机制模块里出现得最密，光靠词面它们永远压过写人的模块。
    bare = {"用户", "本人", "库主人"}
    hit_bare = {b for b in bare if b in low}
    if hit_bare and not (query_tokens(low) - hit_bare):
        return "user"
    best: tuple[int, int] | None = None   # (距离, 优先级)：距离小者胜，同距离取更长的人称
    for topic in SUBJECT_TOPICS:
        start = 0
        while True:
            i = low.find(topic, start)
            if i < 0:
                break
            start = i + 1
            win = low[max(0, i - PRON_WINDOW):i]
            for group, side in ((SELF_PRON, "self"), (USER_PRON, "user")):
                for pron in group:
                    j = win.rfind(pron)
                    if j < 0:
                        continue
                    key = (len(win) - j - len(pron), -len(pron))
                    if best is None or key < best[0]:
                        best = (key, side)
    if best:
        return best[1]
    # 没有主题词时只认复合人称（「你自己」「我自己」「助手自己」）：
    # 光凭一个「你」字就判边会把「你写的那段代码报错了」也拉进人设，
    # 兜底必须比主题词那条路更保守。
    for group, side in ((SELF_PRON, "self"), (USER_PRON, "user")):
        for pron in group:
            if pron + "自己" in low:
                return side
    return None


def subject_bias(query: str, scores: dict[str, float]) -> dict[str, float]:
    """按人称给**模块**分加权：同侧抬、反侧压，并把同侧最强者顶到榜首。

    只作用在人设那 7 个模块上，别的模块一分不改——用户问技术问题时这条规则完全空转。
    为什么还要「顶榜」：`78/85/86` 与 `01/31/29` 的词表几乎重合，靠乘一个 1.35 的
    系数翻不过 `92`（记忆机制本身也写了「性格」「人格」这些词）。人称已经把问题
    答出来了，就不该再让词频决定榜首；但只有同侧真有关联（≥ 原榜首 15%）才顶，
    否则「分析一下我这个人」这种两边都弱的问法会被硬塞一个不相干的人设模块。
    """
    side = subject_of(query)
    if not side:
        return scores
    mine = SELF_MODULES if side == "self" else USER_MODULES
    other = USER_MODULES if side == "self" else SELF_MODULES
    out: dict[str, float] = {}
    for m, s in scores.items():
        if m in mine:
            out[m] = s * SUBJECT_BOOST
        elif m in other:
            out[m] = s * SUBJECT_PENALTY
        else:
            out[m] = s
    top_other = max((s for m, s in out.items() if m not in mine), default=0.0)
    best_mine = max((s for m, s in out.items() if m in mine), default=0.0)
    if best_mine > 0 and top_other > 0 and best_mine >= top_other * SUBJECT_FLOOR:
        target = top_other * SUBJECT_TOP
        scale = target / best_mine
        for m in mine:
            if out.get(m):
                out[m] *= scale
    return out


def module_scores(query: str) -> list[tuple[str, float]]:
    """片段层打分 → 模块分（每模块取最高片段），已套人称加权；榜首归一化为 1.0。"""
    idx = load_index()
    if not idx:
        return []
    idf = idf_map(idx)
    avg = avg_len(idx)
    q, wq = expand_query(query)
    best: dict[str, float] = {}
    for item in idx:
        s = score(item, q, idf, avg, wq)
        if s > 0 and s > best.get(item["module"], 0.0):
            best[item["module"]] = s
    if not best:
        return []
    best = subject_bias(query, best)
    top = max(best.values()) or 1.0
    return sorted(((m, s / top) for m, s in best.items()), key=lambda x: -x[1])


def cmd_list(args: argparse.Namespace) -> int:
    idx = load_index()
    for item in idx:
        if args.module and item["module"] != args.module:
            continue
        mark = "★" if item["weight"] == "main" else "·"
        print(f"{mark} {item['frag']:<8} {item['module_title'][:12]:<12} {item['title'][:28]:<28} {item['summary'][:40]}")
    return 0


def filename_fallback(q: set[str], limit: int = 8) -> list[tuple[str, list[str]]]:
    """片段层一条都没命中时，退回目录树：只看文件名（含模块目录名），
    用文件名里的词自己给自己加命中，别空手回去。"""
    if not q:
        return []
    scored: list[tuple[float, str, list[str]]] = []
    paths = list(FRAG.rglob("*.md")) + list(REF.glob("*.md"))
    for p in paths:
        rel = p.relative_to(ROOT).as_posix()
        hit = sorted(q & tokens_of(rel))
        if not hit:
            continue
        w = sum(1.0 if len(t) <= 2 else 1.8 for t in hit)   # 长词更有指向性
        scored.append((w, rel, hit))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [(rel, hit) for _, rel, hit in scored[:limit]]


def _merge_id_spec(items: list[dict]) -> str:
    """同源组 id 写法：连续 → `02a.011–02a.015`；不连续 → `02a.001, 011–015`。

    连续性按片段号（frag 尾号）判，不按 part.seq——part.seq 是同源组内序号，和 frag 号不是一回事。
    """
    def num(item: dict) -> int:
        return int(str(item["frag"]).split(".")[-1])

    ordered = sorted(items, key=num)
    mod = ordered[0]["module"]
    seqs = [num(i) for i in ordered]
    runs: list[list[int]] = []
    for s in seqs:
        if runs and s == runs[-1][-1] + 1:
            runs[-1].append(s)
        else:
            runs.append([s])
    if len(runs) == 1:
        run = runs[0]
        return f"{mod}.{run[0]:03d}" if len(run) == 1 else f"{mod}.{run[0]:03d}–{mod}.{run[-1]:03d}"
    parts = [f"{mod}.{runs[0][0]:03d}" if len(runs[0]) == 1
             else f"{mod}.{runs[0][0]:03d}–{mod}.{runs[0][-1]:03d}"]
    for run in runs[1:]:
        parts.append(f"{run[0]:03d}" if len(run) == 1 else f"{run[0]:03d}–{run[-1]:03d}")
    return ", ".join(parts)


def fold_hits(hits: list[tuple[float, dict]]) -> list[dict]:
    """search 输出层折叠：同 module + 同 part.base 的命中合成一行（区间摘要，不拼正文）。

    分数取组内最高，位置 = 组内最高分那片的位置；不打散原有顺序。
    """
    scores = {item["frag"]: s for s, item in hits}
    order = {item["frag"]: i for i, (_, item) in enumerate(hits)}
    groups: dict[tuple[str, str], list[dict]] = {}
    for _, item in hits:
        base = str((item.get("part") or {}).get("base") or item["title"])
        groups.setdefault((item["module"], base), []).append(item)

    rows: list[dict] = []
    for (mod, base), items in groups.items():
        if len(items) == 1:
            item = items[0]
            rows.append({"pos": order[item["frag"]], "score": scores[item["frag"]], "single": item})
            continue
        best = max(items, key=lambda i: (scores[i["frag"]], -order[i["frag"]]))
        rows.append({"pos": order[best["frag"]], "score": scores[best["frag"]],
                     "items": items, "base": base, "module": mod})
    rows.sort(key=lambda r: r["pos"])
    return rows


def cmd_search(args: argparse.Namespace) -> int:
    idx = load_index()
    idf = idf_map(idx)
    avg = avg_len(idx)
    q, wq = expand_query(" ".join(args.terms))
    hits = []
    for item in idx:
        s = score(item, q, idf, avg, wq)
        if s > 0:
            hits.append((s, item))
    hits.sort(key=lambda x: (-x[0], x[1]["frag"]))
    # 主体分档（2026-10-24 新设备演练补，零新常数）：判到某一侧时同侧片段先排。
    # 只在同侧最强 ≥ 原榜首 * SUBJECT_FLOOR 时启用，防止把不相干的模块硬塞到前面。
    # 背景：219 片里系统/维护类占 86%、写人的只占 4%，词面排序下「用户」永远先撞机制模块。
    side = subject_of(" ".join(args.terms))
    if side in ("user", "self") and hits:
        mine_mods = USER_MODULES if side == "user" else SELF_MODULES
        mine = [h for h in hits if h[1]["module"] in mine_mods]
        if mine and mine[0][0] >= hits[0][0] * SUBJECT_FLOOR:
            hits = mine + [h for h in hits if h[1]["module"] not in mine_mods]
    if getattr(args, "expand_parts", False):
        for s, item in hits[: args.k]:
            print(f"[{s:5.1f}] {item['frag']:<8} {item['weight']:<6} {item['title'][:24]:<24} {item['file']}")
    else:
        for row in fold_hits(hits)[: args.k]:
            if "single" in row:
                item = row["single"]
                print(f"[{row['score']:5.1f}] {item['frag']:<8} {item['weight']:<6} "
                      f"{item['title'][:24]:<24} {item['file']}")
                continue
            items = row["items"]
            chars = sum(int(i.get("chars") or 0) for i in items)
            print(f"[{row['score']:5.1f}] {_merge_id_spec(items):<8} {items[0]['weight']:<6} "
                  f"{row['base'][:24]:<24} 〔同源 {len(items)} 片 · 合计 {chars} 字〕")
    if not hits:
        print("片段层没命中，退回目录树（只看文件名，自己加命中）：")
        fb = filename_fallback(q)
        if fb:
            for rel, hit in fb:
                print(f"  {rel}  ← 命中 {' / '.join(hit)}")
        else:
            print("  文件名也没命中；python tools/frag.py list 看总表")
    return 0


MERGED_MAX_LINES = 12     # pack 层归并后的正文上限（行）：碎片合成一条，别再喂整段
MERGED_MAX_CHARS = 600    # pack 层归并后的正文上限（字），超了截断


def _frag_body(item: dict) -> str:
    return (ROOT / item["file"]).read_text(encoding="utf-8").split("---", 2)[-1].strip()


def merge_part_runs(group: list[dict]) -> list[dict]:
    """pack 层归并：同一 base 且 seq 相邻的片段合成一条，占组内最靠前那片的位置。

    只动投喂，不动检索：score / search / MAX_LINES / part 生成一概不碰。
    """
    if len(group) < 2:
        return group
    order = {item["frag"]: i for i, item in enumerate(group)}
    by_base: dict[str, list[dict]] = {}
    for item in group:
        part = item.get("part") or {}
        base = str(part.get("base") or "")
        if base:
            by_base.setdefault(base, []).append(item)

    runs: list[list[dict]] = []
    for items in by_base.values():
        items.sort(key=lambda i: int((i.get("part") or {}).get("seq") or 1))
        run: list[dict] = []
        last = None
        for item in items:
            seq = int((item.get("part") or {}).get("seq") or 1)
            if run and seq != last + 1:
                if len(run) > 1:
                    runs.append(run)
                run = []
            run.append(item)
            last = seq
        if len(run) > 1:
            runs.append(run)
    if not runs:
        return group

    consumed = {i["frag"] for run in runs for i in run}
    at = {min(order[i["frag"]] for i in run): run for run in runs}
    out: list[dict] = []
    for i, item in enumerate(group):
        run = at.get(i)
        if run:
            out.append({
                "frag": f"{run[0]['frag']}–{run[-1]['frag']}",
                "title": str((run[0].get("part") or {}).get("base") or run[0]["title"]),
                "module_title": run[0]["module_title"],
                "merged": len(run),
                "members": run,
            })
        elif item["frag"] not in consumed:
            out.append(item)
    return out


def clip_merged(body: str) -> str:
    """归并正文上限 12 行 / 600 字（含「…（截断）」标记），超了就截。"""
    mark = "…（截断）"
    lines = body.splitlines()
    cut = False
    if len(lines) > MERGED_MAX_LINES:
        lines = lines[:MERGED_MAX_LINES - 1]      # 给截断标记留一行
        cut = True
    text = "\n".join(lines).strip()
    room = MERGED_MAX_CHARS - len(mark) - 1
    if len(text) > room:
        text = text[:room].rstrip()
        cut = True
    return text + ("\n" + mark if cut else "")


def pack_doc(args: argparse.Namespace) -> dict:
    if getattr(args, "seed", None) is not None:
        random.seed(int(args.seed))      # 只固定抽样，不影响 score
    idx = load_index()
    idf = idf_map(idx)
    avg = avg_len(idx)
    q, wq = expand_query(args.query or "")
    # 查询词太泛（都是半数片段里都有的大词）时，按通用模式抽，别硬排
    avg_idf = (sum(idf.get(t, 0.0) * wq.get(t, 1.0) for t in q) / len(q)) if q else 0.0
    generic = (not q) or avg_idf < 1.5
    scored = []
    for item in idx:
        s = score(item, q, idf, avg, wq) if q else 0.0
        if not generic and item["weight"] == "main":
            s += 0.3          # 匹配分相同时，主片段优先
        scored.append((s, item))
    scored.sort(key=lambda x: (-x[0], x[1]["frag"]))

    # 主片段：只在 weight=main 里挑；通用模式下随机抽，避免每次都是同一批
    main_pool = [i for _, i in scored if i["weight"] == "main" and not i["sensitive"]]
    main, used_modules = [], set()
    if generic:
        random.shuffle(main_pool)
        for item in main_pool:
            if len(main) >= args.main:
                break
            if item["module"] in used_modules:
                continue
            main.append(item)
            used_modules.add(item["module"])
    else:
        for s, item in scored:
            if len(main) >= args.main:
                break
            if item["weight"] != "main" or item["sensitive"] or item["module"] in used_modules:
                continue
            if s <= 0:
                continue
            main.append(item)
            used_modules.add(item["module"])

    # 次片段：只在 weight=second 里随机抽（主片段不参与随机，避免每次都读同一批核心）
    rest = [i for _, i in scored
            if i["weight"] != "main" and i not in main and not i["sensitive"]
            and i["module"] not in used_modules]
    secondary = random.sample(rest, min(args.random, len(rest))) if rest else []

    # 引用跟随：主片段里提到别的模块（`02a-…`），就补一条那模块的片段，别让读到的片段断在半路
    followed: list[dict] = []
    if args.follow:
        need = {r for item in main for r in item.get("refs", [])}
        for ref in sorted(need):
            if len(followed) >= args.follow:
                break
            pool = [i for i in idx
                    if i["module"] == ref and not i["sensitive"] and i not in main]
            if not pool:
                continue
            pool.sort(key=lambda i: (i["weight"] != "main", i["frag"]))
            followed.append(pool[0])

    merge = bool(getattr(args, "merge_parts", True))
    mode = "无查询·核心抽取" if not q else ("通用抽取（查询词太泛，都是半数片段里的大词）" if generic else "按查询匹配")
    out = [f"# 记忆片段包（query={args.query or '无'} · {mode}）", "",
           f"> 主片段 {len(main)} 个（精读）+ 次片段 {len(secondary)} 个（随机）"
           f"{f' + 引用跟随 {len(followed)} 个' if followed else ''}"
           f"{'。同源片段（同一段被切开的兄弟片）已在投喂层归并' if merge else ''}。用 frag.py build 可重建。", ""]
    chars = 0
    entries = 0
    for label, group in (("主片段（精读）", main), ("次片段（随机）", secondary),
                         ("引用跟随（主片段里点名指到这儿）", followed)):
        if not group:
            continue
        if merge:
            group = merge_part_runs(group)
        out.append(f"## {label}")
        for item in group:
            if item.get("merged"):
                body = clip_merged("\n".join(_frag_body(m) for m in item["members"]))
                ids = f"{item['members'][0]['frag']}–{item['members'][-1]['frag']}"
                out.append(f"\n### [{ids}] {item['title']}（{item['module_title']}）〔合 {item['merged']} 片〕")
            else:
                body = _frag_body(item)
                out.append(f"\n### [{item['frag']}] {item['title']}（{item['module_title']}）")
            chars += len(body)
            entries += 1
            out.append(body)
    if q and not main:
        out.append("\n> 没匹配到主片段，只有随机抽的。换关键词再试。")
        fb = filename_fallback(q)
        if fb:
            out.append("> 目录树兜底（只看文件名，自己加命中）：")
            for rel, hit in fb:
                out.append(f"> - `{rel}` ← {' / '.join(hit)}")
    return {"text": "\n".join(out), "chars": chars, "entries": entries,
            "counts": (len(main), len(secondary), len(followed))}


def cmd_pack(args: argparse.Namespace) -> int:
    res = pack_doc(args)
    print(res["text"])
    a, b, c = res["counts"]
    print(f"\n---\n统计：{a}+{b}+{c} 片段，约 {res['chars'] // 2} tokens", file=sys.stderr)
    return 0


def cmd_auto(args: argparse.Namespace) -> int:
    """主动检索：把用户这一句话直接丢进来，当场吐出该读的片段（不用先想关键词）。

    判据：用户的原话里如果有稀有线索引（avg_idf ≥ 1.5）就按查询匹配挑主片段；
    全是「什么 / 现在 / 是不是」这类大词时，说明这句话本身没有可检索的线索，
    退回核心抽取并说明原因，免得硬排出一堆噪声。
    """
    text = " ".join(args.text).strip()
    q = query_tokens(text)
    idx = load_index()
    idf = idf_map(idx)
    avg_idf = (sum(idf.get(t, 0.0) for t in q) / len(q)) if q else 0.0
    if not q or avg_idf < 1.5:
        print("# 主动检索：这句话没有稀有线索，退回核心抽取", file=sys.stderr)
        ns = argparse.Namespace(query="", main=args.main, random=args.random, follow=args.follow, merge_parts=getattr(args, "merge_parts", True))
        return cmd_pack(ns)
    print(f"# 主动检索：{text}", file=sys.stderr)
    ns = argparse.Namespace(query=text, main=args.main, random=args.random, follow=args.follow, merge_parts=getattr(args, "merge_parts", True))
    return cmd_pack(ns)


def cmd_stats(_: argparse.Namespace) -> int:
    """片段层体检：规模、主/次、行数分布、敏感与引用数，给「细到这个粒度值不值」一个数。"""
    idx = load_index()
    by_mod: dict[str, int] = {}
    mains = sum(1 for i in idx if i["weight"] == "main")
    sens = sum(1 for i in idx if i["sensitive"])
    refs = sum(len(i.get("refs", [])) for i in idx)
    lines = sorted(i["lines"] for i in idx)
    chars = sum(i["chars"] for i in idx)
    for i in idx:
        by_mod[i["module"]] = by_mod.get(i["module"], 0) + 1
    top = sorted(by_mod.items(), key=lambda kv: -kv[1])[:5]
    mods = sum(1 for p in REF.glob("*.md") if p.name not in MODULE_FILES_EXCLUDE)
    print(f"片段 {len(idx)} 个 · 模块 {mods} 个（有片段 {len(by_mod)}）· 主 {mains} / 次 {len(idx) - mains}")
    print(f"正文 {chars:,} 字 · 平均 {chars // max(len(idx), 1)} 字/片段 · 行数中位 {lines[len(lines) // 2]}、最长 {lines[-1]}")
    print(f"敏感片段 {sens} 个（检索时排除）· 模块间引用 {refs} 条")
    print("片段最多的模块：" + "，".join(f"{m} {n}" for m, n in top))
    return 0


def cmd_lint(_: argparse.Namespace) -> int:
    idx = load_index()
    bad: list[str] = []
    warns: list[str] = []
    seen: set[str] = set()
    mods = {i["module"] for i in idx}
    for item in idx:
        path = ROOT / item["file"]
        if not path.exists():
            bad.append(f"文件丢了 {item['frag']} {item['file']}")
            continue
        body = path.read_text(encoding="utf-8").split("---", 2)[-1].strip()
        if item["frag"] in seen:
            bad.append(f"片段号重复 {item['frag']}")
        seen.add(item["frag"])
        if item["lines"] > MAX_LINES + 2:
            bad.append(f"过长 {item['frag']} {item['lines']} 行")
        if len(body) < 8:
            bad.append(f"过短 {item['frag']} {len(body)} 字")
        for ref in item.get("refs", []):
            if ref in mods:
                continue
            if ref.split(".")[0] == item["module"]:
                bad.append(f"引用指向不存在的片段 {item['frag']} → {ref}")
            else:
                # 跨模块引用断了不一定是库坏了：分发包会 drop 掉一批模块（08、83 之类），
                # 引用自然断，这是包的合法形态。不把 lint 弄红，单独报一行让人自己看。
                warns.append(f"引用指向不存在的模块 {item['frag']} → {ref}")
    # 索引和磁盘对不对得上（改过模块没 rebuild 会在这里露出来）
    on_disk = {p.name for p in FRAG.rglob("*.md")
               if p.name not in ("INDEX.md", "INDEX-full.md")}
    indexed = {Path(i["file"]).name for i in idx}
    for name in sorted(on_disk - indexed)[:5]:
        bad.append(f"磁盘多出来的片段（没进索引）{name}")
    for name in sorted(indexed - on_disk)[:5]:
        bad.append(f"索引里有但磁盘没有 {name}")
    print(f"片段 {len(idx)} 个；磁盘 {len(on_disk)} 个；问题 {len(bad)} 条")
    for b in bad[:20]:
        print(" -", b)
    if warns:
        print(f"（另有 {len(warns)} 条跨模块引用失效，多为分发包 drop 掉的模块，不算错）")
        for w in warns[:5]:
            print("   ~", w)
    return 0 if not bad else 1


def cmd_morning(args: argparse.Namespace) -> int:
    """开场包：人格三块（语气 / 立场 / 我自己的账）各一 + 全库随机几条。"""
    idx = load_index()
    out, used = [], set()
    for mod in ("89", "86", "85"):
        cands = [i for i in idx
                 if i["module"] == mod and i["weight"] == "main" and not i["sensitive"]]
        if not cands:
            continue
        item = random.choice(cands)
        out.append(item)
        used.add(item["frag"])
    rest = [i for i in idx
            if i["frag"] not in used and not i["sensitive"] and i["weight"] == "second"]
    random.shuffle(rest)
    out += rest[: args.random]

    print("# 开场记忆包", file=sys.stderr)
    chars = 0
    for item in out:
        text = (ROOT / item["file"]).read_text(encoding="utf-8").split("---", 2)[-1].strip()
        chars += len(text)
        print(f"\n### [{item['frag']}] {item['title']}（{item['module_title']}）\n{text}")
    print(f"\n---\n统计：{len(out)} 个片段，约 {chars // 3} tokens", file=sys.stderr)
    return 0


def cmd_core(_: argparse.Namespace) -> int:
    """硬塞层：把 86 第七节「硬塞层清单」的条目整段打出来，直接贴进前言。"""
    path = ROOT / "references" / "86-独立人格与立场.md"
    text = path.read_text(encoding="utf-8")
    if "## 七、硬塞层清单" not in text:
        print("没有找到「硬塞层清单」小节，检查 references/86-独立人格与立场.md", file=sys.stderr)
        return 1
    block = text.split("## 七、硬塞层清单", 1)[1]
    lines = [ln.strip() for ln in block.splitlines()]
    items = [ln for ln in lines if re.match(r"^\d+\.\s+\S", ln)]
    body = "\n".join(f"{i}. {re.sub(r'^\\d+\\.\\s*', '', ln)}" for i, ln in enumerate(items, 1))
    body = "\n".join(re.sub(r"^\d+\.\s*", "", ln) for ln in items)
    print(body)
    print(f"\n---\n统计：{len(items)} 条常驻规则，约 {len(body) // 3} tokens", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="记忆片段工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build")

    p = sub.add_parser("list")
    p.add_argument("--module")

    p = sub.add_parser("search")
    p.add_argument("terms", nargs="+")
    p.add_argument("--k", type=int, default=8,
                   help="折叠后保留几组（--expand-parts 时按原始片段条数）")
    p.add_argument("--expand-parts", dest="expand_parts", action="store_true",
                   help="展开为旧行为：逐条片段输出，不折叠同源切片")

    p = sub.add_parser("pack")
    p.add_argument("--query", default="")
    p.add_argument("--main", type=int, default=3)
    p.add_argument("--random", type=int, default=2)
    p.add_argument("--follow", type=int, default=1, help="主片段里引用了别的模块时，最多跟几条过去")
    p.add_argument("--merge-parts", dest="merge_parts", action="store_true", default=True,
                   help="投喂层把同源切片（同 base、seq 相邻）归并成一条（默认开）")
    p.add_argument("--no-merge-parts", dest="merge_parts", action="store_false",
                   help="回退旧行为：一条片段一条，不归并")
    p.add_argument("--seed", type=int, default=None, help="固定随机抽样种子，方便 A/B 与复现")

    p = sub.add_parser("auto")
    p.add_argument("text", nargs="+", help="用户的原话，原样丢进来")
    p.add_argument("--main", type=int, default=3)
    p.add_argument("--random", type=int, default=1)
    p.add_argument("--follow", type=int, default=0)
    p.add_argument("--merge-parts", dest="merge_parts", action="store_true", default=True)
    p.add_argument("--no-merge-parts", dest="merge_parts", action="store_false")
    p.add_argument("--seed", type=int, default=None)

    sub.add_parser("lint")

    sub.add_parser("stats")

    sub.add_parser("core")

    p = sub.add_parser("morning")
    p.add_argument("--random", type=int, default=2)

    args = ap.parse_args()
    return {"build": lambda a: build(), "list": cmd_list, "search": cmd_search,
            "pack": cmd_pack, "auto": cmd_auto, "lint": cmd_lint, "stats": cmd_stats,
            "core": cmd_core, "morning": cmd_morning}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

