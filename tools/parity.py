#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双份脚本对照：`tools/` 下同名的 `.py` / `.ps1` 各跑一次，比 stdout 与退出码。

用法：
    python tools/parity.py                 # 只跑只读脚本（默认），全绿退出 0
    python tools/parity.py --include-heavy # 连有副作用的脚本一起跑（会写 dist/、AGENTS.md）
    python tools/parity.py --all           # = --include-heavy
    python tools/parity.py --timeout 300   # 单个脚本的超时秒数

为什么要有它（2026-10-24，桌面《记忆库修复大纲》步骤 11 / S-2）：`tools/` 里
`.py` 与 `.ps1` 是两份独立实现，两份都没测过，早就分叉了没人发现。这里只做
「跑一遍、对一眼」：stdout 与退出码任一不同就报红并打印差异摘要。

配对规则：把两边的文件名归一（连字符 `-` 换成下划线 `_`，去掉扩展名）后相同即成对。
只有一边的（`check-modules.ps1` 这种）只算「无对照」信息行，**不进不一致计数**。

退出码口径（2026-10-24 Lead 定）：只有**成对的两个都存在、且 stdout 或退出码不同**
才 exit 1；跑不起来、跳过、无对照都只当信息行，exit 0。

注意：`pack` / `setup` 会写盘（`dist/`、工作区 `AGENTS.md`），默认**跳过**不跑；
要跑就显式 `--include-heavy`。别的脚本按只读处理。
"""
from __future__ import annotations

import argparse
import difflib
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

# 有副作用：会写 dist/ 或工作区文件，默认不跑
HEAVY: dict[str, str] = {
    "pack": "会往 dist/ 写打包产物",
    "setup": "会写工作区 AGENTS.md、重建索引",
}


def norm(stem: str) -> str:
    return stem.replace("-", "_")


def collect() -> tuple[dict[str, dict[str, Path]], list[tuple[str, str]]]:
    pairs: dict[str, dict[str, Path]] = {}
    for p in sorted(TOOLS.iterdir()):
        if not p.is_file() or p.suffix not in (".py", ".ps1"):
            continue
        key = norm(p.stem)
        pairs.setdefault(key, {})[p.suffix] = p
    only = []
    for key in sorted(pairs):
        got = set(pairs[key])
        if got == {".py"}:
            only.append((key, "只有 .py"))
        elif got == {".ps1"}:
            only.append((key, "只有 .ps1"))
    return pairs, only


def shell_for_ps1() -> list[str] | None:
    if shutil.which("pwsh"):
        return ["pwsh", "-NoProfile", "-File"]
    if shutil.which("powershell.exe"):
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    return None


def run(cmd: list[str], timeout: int) -> tuple[int | None, str, str]:
    """返回 (退出码, stdout, stderr)；启动失败时退出码为 None。"""
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "", f"超时（>{timeout}s）"
    except OSError as exc:
        return None, "", f"启动失败: {exc}"
    return p.returncode, p.stdout or "", p.stderr or ""


def norm_out(text: str) -> list[str]:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def diff_summary(py_lines: list[str], ps_lines: list[str], limit: int = 16) -> str:
    diff = list(difflib.unified_diff(py_lines, ps_lines, fromfile="py", tofile="ps1", n=1, lineterm=""))
    out = diff[:limit]
    if len(diff) > limit:
        out.append(f"...（差异共 {len(diff)} 行，只显示前 {limit} 行）")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="tools/ 下 .py / .ps1 双份脚本对照")
    ap.add_argument("--include-heavy", "--all", dest="heavy", action="store_true",
                    help="连会写盘的 pack / setup 一起跑")
    ap.add_argument("--timeout", type=int, default=180, help="单个脚本超时秒数（默认 180）")
    ap.add_argument("--diff-lines", type=int, default=16, help="每份不一致最多打印几行差异（默认 16）")
    args = ap.parse_args()

    pairs, only = collect()
    ps_prefix = shell_for_ps1()
    py_prefix = [sys.executable]

    ok = bad = skipped = failed = 0
    print(f"py/ps1 对照（{TOOLS}，python = {sys.executable}）")
    print("")

    for key in sorted(pairs):
        got = pairs[key]
        if ".py" not in got or ".ps1" not in got:
            continue
        py, ps = got[".py"], got[".ps1"]
        if ps_prefix is None:
            print(f"- {key}: ⚠️ 跳过（找不到 pwsh / powershell.exe，.ps1 跑不起来）")
            skipped += 1
            continue
        if key in HEAVY and not args.heavy:
            print(f"- {key}: ⏭️ 跳过（有副作用：{HEAVY[key]}；要跑加 --include-heavy）")
            skipped += 1
            continue
        py_code, py_out, py_err = run(py_prefix + [str(py)], args.timeout)
        ps_code, ps_out, ps_err = run(ps_prefix + [str(ps)], args.timeout)
        if py_code is None or ps_code is None:
            failed += 1
            print(f"- {key}: ❌ 跑不起来（{py.name} {py_code} / {ps.name} {ps_code}）")
            for who, err in (("py", py_err), ("ps1", ps_err)):
                if err.strip():
                    print(f"    {who}: {err.strip().splitlines()[0]}")
            continue
        py_lines, ps_lines = norm_out(py_out), norm_out(ps_out)
        if py_code == ps_code and py_lines == ps_lines:
            ok += 1
            print(f"- {key}: ✅ 一致（退出 {py_code}，输出 {len(py_lines)} 行）")
            continue
        bad += 1
        print(f"- {key}: ❌ 不一致（退出码 py={py_code} / ps1={ps_code}；"
              f"输出 {len(py_lines)} / {len(ps_lines)} 行）")
        print(diff_summary(py_lines, ps_lines, args.diff_lines))

    print("")
    ps_only = [(k, w) for k, w in only if "ps1" in w]
    py_only = [(k, w) for k, w in only if "py" in w]
    for key, why in ps_only:
        print(f"- {key}: ℹ️ 无对照（{why}）—— 只有 ps1、没有 py 版，不算不一致")
    if py_only:
        print(f"- ℹ️ 无对照（只有 .py、没有 ps1 版）：{len(py_only)} 个，不算不一致")
    print("")
    total = ok + bad + failed
    print(f"汇总：成对 {total} · 一致 {ok} · 不一致 {bad} · 跑不起来 {failed} · "
          f"跳过 {skipped} · 无对照 {len(ps_only) + len(py_only)}"
          f"（ps1 {len(ps_only)} / py {len(py_only)}）")
    if bad:
        print(f"结果：{bad} 对不一致")
        return 1
    print("结果：全绿（成对的一致；无对照只当信息行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
