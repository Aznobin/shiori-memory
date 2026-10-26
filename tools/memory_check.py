#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记忆库体检（只读，不修改任何文件）。Python 版。

用法：
  python tools/memory_check.py
  python tools/memory_check.py --root <技能根> --stale-days 30 --checkup-days 7
  python tools/memory_check.py --selftest          自检 updated 引号链 + 坏日期检查（临时目录，只读真库）

等价于 tools/memory-check.ps1（PowerShell 版保留给只装了 PS 的机器）。
退出码：0 无硬错误；1 有硬错误。
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import cfg  # noqa: E402  路径统一从 memory.config.json 取（见 tools/config.py）

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = ROOT

# 内容模块在 `memory/`，骨架与协议件在 `references/`；`--root` 指向哪个库就扫哪个库。
MODULE_EXCLUDE = ("926.md", "927.md", "925.md", "923.md", "928.md", "924.md")

# 疑似编号迁移改坏日期（2026-10-27 批 M）：两位→三位替换那次把 `2026-10-26` 里的
# `10`/`24` 当成模块号，写成了「年-三位模块号-日」这种坏串。这两种形状在正文里没有任何正当用途。
# 本文件正文里不写坏串原文，否则这条检查会命中自己（下面注释里用 `2026-<三位>-26` 占位）。
BAD_DATE_RE = re.compile(r"20\d{2}-\d{3}-\d{2}|20\d{2}-\d{2}-\d{3}")


def bad_date_hits(path: Path) -> list[int]:
    """返回文件里命中坏日期形状的行号（1 起）；读不到就当没有。"""
    try:
        text = read(path)
    except OSError:
        return []
    return [i for i, line in enumerate(text.split("\n"), 1) if BAD_DATE_RE.search(line)]


def _module_dirs(root: Path) -> list[Path]:
    """模块可能待的两个目录：`memory/` 优先，`references/` 兼容一层（阶段3a 双根读取）。"""
    return [root / "memory", root / "references"]


def _module_map(root: Path) -> dict[str, Path]:
    """`{文件名: 路径}`，扫两个根；同名时 `memory/` 优先。"""
    out: dict[str, Path] = {}
    for base in _module_dirs(root):
        if not base.is_dir():
            continue
        for p in sorted(base.glob("*.md")):
            out.setdefault(p.name, p)
    return out

# 结构性长文件：超 120 行红线但不拆（2026-10-22 定的，理由记在 83 §6「红线豁免」）。
# 照旧报出来、标「已豁免」——不静默，避免以后有人以为脚本漏查。
LINE_EXEMPT: dict[str, str] = {
    "905-AGENTS骨架.md": "整份照抄给新机器，拆开就拼不起来",
    "918-架构大纲.md": "改动前必读的唯一入口，要一次读全",
    "921.md": "台账价值在连续，只往后归档旧年份",
    "813-语气与交流（每次必读）.md": "每次对话都读的语气模块，拆开只会多一次读取",
    "809-独立人格与立场.md": "常驻行为规则已整段注入 AGENTS.md §8，正文要一次读全",
    "803-用户自己的性格.md": "人格自述看的是变化轨迹，拆开读不出前后差别",
}


def line_count(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    text = text.replace("\r\n", "\n").rstrip("\n")
    return 0 if not text else len(text.split("\n"))


def dates_in_tables(text: str) -> list[str]:
    # 允许日期后面跟时间（`923` 写成「2026-10-20 03:55」），漏了这段会永远判「没有任何体检日期」（2026-10-20 修）
    return re.findall(r"(?m)^\|\s*(\d{4}-\d{2}-\d{2})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\s*\|", text)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def frontmatter(text: str) -> str | None:
    m = re.match(r"(?s)^\ufeff?---\s*\r?\n(.*?)\r?\n---", text)
    return m.group(1) if m else None


def parse_updated(text: str) -> str:
    """从 frontmatter 里取 updated 日期。

    2026-10-26：引号链修好前 `tools/decay.py` 生成的 `924.md` 写成
    `updated: "2026-10-24"`（带引号），下面原来只认裸日期的正则会把这种正常
    写法判成「没有可解析的 updated 日期」并吐假 ❌。裸日期和带引号/单引号
    两种写法都收，返回 `YYYY-MM-DD`；取不到返回空串。
    """
    m = re.search(
        r"(?m)^updated:\s*[\"']?(\d{4}-\d{2}-\d{2}(?:[ T][\d:]+)?)[\"']?\s*$",
        frontmatter(text) or "",
    )
    return m.group(1) if m else ""


def _selftest() -> int:
    """只验两件事：带引号的 updated 不再误报；真缺字段仍然报。

    用临时目录跑真 `main()`，临时目录里没有 `923.md`（体检日期那条会走
    「文件不存在」分支，只给建议不给硬错），所以硬错误数可预期。
    """
    import contextlib
    import io
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

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        refs = root / "references"
        refs.mkdir(parents=True, exist_ok=True)
        (root / "SKILL.md").write_text("# 临时\n", encoding="utf-8", newline="")
        (refs / "928.md").write_text(
            "| 大类 | 模块 |\n| --- | --- |\n| 测 | `003-带引号.md` |\n| 测 | `32-缺字段.md` |\n",
            encoding="utf-8",
            newline="",
        )
        (refs / "003-带引号.md").write_text(
            "---\nid: \"31\"\ntitle: \"带引号\"\nkeywords:\n  - 自测\n"
            "sensitive: \"false\"\nalways: \"false\"\nupdated: \"2026-10-24\"\n"
            "distribute: keep\n---\n\n正文\n",
            encoding="utf-8",
            newline="",
        )
        (refs / "32-缺字段.md").write_text(
            "---\ntitle: \"缺字段\"\nkeywords:\n  - 自测\n"
            "sensitive: \"false\"\nalways: \"false\"\nupdated: 2026-10-24\n"
            "distribute: keep\n---\n\n正文\n",
            encoding="utf-8",
            newline="",
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--root", str(root)])
        out = buf.getvalue()
        check(
            "带引号的 updated 不再误报（A3）",
            "003-带引号.md 没有可解析的 updated 日期" not in out,
            out.strip().splitlines()[-3:],
        )
        check("裸日期照旧能解析", "32-缺字段.md 没有可解析的 updated 日期" not in out)
        check("真缺字段仍然报硬错", "32-缺字段.md frontmatter 缺 id" in out and rc == 1)
        check("带引号那篇不产生硬错", "003-带引号.md frontmatter 缺" not in out)

        check("解析函数直接认两种写法", parse_updated("---\nupdated: 2026-01-02\n---\n") == "2026-01-02"
              and parse_updated("---\nupdated: \"2026-01-02\"\n---\n") == "2026-01-02"
              and parse_updated("---\nupdated: '2026-01-02'\n---\n") == "2026-01-02"
              and parse_updated("---\ntitle: 没有日期\n---\n") == "")

        # 坏日期检查（2026-10-27 批 M）：喂坏串必须报硬错并带行号，喂正常日期不许报。
        # 坏串在这里拼出来，别写成字面量——否则这条检查会命中本文件自己。
        bad_sample = "2026-" + "701" + "-26"
        target = refs / "003-带引号.md"
        clean_text = target.read_text(encoding="utf-8")
        target.write_text(clean_text.rstrip("\n") + "\n- 坏日期示例：" + bad_sample + "\n", encoding="utf-8", newline="")
        check("坏日期串被识别（年-三位-日）", len(bad_date_hits(target)) == 1, bad_date_hits(target))
        check(
            "好日期串不被误报",
            not BAD_DATE_RE.search("2026-10-26") and not BAD_DATE_RE.search("updated: 2026-01-02"),
        )
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            rc2 = main(["--root", str(root)])
        out2 = buf2.getvalue()
        check(
            "坏日期进硬错且带文件名+行号",
            "疑似编号迁移改坏日期" in out2 and "003-带引号.md:" in out2 and rc2 == 1,
            out2.strip().splitlines()[-2:],
        )
        target.write_text(clean_text, encoding="utf-8", newline="")
        buf3 = io.StringIO()
        with contextlib.redirect_stdout(buf3):
            rc3 = main(["--root", str(root)])
        check(
            "清掉坏串后不再报坏日期",
            "疑似编号迁移改坏日期" not in buf3.getvalue() and rc3 == rc,
        )

    print(f"selftest {passes}/{total}")
    return 0 if passes == total else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--stale-days", type=int, default=30)
    ap.add_argument("--checkup-days", type=int, default=7)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()

    root = Path(args.root).expanduser()
    refs = root / "references"
    skill = root / "SKILL.md"
    issues: list[str] = []
    warns: list[str] = []

    if not skill.exists():
        print(f"❌ 找不到 {skill}")
        return 1

    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    skill_text = read(skill)

    print(f"=== 记忆库体检 {today.strftime('%Y-%m-%d %H:%M')} ===")
    print(f"根目录: {root}")
    print(f"宿主: Python {sys.version.split()[0]}")

    # 1) 路由表 <-> 文件
    #    2026-10-20 起小类详表拆到 references/928.md；两张都读，缺一张就只看另一张
    route_text = skill_text
    route_src = refs / "928.md"
    if route_src.exists():
        route_text += "\n" + read(route_src)
    route_files: list[str] = []
    for row in re.findall(r"(?m)^\|\s*([^|]*?)\s*\|", route_text):
        for name in re.findall(r"`([^`]+)`", row):
            # `929.md` 是 `_` 开头的结构件、已在 `928.md` 登记，名字不匹配 \d{2}[a-z]?- 规则，单独白名单
            if re.match(r"^\d{2,3}[a-z]?-", name) or name in ("922", "921", "925", "923", "929"):
                route_files.append(name + ".md")

    mods = _module_map(root)
    module_files = [
        name
        for name in sorted(mods)
        # 2026-10-24：924.md 是 decay.py --pairs 的生成物（distribute: drop、auto 日期），
        # 不该当人写模块查路由与 updated，否则每次 --pairs 都多两条假 ❌。
        if name not in MODULE_EXCLUDE
    ]

    missing_file = [f for f in route_files if f not in module_files]
    missing_route = [f for f in module_files if f not in route_files]
    if missing_file:
        issues.append("路由表登记但文件不存在: " + ", ".join(missing_file))
    if missing_route:
        issues.append("文件未登记进路由表: " + ", ".join(missing_route))

    # 2) frontmatter 必备字段（2026-10-20 补 always / distribute：漏了 distribute 会静默走默认 keep 被发出去）
    required = ("id", "title", "keywords", "sensitive", "always", "updated", "distribute")
    for name in module_files:
        text = read(mods[name])
        fm = frontmatter(text)
        if fm is None:
            issues.append(f"{name} 没有 frontmatter 区块")
            continue
        for field in required:
            if not re.search(rf"(?m)^{field}\s*:", fm):
                issues.append(f"{name} frontmatter 缺 {field}")

    # 3) 行数
    #    2026-10-22：三份「结构性长文件」超红线不拆，理由写在 83 §6 的红线豁免条里。
    #    它们照旧列在提醒里（不静默），但标「已豁免」，不再劝人去拆。
    skill_lines = line_count(skill)
    if skill_lines > 150:
        warns.append(f"SKILL.md {skill_lines} 行（上限 150）：把内容下放到模块")
    for name in module_files:
        n = line_count(mods[name])
        if n <= 120:
            continue
        if name in LINE_EXEMPT:
            warns.append(f"{name} {n} 行（结构性长文件，已豁免）：{LINE_EXEMPT[name]}，见 83 §6")
        else:
            warns.append(f"{name} {n} 行（建议 ≤120）：按 83 §6 拆分")

    # 4) updated 新鲜度（2026-10-26：引号链修好——裸日期和 `"2026-10-24"` 都收，见 parse_updated）
    for name in module_files:
        stamp = parse_updated(read(mods[name]))
        if not stamp:
            issues.append(f"{name} 没有可解析的 updated 日期")
            continue
        try:
            d = datetime.strptime(stamp[:10], "%Y-%m-%d")
        except ValueError:
            issues.append(f"{name} updated 日期格式不对: {stamp}")
            continue
        age = (today - d).days
        if age > args.stale_days:
            warns.append(f"{name} updated 距今 {age} 天：话题涉及现状时先复核")

    # 5) inbox
    inbox = read(refs / "922.md")
    pending = len(re.findall(r"(?m)^\|\s*\d{4}-\d{2}-\d{2}\s*\|", inbox))
    if pending:
        warns.append(f"inbox 有 {pending} 条待确认：收尾时清一遍")

    # 6) 重复问题台账
    ledger = read(refs / "901-重复问题台账.md")
    watching = ledger.count("观察中")
    if watching:
        warns.append(f"台账还有 {watching} 行「观察中」")
    for m in re.finditer(r"(?m)^\|([^|\n]+)\|\s*(\d+)\s*\|([^\n]*)$", ledger):
        n = int(m.group(2))
        row = m.group(0)
        if n >= 2 and not re.search(r"已升级|已解决|已排除|不升级|已修", row):
            warns.append("台账 2 次以上却没升级: " + m.group(1).strip())

    # 7) 变更日志
    log_text = read(refs / "921.md")
    log_dates = dates_in_tables(log_text)
    if log_dates:
        print(f"变更日志: {len(log_dates)} 行，最新 {max(log_dates)}")

    # 8) 节律
    check_text = read(refs / "923.md")
    check_dates = sorted(dates_in_tables(check_text), reverse=True)
    if not check_dates:
        warns.append("_巡检记录 里没有任何体检日期：跑完这轮补一行")
    else:
        try:
            last = datetime.strptime(check_dates[0], "%Y-%m-%d")
            gap = (today - last).days
            if gap > args.checkup_days:
                warns.append(f"上次体检距今 {gap} 天（节律 {args.checkup_days} 天）：该跑一轮了")
        except ValueError:
            pass

    # 9) 日志纪律
    touched_today = [
        p for p in mods.values()
        if datetime.fromtimestamp(p.stat().st_mtime).date() == today.date()
    ]
    if touched_today and today_str not in log_dates:
        warns.append(f"今天改过 {len(touched_today)} 个文件，但变更日志没有 {today_str} 的行")

    # 10) 记录膨胀
    if len(check_dates) > 5:
        warns.append(f"_巡检记录 有 {len(check_dates)} 行：只留最近 5 次，把旧行删掉")

    # 11) 开场那一步的三处分身必须一致（2026-10-21 她点出来的）
    #     她要求（原话已移除）——
    #     同一条规矩存在三处：本库 templates/AGENTS.md（setup.py 生成用的正源）、
    #     references/91a 骨架（发给别人的文档）、工作区 AGENTS.md（每轮真注入的那份）。
    #     只改一处 = 另外两处悄悄过期，而且过期那份正好是真正生效的。
    #     这里不比对全文（各自的个性化内容本来就该不同），只查两件硬事实：
    #     ① 三处提到 `memgit.py check`；② 工作区那份里 check 排在第 1 条。
    tpl = root / "templates" / "AGENTS.md"
    skeleton = refs / "905-AGENTS骨架.md"
    ws = (cfg.get("paths") or {}).get("workspace") or ""
    workspace_md = Path(ws) / "AGENTS.md" if ws else None
    for label, path in (
        ("templates/AGENTS.md", tpl),
        ("references/905-AGENTS骨架.md", skeleton),
    ):
        if path.exists() and "memgit.py check" not in read(path):
            # 2026-10-24：打包版里 91a 是空槽位（distribute: blank），正文被清空、
            # 只剩标题和提示，这条检查在包内必然误报；只有主库那版（够长）才查。
            if label.startswith("references/") and len(read(path).splitlines()) < 20:
                continue
            warns.append(f"{label} 没有开局对 git 最新这一步：三处口径不同步，setup 生成出来的会缺")
    if workspace_md and workspace_md.exists():
        ws_text = read(workspace_md)
        if "memgit.py check" not in ws_text:
            warns.append("工作区 AGENTS.md 没有 memgit.py check：真正生效的那份过期了")
        else:
            m = re.search(r"(?ms)^## 1\. .*?(?=^## )", ws_text)
            head = m.group(0) if m else ""
            first = re.search(r"(?m)^1\..*$", head)
            if first and "memgit.py check" not in first.group(0):
                warns.append("工作区 AGENTS.md 的 §1 第 1 条不是 memgit.py check：排序被改回去了")
        # 12) 托管块：硬塞层必须由 agents_sync 生成，不能是手抄的散条（2026-10-21 多设备对齐）
        if "<!-- CORE:BEGIN -->" not in ws_text or "<!-- CORE:END -->" not in ws_text:
            warns.append("工作区 AGENTS.md 没有 CORE 托管块：§8 还是手抄的，跑 python tools/agents_sync.py")
        elif "<!-- ROUTES:BEGIN -->" not in ws_text:
            warns.append("工作区 AGENTS.md 没有 ROUTES 托管块：跑 python tools/agents_sync.py")

    # 13) 疑似编号迁移改坏日期（2026-10-27 批 M 加，硬错）
    #     两位→三位编号替换曾把 `2026-10-26` 里的 `10`/`24` 当模块号，写成 `2026-<三位>-26` 这类坏串；
    #     它在正文里没有正当用途，命中就报硬错并把行号列出来。
    scan_targets: list[Path] = [skill, *mods.values()]
    for pattern in ("templates/**/*.md", "tools/**/*.py", "tools/**/*.mjs", "tools/**/*.ps1"):
        scan_targets.extend(sorted(root.glob(pattern)))
    bad_dates = [
        f"{path.relative_to(root)}:{line_no}"
        for path in dict.fromkeys(scan_targets)
        for line_no in bad_date_hits(path)
    ]
    if bad_dates:
        issues.append("疑似编号迁移改坏日期（YYYY-NNN-DD / YYYY-DD-NNN）: " + ", ".join(bad_dates))

    print()
    print(f"模块 {len(module_files)} 个 · SKILL.md {skill_lines} 行")
    if not issues and not warns:
        print("✅ 全部通过：登记一致、frontmatter 齐、行数达标、无超期、无待办")
        return 0
    if issues:
        print(f"❌ 必须修 ({len(issues)})")
        for item in issues:
            print("  - " + item)
    if warns:
        print(f"⚠️ 建议看 ({len(warns)})")
        for item in warns:
            print("  - " + item)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
