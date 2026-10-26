#!/usr/bin/env python3
"""编号迁移：两位编号模块 → 三位编号（末位非 0）。

默认 dry-run，只统计不改盘；`--apply` 才落盘（改名 + 改文）。

只动这八类位置，别的一律不碰：
  1. 文件名            references/<旧>-<标题>.md → references/<新>-<标题>.md
  2. frontmatter       id: "303" → id: "303"
  3. frontmatter       relates_to: [...] 里的编号元素
  4. 编号 token        `302` / `303` / `303.012`（点号后不动）；代码里的 "302" 字面量同理
  5. 路径写法          memory/303-服务器与隧道.md 里的模块号
  6. 命令参数          python tools/memread.py "302" 里的模块号
  7. 模块号:行号        `89:<行号>` → `813:<行号>`（只换数字模块号；`_巡检记录:<行号>` 这类非数字模块名按裁定不动）
  8. 表格首格裸模块号  只认 `925.md`（99-架构大纲 / _合并候选 的首格是行序号，按裁定不换且只报不改）

绝不裸串替换：日期、时间、提交号、版本号、行号一律不碰。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_MAP = Path(r"%USERPROFILE%\Desktop\Shiori-编号映射草表.md")
DEFAULT_ROOT = Path(r"%USERPROFILE%\.agents\skills\user-memory")

# 生成物 / 历史文件：只读不改（`921` 变更日志的历史行按旧号保留；`927` / `928` 是生成物）
SKIP_FILES = {"921.md", "927.md", "928.md"}
SKIP_DIRS = {".git", "fragments", "dist", "__pycache__", "archive", "node_modules", "state"}
SCAN_SUFFIXES = {".md", ".py", ".ps1", ".mjs", ".json", ".txt"}
ROOT_FILES = ("SKILL.md", "README.md")


def load_mapping(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if "## 映射表" not in text:
        raise SystemExit(f"[无法确认] 映射文件里没有「## 映射表」小节：{path}")
    section = text.split("## 映射表", 1)[1].split("\n## ", 1)[0]
    mapping: dict[str, str] = {}
    for line in section.splitlines():
        m = re.match(r"\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|", line.strip())
        if m:
            mapping[m.group(1)] = m.group(2)
    if not mapping:
        raise SystemExit(f"[无法确认] 映射表解析出 0 条：{path}")
    return mapping


def iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    self_path = Path(__file__).resolve()
    for name in ROOT_FILES:
        p = root / name
        if p.is_file():
            files.append(p)
    for base in ("references", "tools", "templates", "framework-public"):
        d = root / base
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in SCAN_SUFFIXES:
                continue
            if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
                continue
            if p.resolve() == self_path:
                continue  # 迁移脚本自己的示例不许被自己改写
            if p.parent.name == "references" and p.name in SKIP_FILES:
                continue
            files.append(p)
    return files


def boundary(old: str) -> str:
    return rf"(?<![0-9A-Za-z]){re.escape(old)}(?![0-9A-Za-z])"


class Plan:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        # 长号优先：08a 必须先于 08 匹配
        self.olds = sorted(mapping, key=len, reverse=True)
        self.counts = {
            "rename": 0,
            "id": 0,
            "relates_to": 0,
            "token": 0,
            "path": 0,
            "command": 0,
            "module_line": 0,
            "table_cell": 0,
        }
        self.special = {k: 0 for k in ("701", "702", "303")}
        self.renames: list[tuple[Path, Path]] = []
        self.changed_files: set[Path] = set()
        self.residual = 0
        self.suspicious: list[tuple[Path, int, str]] = []
        self.kept: list[tuple[Path, int, str]] = []
        self.unknown_refs: set[str] = set()

    # ---- 八类替换 ----
    def note(self, old: str) -> None:
        if old in self.special:
            self.special[old] += 1

    def _sub(self, text: str, pat: re.Pattern[str], kind: str, repl) -> str:
        def run(m: re.Match[str]) -> str:
            out = repl(m)
            if out != m.group(0):  # 原样返回的不算一次替换
                self.counts[kind] += 1
                self.note(m.group("old"))
            return out

        return pat.sub(run, text)

    def rewrite_text(self, path: Path, text: str) -> str:
        before = text
        alt = "|".join(boundary(o) for o in self.olds)
        # 5. 路径写法：references/<旧>-标题.md 与不带前缀的 <旧>-标题.md / <旧>.md
        path_pat = re.compile(
            rf"(?P<pre>references[\\/])?(?P<old>{alt})(?=-[^\s`|]+\.md(?![A-Za-z0-9])|\.md(?![A-Za-z0-9]))"
        )
        text = self._sub(
            text,
            path_pat,
            "path",
            lambda m: (m.group("pre") or "") + self.mapping[m.group("old")],
        )
        # 5b. 不带扩展名的模块名：`83-自维护` / 83-自维护（前面的字符不许是 . 或字母数字，避免撞上 1.13-free 这类版本串）
        bare_name_pat = re.compile(
            rf"(?<![0-9A-Za-z.])(?P<old>{alt})(?=-(?!\d)[^\s`|]+(?![A-Za-z0-9]))"
        )
        text = self._sub(text, bare_name_pat, "path", lambda m: self.mapping[m.group("old")])
        # 6. 命令参数（memread.py "302"）
        cmd_pat = re.compile(
            rf"(?P<pre>(?:memread|frag|routes)\.py\s+(?:--\S+\s+)*(?:auto\s+)?)(?P<q>[\"']?)(?P<old>{alt})(?P=q)"
        )
        text = self._sub(
            text,
            cmd_pat,
            "command",
            lambda m: m.group("pre") + m.group("q") + self.mapping[m.group("old")] + m.group("q"),
        )
        # 2. frontmatter id
        id_pat = re.compile(rf"^(?P<pre>\s*id:\s*)(?P<q>[\"']?)(?P<old>{alt})(?P=q)(?P<post>\s*)$", re.M)
        text = self._sub(
            text,
            id_pat,
            "id",
            lambda m: m.group("pre") + m.group("q") + self.mapping[m.group("old")] + m.group("q") + m.group("post"),
        )
        # 3. frontmatter relates_to
        rel_pat = re.compile(rf"^(?P<pre>\s*relates_to:\s*\[)(?P<inner>[^\]]*)(?P<post>\]\s*)$", re.M)

        def rel_repl(m: re.Match[str]) -> str:
            inner = m.group("inner")

            def one(mm: re.Match[str]) -> str:
                self.counts["relates_to"] += 1
                self.note(mm.group("old"))
                return self.mapping[mm.group("old")]

            inner = re.sub(rf"(?P<q>[\"']?)(?P<old>{alt})(?P=q)", one, inner)
            return m.group("pre") + inner + m.group("post")

        text = rel_pat.sub(rel_repl, text)
        # 4. 编号 token：反引号 / 引号包起来的纯模块号（可带 .012 片段号），以及裸片段号
        token_pat = re.compile(rf"(?P<q>`)(?P<old>{alt})(?P<frag>\.\d{{3}})?(?P=q)")
        text = self._sub(
            text,
            token_pat,
            "token",
            lambda m: "`" + self.mapping[m.group("old")] + (m.group("frag") or "") + "`",
        )
        quote_pat = re.compile(rf"(?P<q>[\"'])(?P<old>{alt})(?P<frag>\.\d{{3}})?(?P=q)")
        text = self._sub(
            text,
            quote_pat,
            "token",
            lambda m: m.group("q") + self.mapping[m.group("old")] + (m.group("frag") or "") + m.group("q"),
        )
        bare_frag_pat = re.compile(rf"(?P<old>{alt})(?P<frag>\.\d{{3}})(?![0-9])")
        text = self._sub(
            text,
            bare_frag_pat,
            "token",
            lambda m: self.mapping[m.group("old")] + m.group("frag"),
        )
        # 7. 模块号:行号 —— 只换数字模块号；非数字模块名（如 `_巡检记录:<行号>`）按裁定不动
        ml_pat = re.compile(rf"(?<![\d./])(?P<old>{alt}):(?P<ln>\d{{3}})(?![0-9])")
        text = self._sub(
            text,
            ml_pat,
            "module_line",
            lambda m: (
                m.group(0) if not m.group("old").isdigit() else self.mapping[m.group("old")] + ":" + m.group("ln")
            ),
        )
        # 8. 表格首格裸模块号 —— 只认 `925.md`（99-架构大纲 / _合并候选 的首格是行序号，按裁定不换）
        if path.name in ("925.md", "925-复核清单.md"):
            cell_pat = re.compile(rf"^(?P<pre>\|\s*)(?P<old>{alt})(?P<post>\s*\|)", re.M)
            text = self._sub(
                text,
                cell_pat,
                "table_cell",
                lambda m: m.group("pre") + self.mapping[m.group("old")] + m.group("post"),
            )
        if text != before:
            self.changed_files.add(path)
        return text

    def plan_renames(self, root: Path, mapping: dict[str, str]) -> None:
        # 两处会被改名的模块目录：references/ 本体 与 framework-public/ 的 public- 前缀模板
        for base, prefix in ((root / "references", ""), (root / "framework-public", "public-")):
            if not base.is_dir():
                continue
            for p in sorted(base.glob("*.md")):
                if not prefix and p.name in SKIP_FILES:
                    continue
                stem = p.stem
                if prefix:
                    if not stem.startswith(prefix):
                        continue
                    stem = stem[len(prefix) :]
                old = None
                rest = None
                for o in self.olds:
                    if stem == o:
                        old, rest = o, ""
                        break
                    if stem.startswith(o + "-"):
                        old, rest = o, stem[len(o) + 1 :]
                        break
                if old is None:
                    continue
                core = f"{mapping[old]}-{rest}" if rest else mapping[old]
                self.renames.append((p, base / f"{prefix}{core}.md"))
                self.counts["rename"] += 1

    def suspicious_scan(self, path: Path, text: str) -> None:
        """已裁定类别之外的模块引用形态：发现即报，绝不擅自替换。

        裁定（2026-10-27）：
          - `模块号:行号`：数字模块号要换，非数字模块名不动（见 adjudicated_scan）
          - 表格首格裸编号：`925.md` 是模块号要换；99-架构大纲 / _合并候选 是行序号，
            保持原样但仍列在报告里（就是这里报出来的 suspicious）
        """
        alt = "|".join(boundary(o) for o in self.olds)
        wiki = re.compile(rf"\[\[(?P<old>{alt})")
        for m in wiki.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            self.suspicious.append((path, line, f"wiki:{m.group(0)[:60]}"))
        if path.suffix == ".md":
            pat = re.compile(rf"^\|\s*(?P<old>{alt})\s*\|", re.M)
            if path.name in ("925.md", "925-复核清单.md"):
                return  # 该文件首格是模块号，由 rewrite_text 换掉
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                self.suspicious.append((path, line, f"keep 行序号:{m.group(0)[:60]}"))

    def adjudicated_scan(self, path: Path, text: str) -> None:
        """已裁定「不动」的命中：非数字模块名的 `xxx:行号` 引用，登记备查。"""
        alt = "|".join(boundary(o) for o in self.olds)
        pat = re.compile(rf"(?<![\d./])(?P<old>{alt}):\d{{3}}(?![0-9])")
        for m in pat.finditer(text):
            if m.group("old").isdigit():
                continue  # 数字模块号会在 rewrite_text 里换掉
            line = text.count("\n", 0, m.start()) + 1
            self.kept.append((path, line, f"keep 非数字模块名:{m.group(0)}"))

    def special_scan(self, path: Path, text: str) -> None:
        """10 / 13 / 08a 的命中数由替换阶段统计（note），这里不再重复计。"""
        return


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="编号迁移（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真正落盘；默认只 dry-run")
    ap.add_argument("--map", default=str(DEFAULT_MAP), help="映射表路径")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="记忆库根目录")
    args = ap.parse_args(argv)

    root = Path(args.root)
    map_path = Path(args.map)
    if not root.is_dir():
        print(f"[无法确认] 根目录不存在：{root}")
        return 2
    if not map_path.is_file():
        print(f"[无法确认] 映射表不存在：{map_path}")
        return 2

    mapping = load_mapping(map_path)
    plan = Plan(mapping)
    plan.plan_renames(root, mapping)

    for p in iter_files(root):
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        plan.suspicious_scan(p, text)
        plan.adjudicated_scan(p, text)
        plan.special_scan(p, text)
        new_text = plan.rewrite_text(p, text)
        # 幂等自检：改完再跑一遍，不该再有可替换处
        probe = Plan(mapping)
        probe.rewrite_text(p, new_text)
        plan.residual += sum(probe.counts.values())
        if args.apply and new_text != text:
            p.write_text(new_text, encoding="utf-8", newline="")

    if args.apply:
        for src, dst in plan.renames:
            if dst.exists():
                print(f"[无法确认] 目标已存在，停手：{dst}")
                return 2
            src.rename(dst)

    mode = "apply" if args.apply else "dry"
    total = sum(plan.counts.values())
    print(f"mode={mode} map={len(mapping)} rename={plan.counts['rename']} files={len(plan.changed_files)} replacements={total}")
    print(
        "  id={id} relates_to={relates_to} token={token} path={path} command={command} rename={rename} "
        "module_line={module_line} table_cell={table_cell}".format(**plan.counts)
    )
    print(f"  special 10={plan.special['701']} 13={plan.special['702']} 08a={plan.special['303']}")
    print(f"  residual={plan.residual} suspicious={len(plan.suspicious)} kept={len(plan.kept)}")
    for p, line, what in plan.suspicious[:20]:
        print(f"    {p.relative_to(root)}:{line} {what}")
    for p, line, what in plan.kept[:20]:
        print(f"    {p.relative_to(root)}:{line} {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
