#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包成可发给别人的版本：个人信息清零、路径换成占位符、机器状态剔除（Python 版，跨平台）。

用法：
  python tools/pack.py                 # 产出 dist/user-memory-<版本>-<日期>.zip
  python tools/pack.py --no-zip        # 只生成目录，不压包
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


def to_blank(text: str) -> str:
    fm, body = split_doc(text)
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
    ap.add_argument("--no-zip", action="store_true")
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
        # v 允许是空串（「」→删掉、「」→删掉）：原来写 `if k and v` 会把空串目标整条丢掉，
        # 结果包里留着该抹掉的词（2026-10-20 抓到，扫描命中 ``）
        if k and v is not None:
            mapping.append((str(k), str(v)))
    mapping.sort(key=lambda kv: len(kv[0]), reverse=True)

    # 任何盘符 + Users + 用户名，统一抹成占位符（2026-10-20 加）：光靠 scrub 表按名字替换会漏掉
    # 另一台机器的用户名（本机与台式机器名不同，写进文档就带出去了），换机一次就漏一次。
    WIN_HOME_RE = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s`'\"]+")

    def sanitize(text: str) -> str:
        for real, ph in mapping:
            if real:
                text = text.replace(real, ph)
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
        v = front_field(read(f), "distribute", "keep")
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
    target = dist_root / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    pack_cfg = cfg.get("pack") or {}
    ex_dirs = set(pack_cfg.get("excludeDirs") or ["archive", "__pycache__", ".git", "fragments", "state"])
    # 2026-10-24：state/ 放的是机器状态（decay.json 等），不能跟着包发出去。
    # memory.config.json 里的 excludeDirs 会整个覆盖上面的默认值，所以这里再并一次，不管配置怎么写都排除。
    ex_dirs.add("state")
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
        mode = front_field(raw, "distribute", "keep")
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
            packed = to_blank(raw)
            stat["blank"] += 1
        elif mode == "reset":
            packed = to_reset(raw)
            stat["reset"] += 1
        else:
            packed = raw
            stat["keep"] += 1
        write(file, sanitize(packed))

    for rel in ("SKILL.md", "README.md", "ENTRY.md", "LICENSE", "setup.ps1", "templates/AGENTS.md", "references/_模板.md", "config.example.json"):
        p = target / rel
        if p.exists():
            write(p, sanitize(read(p)))

    tools_target = target / "tools"
    if tools_target.exists():
        for p in tools_target.iterdir():
            if p.suffix in (".py", ".ps1") and p.is_file():
                src = read(p)
                clean = sanitize(src)
                if clean != src:
                    write(p, clean)

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

    # 包里永远用模板配置
    example = root / "config.example.json"
    if example.exists():
        shutil.copy2(example, target / "memory.config.json")

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
    print(f"   保留 {stat['keep']} · 清空 {stat['blank']} · 重置 {stat['reset']} · 不发 {stat['drop']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
