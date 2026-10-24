#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包成可发给别人的版本：个人信息清零、路径换成占位符、机器状态剔除（Python 版，跨平台）。

用法：
  python tools/pack.py                 # 产出 dist/user-memory-<版本>-<日期>.zip
  python tools/pack.py --no-zip        # 只生成目录（抽检用），不压包
python tools/pack.py --keep-dir      # 压包的同时保留解压目录
  python tools/pack.py --out /tmp/m.zip
  python tools/pack.py --root <技能根>
  python tools/pack.py --persona       # 人格包：额外带上 tools/persona/ 里的可分发人格

判定依据是每个模块 frontmatter 的 distribute:  keep | blank | reset | drop
等价于 tools/pack.ps1（PowerShell 版保留给只装了 PS 的机器）。

人格包与记忆包是两条线：记忆包讲「怎么记住用户」，人格包讲「我说话的样子」。
人格模板的正源在 tools/persona/（本机不出现在 references 里，只从这里分发）。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# 2026-10-24 她的口径：「别人的骨架应该是全空的」。
# 所以 references/*.md 的默认值反过来：只有系统区（90–99）与协议件默认 keep，
# 其余（01–89、08a、12a…）**默认 drop**，要进包必须自己在 frontmatter 写 distribute。
DEFAULT_KEEP_RE = re.compile(r"^(9[0-9]|CATALOG|_模板|_待确认|变更日志)")


def default_mode(stem: str) -> str:
    return "keep" if DEFAULT_KEEP_RE.match(stem) else "drop"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def front_field(text: str, name: str, fallback: str = "") -> str:
    m = re.search(rf"(?m)^{re.escape(name)}:\s*(\S+)\s*$", text)
    # 2026-10-24：frontmatter 里写成 distribute: "drop" 时旧实现把引号一起带出来，
    # 导致 `mode == "drop"` 永远不成立、隐私模块被当 keep 打进包里。这里剥掉引号。
    return m.group(1).strip("\"'") if m else fallback


def split_doc(text: str) -> tuple[str, str]:
    m = re.match(r"(?s)^(---\r?\n.*?\r?\n---\r?\n)(.*)$", text)
    if m:
        return m.group(1), m.group(2)
    return "", text


def get_head(body: str) -> tuple[str, str]:
    lines = body.replace("\r\n", "\n").split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), -1)
    if start < 0:
        return "", body
    end = start
    for j in range(start + 1, len(lines)):
        if re.match(r"^\s*>\s?", lines[j]):
            end = j
            continue
        if lines[j].strip() == "" and end == j - 1:
            end = j
            continue
        break
    return "\n".join(lines[: end + 1]), "\n".join(lines[end + 1 :])


def to_blank(text: str, hint: str = "") -> str:
    fm, body = split_doc(text)
    # 2026-10-24：只有 frontmatter 是「空槽」还漏——她的兴趣、学科、项目名
    # 全写在 keywords 里，blank 之后照样把整串关键词发出去。keywords 一律换成占位。
    # 占位优先用模块自己的通用名（`07-项目.md` → 项目），这样空槽还能被「项目」这类词命中；
    # 拿不到才退回「待填」——「待填」两个字谁也不会在笔记里写，等于这个词废了。
    key = hint or "待填"
    lines = fm.replace("\r\n", "\n").split("\n")
    out, skipping = [], False
    for ln in lines:
        if re.match(r"^keywords\s*:", ln):
            out.append("keywords:")
            out.append(f"  - {key}")
            skipping = True
            continue
        if skipping:
            if ln.startswith(("  ", "\t", "- ")):
                continue
            skipping = False
        out.append(ln)
    fm = "\n".join(out)
    # 2026-10-24：blank 之后 `updated:` 还是源库那天的日期，新用户看到 2026-10-23
    # 会以为「这模块里已经有东西了」。空槽位的 updated 就是打包当天。
    from datetime import date as _date

    fm = re.sub(r"(?m)^updated:.*$", f"updated: {_date.today().isoformat()}", fm)
    head, _ = get_head(body)
    note = "\n> 这个模块是空的：按自己的情况往里加条目，一条只记一个事实，格式见 `_模板.md`。\n"
    return fm + head + note


def to_reset(text: str) -> str:
    fm, body = split_doc(text)
    lines = body.replace("\r\n", "\n").split("\n")
    cut = next((i for i, ln in enumerate(lines) if re.match(r"^\|\s*:?-{3,}", ln)), -1)
    if cut >= 0:
        # 只留表头 + 分隔行：表头取分隔行以上的第一条表格行，中间的数据行一律丢掉
        head_at = next((i for i in range(0, cut) if lines[i].lstrip().startswith("|")), -1)
        if head_at >= 0:
            return fm + "\n".join(lines[:head_at] + [lines[head_at], lines[cut]]) + "\n"
        return fm + "\n".join(lines[: cut + 1]) + "\n"
    head, _ = get_head(body)
    return fm + head + "\n> 已清空：这里留你自己的记录。\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--out", default="")
    ap.add_argument("--no-zip", action="store_true", help="只生成解压目录，不压包（抽检用）")
    ap.add_argument("--keep-dir", action="store_true", help="压包的同时保留解压目录")
    ap.add_argument("--persona", action="store_true",
                    help="额外把 tools/persona/ 的可分发人格放进包（人格包）")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    refs = root / "references"
    cfg_file = root / "memory.config.json"
    if not cfg_file.exists():
        print("❌ 缺 memory.config.json，先跑 tools/setup.py")
        return 1

    cfg = json.loads(read(cfg_file))
    version_file = root / "VERSION"
    ver = read(version_file).strip() if version_file.exists() else "0.0.0"
    if not ver:
        ver = "0.0.0"

    paths = cfg.get("paths") or {}
    placeholders = cfg.get("placeholders") or {}
    mapping: list[tuple[str, str]] = []
    for key in ("workspace", "codeRepo", "algoRepo", "javaVault", "home"):
        real, ph = paths.get(key), placeholders.get(key)
        if real and ph:
            mapping.append((str(real), str(ph)))
    for k, v in (cfg.get("pack", {}).get("scrub") or {}).items():
        # v 允许是空串（「喵」→删掉、「home\notes\」→删掉）：原来写 `if k and v` 会把空串目标整条丢掉，
        # 结果包里留着该抹掉的词（2026-10-20 抓到，扫描命中 `喵`）
        if k and v is not None:
            mapping.append((str(k), str(v)))
    mapping.sort(key=lambda kv: len(kv[0]), reverse=True)

    # 任何盘符 + Users + 用户名，统一抹成占位符（2026-10-20 加）：光靠 scrub 表按名字替换会漏掉
    # 另一台机器的用户名（本机与台式机器名不同，写进文档就带出去了），换机一次就漏一次。
    WIN_HOME_RE = re.compile(r"(?i)\b[A-Z]:[\\/]+Users[\\/]+[^\\/\s`'\"]+")

    def sanitize(text: str) -> str:
        for real, ph in mapping:
            if real:
                text = text.replace(real, ph)
        # json/yml 里路径是转义写的（C:\\Users\\user），单反斜杠正则匹配不到，等于漏用户名
        WIN_HOME_ESC_RE = re.compile(r"(?i)[A-Z]:[\\\\/]+Users[\\\\/]+[^\\\\/\s\"']+")
        text = WIN_HOME_ESC_RE.sub("%USERPROFILE%", text)
        return WIN_HOME_RE.sub("%USERPROFILE%", text)

    # 2026-10-24：四值断言必须先跑、并且在动 dist 之前跑。
    # 原来的位置在复制完、开始处理 references 时，那时 dist/<版本> 已经被 rmtree 重建过了——
    # 断言一炸就留下一个半成品包（真的发生过：red test 塞了个 distribute: nuke 的探针，
    # 结果 dist/user-memory-0.3.1-20261024 被清空、只剩半个 references/）。
    ALLOWED = ("keep", "blank", "reset", "drop")
    src_refs = root / "references"
    bad: list[str] = []
    for f in sorted(src_refs.glob("*.md")):
        if f.name in ("CATALOG.md", "_模板.md"):
            continue
        v = front_field(read(f), "distribute", default_mode(f.stem))
        if v not in ALLOWED:
            bad.append(f"{f.name} -> {v!r}")
    if bad:
        print("❌ distribute 值非法，本次一个文件都没动（dist 保持原样）：")
        for b in bad:
            print(f"   {b}")
        print("   只允许 keep / blank / reset / drop")
        return 1

    stamp = datetime.now().strftime("%Y%m%d")
    name = f"user-memory-{ver}-{stamp}"
    dist_root = root / "dist"
    dist_root.mkdir(parents=True, exist_ok=True)
    # 2026-10-24 她定的口径：dist/ 只存版本压缩包，不留解压目录。
    # 默认在 dist/ 下的临时目录里组装、打完 zip 就删；
    # 要抽检内容用 --no-zip（出目录不压包）或 --keep-dir（压包 + 留目录）。
    tmp_parent = None
    if args.no_zip or args.keep_dir:
        target = dist_root / name
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
    else:
        tmp_parent = Path(tempfile.mkdtemp(prefix="pack-", dir=str(dist_root)))
        target = tmp_parent / name
        target.mkdir(parents=True)

    pack_cfg = cfg.get("pack") or {}
    ex_dirs = set(pack_cfg.get("excludeDirs")
                 or ["archive", "__pycache__", ".git", ".framework", "fragments", "state"])
    # 2026-10-24：state/ 放的是机器状态（decay.json 等），不能跟着包发出去。
    # memory.config.json 里的 excludeDirs 会整个覆盖上面的默认值，所以这里再并一次，不管配置怎么写都排除。
    ex_dirs.add("state")
    ex_dirs.add(".framework")  # framework.py --build 的 worktree，里面是另一个分支的整棵树
    ex_files = set(pack_cfg.get("excludeFiles") or [".style_override.json", "memdb.sqlite3"])
    ex_files.add("memory.config.json")

    for src in root.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(root)
        if rel.parts and rel.parts[0] == "dist":
            continue
        if any(part in ex_dirs for part in rel.parts):
            continue
        # 人格模板只走 --persona 那条线进包（放在包根的 persona/），不跟着 tools/ 一起复制
        if rel.parts[:2] == ("tools", "persona"):
            continue
        # 两台机器的私人 AGENTS 副本（含个人规则与原话），只留在本机，不进包
        if rel.parts[:2] == ("templates", "hosts"):
            continue
        if src.name in ex_files or src.suffix in (".sqlite3", ".pyc", ".zip"):
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    stat = {"keep": 0, "blank": 0, "reset": 0, "drop": 0}
    dropped: list[str] = []
    ref_target = target / "references"
    for file in sorted(ref_target.glob("*.md")):
        if file.name in ("CATALOG.md", "_模板.md"):
            continue
        raw = read(file)
        mode = front_field(raw, "distribute", default_mode(file.stem))
        # 2026-10-24：隐私门只认这四个值，别的一律当场炸掉，不许静默当 keep 打包。
        if mode not in ("keep", "blank", "reset", "drop"):
            raise SystemExit(
                f"❌ {file.name} 的 distribute 值非法：{mode!r}"
                f"（只允许 keep / blank / reset / drop）"
            )
        if mode == "drop":
            file.unlink()
            dropped.append(file.stem)
            stat["drop"] += 1
            continue
        if mode == "blank":
            hint = file.stem.split("-", 1)[1] if "-" in file.stem else ""
            packed = to_blank(raw, hint)
            stat["blank"] += 1
        elif mode == "reset":
            packed = to_reset(raw)
            stat["reset"] += 1
        else:
            packed = raw
            stat["keep"] += 1
        write(file, sanitize(packed))

    # 2026-10-24：ENTRY.md 是本机入口页（私库名、我自己的分类），framework 产物那边由
    # DROP_ROOT_FILES 剔掉，分发包漏了这条——桌面演练里它直接流出去了，还带着双重编码乱码。
    entry = target / "ENTRY.md"
    if entry.exists():
        entry.unlink()
        print("  剔掉 ENTRY.md（本机入口页，不进分发包）")

    for rel in ("SKILL.md", "README.md", "LICENSE", "setup.ps1", "templates/AGENTS.md",
                "references/_模板.md", "config.example.json"):
        p = target / rel
        if p.exists():
            write(p, sanitize(read(p)))

    # templates/AGENTS.md 是本机的规则版（硬塞层 + 个人规则），给别人必须换成空骨架版
    pub = root / "framework-public" / "public-AGENTS.md"
    if pub.exists() and (target / "templates" / "AGENTS.md").exists():
        write(target / "templates" / "AGENTS.md", sanitize(pub.read_text(encoding="utf-8")))

    tools_target = target / "tools"
    if tools_target.exists():
        for p in tools_target.iterdir():
            if p.suffix in (".py", ".ps1") and p.is_file():
                src = read(p)
                clean = sanitize(src)
                if clean != src:
                    write(p, clean)

    # 2026-10-24：templates/hooks 下的 json/yml 里写着 %USERPROFILE%\...，
    # 原来只处理 tools 下的 .py/.ps1，这些配置整个漏过去，等于把本机路径发出去。
    hooks_target = target / "templates" / "hooks"
    if hooks_target.is_dir():
        for hp in sorted(hooks_target.iterdir()):
            if hp.is_file() and hp.suffix in (".json", ".yml", ".yaml", ".ps1", ".md"):
                src = read(hp)
                clean = sanitize(src)
                if clean != src:
                    write(hp, clean)

    persona_note = ""
    if args.persona:
        src_dir = root / "tools" / "persona"
        if not src_dir.is_dir():
            print(f"⚠️ 找不到人格模板目录 {src_dir}，跳过")
        else:
            dst_dir = target / "persona"
            copied = 0
            for p in sorted(src_dir.glob("*.md")):
                if p.name.startswith("."):
                    continue
                dst_dir.mkdir(parents=True, exist_ok=True)
                write(dst_dir / p.name, sanitize(read(p)))
                copied += 1
            persona_note = f"人格模板 {copied} 份（persona/）"

    # 2026-10-24：CATALOG.md 是从主库复制过来的，里面列着 55 个模块（01 基本背景、10 健康、
    # 12a 英语…全是私人内容）。进包必须按包里真实剩下的模块重建，只留 90–99 + 协议件。
    cat = ref_target / "CATALOG.md"
    if cat.exists():
        rows = []
        for f in sorted(ref_target.glob("*.md")):
            if f.name in ("CATALOG.md", "_模板.md"):
                continue
            txt = read(f)
            title = front_field(txt, "title", f.stem).strip('"') or f.stem
            kws = front_field(txt, "keywords", "").strip()
            parts = [x.strip(" -\"") for x in kws.splitlines()]
            words = [w for w in " ".join(parts).replace(",", " ").split() if w and w not in ("keywords:",)]
            updated = front_field(txt, "updated", "").strip('"')
            lines = len(txt.splitlines())
            kb = max(1, round(len(txt.encode("utf-8")) / 1024))
            rows.append(f"| `{f.stem}` | {title} | {'、'.join(words)[:60]} | {lines} | {kb}KB | {updated} |")
        head = (
            "# 记忆库总览（CATALOG）\n\n"
            "> 本文件由 `tools/catalog.py` 自动生成，别手改；改完模块跑一次脚本刷新。\n"
            "> 用途：换设备或新会话开局读这一页就够导航，不用打开模块正文。\n\n"
            f"模块数：{len(rows)}　生成时间：{datetime.now().strftime('%Y-%m-%d')}\n\n"
            "| 模块 | 标题 | 关键词 | 行数 | 大小 | 更新 |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
        )
        write(cat, head + "\n".join(rows) + "\n")
        print(f"   CATALOG 已按包内模块重建：{len(rows)} 个")

    # _路由详表.md：体检靠它核对模块是否登记（同 framework 侧，见那里的注释）。
    detail = ["# 路由详表", "",
              "> 由 `tools/pack.py` 按包内模块生成，别手改。", "",
              "| 模块 | 标题 |", "| --- | --- |"]
    skip = {"CATALOG.md", "_模板.md", "_路由详表.md", "_巡检记录.md", "_复核清单.md", "_合并候选.md"}
    for f in sorted(ref_target.glob("*.md")):
        if f.name in skip:
            continue
        stem = f.stem
        if not (re.match(r"^\d{2}[a-z]?-", f.name) or stem in ("_待确认", "变更日志")):
            continue
        txt = read(f)
        title = front_field(txt, "title", stem).strip('"') or stem
        detail.append(f"| `{stem}` | {title} |")
    write(ref_target / "_路由详表.md", "\n".join(detail) + "\n")
    print(f"   _路由详表 已按包内模块重建：{len(detail) - 5} 个")

    # 收尾：把 relates_to 里指向「已被 drop 的模块」的编号摘掉。
    # 不摘的话，收包人看到的是一串指向不存在模块的引用（2026-10-20 第二遍排查发现的）。
    present_ids = set()
    for f in ref_target.glob("*.md"):
        m = re.match(r"^(\d+[a-z]?)-", f.name)
        if m:
            present_ids.add(m.group(1))
        present_ids.add(f.stem.split("-")[0])
    present_ids.update({"SKILL", "CATALOG"})
    for f in ref_target.glob("*.md"):
        text = read(f)
        new = re.sub(
            r"(?m)^(relates_to:\s*\[)(.*?)(\]\s*)$",
            lambda m: m.group(1)
            + ", ".join(
                x for x in re.findall(r'"[^"]*"', m.group(2))
                if x.strip('"') in present_ids
            )
            + m.group(3),
            text,
        )
        if new != text:
            write(f, new)

    dropped_specs: list[str] = []
    for d in dropped:
        dropped_specs.append(d)
        f = refs / (d + ".md")
        if f.exists():
            mid = front_field(read(f), "id", "").strip('"')
            if mid and mid != d:
                dropped_specs.append(mid)

    skill_path = target / "SKILL.md"
    if skill_path.exists():
        kept: list[str] = []
        in_block = False
        for ln in read(skill_path).replace("\r\n", "\n").split("\n"):
            if "dist:drop-begin" in ln:
                in_block = True
                continue
            if "dist:drop-end" in ln:
                in_block = False
                continue
            if in_block:
                continue
            if any(f"`{spec}`" in ln for spec in dropped_specs):
                continue
            kept.append(ln)
        write(skill_path, "\n".join(kept))

    # 2026-10-24：包里不再塞 memory.config.json。
    # 它必然是「路径全空」的模板，新人以为配好了其实全是空；更要命的是镜像同步
    # 会把本机那份真配置顺手覆盖掉（新设备演练就是这么把 workspace 指到空字符串的）。
    # 交付 config.example.json 就够，setup.ps1 负责生成 memory.config.json。

    # 2026-10-24：PowerShell 5.1 读「UTF-8 无 BOM」的 .ps1 会按 GBK 解析 —— 中文全乱、
    # 引号配对错乱，直接 SyntaxError（setup.ps1 就这么在新设备上打不开过）。
    # 上面的清洗走 write()，写出来天然无 BOM，所以这里统一给包内所有 .ps1 补回。
    for ps in target.rglob("*.ps1"):
        b = ps.read_bytes()
        if not b.startswith(b"\xef\xbb\xbf"):
            ps.write_bytes(b"\xef\xbb\xbf" + b)

    if pack_cfg.get("regenerateCatalog", True):
        catalog = target / "tools" / "catalog.py"
        if catalog.exists():
            import subprocess
            try:
                subprocess.run([sys.executable, str(catalog)], cwd=str(target), check=False)
            except Exception:
                pass

    write(target / "VERSION", ver + "\n")
    manifest = "\n".join([
        f"打包版本 v{ver}",
        f"生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"保留 keep {stat['keep']} / 清空 blank {stat['blank']} / 重置 reset {stat['reset']} / 不发 drop {stat['drop']}",
        "不发：" + "、".join(sorted(set(d.split("-")[0] for d in dropped))),
    ] + ([persona_note] if persona_note else []))
    write(target / "PACKINFO.txt", manifest)

    if args.no_zip:
        print(f"✅ 生成目录 {target}")
    else:
        out = Path(args.out) if args.out else dist_root / (name + ".zip")
        if out.exists():
            out.unlink()
        archive = shutil.make_archive(str(out.with_suffix("")), "zip", root_dir=target)
        print(f"✅ 打包完成 {archive}")
        if tmp_parent is not None:
            # dist/ 里只留 zip：临时组装目录用完即删
            shutil.rmtree(tmp_parent, ignore_errors=True)
    print(f"   保留 {stat['keep']} · 清空 {stat['blank']} · 重置 {stat['reset']} · 不发 {stat['drop']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
