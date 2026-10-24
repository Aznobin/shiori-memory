#!/usr/bin/env python3
"""产物冒烟测试：把框架包当成「别人刚下载下来的东西」跑一遍。

为什么非要有它：隐私清洗（sanitize）会改代码，主库全绿不代表包里能跑。
0.3.2 就是这么发出去的——包里 `framework.py` 语法错误、`update.py` 的仓库地址
被换成占位符，两条命令直接崩，而主库 `memory_check` 一直是绿的。

用法：
    python tools/verify_pack.py             # 从 framework 分支导出最新包来测
    python tools/verify_pack.py --src DIR   # 测一个现成的产物目录
    python tools/verify_pack.py --keep      # 保留临时目录，方便手看
    python tools/verify_pack.py --net       # 额外测 update.py check（要网络）

判据：P0 全过 → exit 0；任何 P0 红 → exit 1。发版前必须绿。
"""

from __future__ import annotations

import argparse
import compileall
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANCH = "framework"

P0 = "P0"
P1 = "P1"

_results: list[tuple[str, str, str, str]] = []  # (级别, 名字, 状态, 详情)


def record(level: str, name: str, ok: bool | None, detail: str) -> None:
    status = "PASS" if ok is True else ("WARN" if ok is None else "FAIL")
    _results.append((level, name, status, detail))


def pack_kind(pkg: Path) -> str:
    """两种产物形态，判据不一样，别混着测：

    - framework：framework.py 从 orphan 分支导出的骨架包，自带根 AGENTS.md + manifest.json
    - pack：pack.py 出的分发包，根 AGENTS.md 由用户自己的工作区持有，包里只给 templates/AGENTS.md
    """
    if (pkg / "manifest.json").exists() or (pkg / "AGENTS.md").exists():
        return "framework"
    if (pkg / "templates" / "AGENTS.md").exists():
        return "pack"
    return "unknown"


def export_from_branch(dest: Path) -> bool:
    """把 framework 分支整棵导出来当产物（不依赖 dist/ 是否新鲜）。"""
    zip_path = dest.parent / "_pack.zip"
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "archive", BRANCH,
                            "--format=zip", "-o", str(zip_path)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False
    if r.returncode != 0 or not zip_path.exists():
        return False
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    zip_path.unlink(missing_ok=True)
    return True


def run(pkg: Path, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=str(pkg), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)


def tail(text: str, n: int = 3) -> str:
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return " / ".join(lines[-n:])[:220]


def check_syntax(pkg: Path) -> None:
    bad: list[str] = []
    files = sorted(list(pkg.rglob("*.py")))
    for f in files:
        try:
            compile(f.read_text(encoding="utf-8"), str(f), "exec")
        except SyntaxError as e:
            bad.append(f"{f.relative_to(pkg).as_posix()}:{e.lineno}: {e.msg}")
    record(P0, f"py_compile（{len(files)} 个脚本）", not bad,
           "全部可编译" if not bad else "; ".join(bad[:3]))


def check_urls(pkg: Path) -> None:
    """包里出现的 http(s) 地址不能有占位符、空格、中文。"""
    bad: list[str] = []
    for f in sorted(list(pkg.rglob("*.py")) + list(pkg.rglob("*.ps1"))):
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "http" not in line:
                continue
            for token in ("https://", "http://"):
                idx = line.find(token)
                while idx != -1:
                    url = line[idx:].split('"')[0].split("'")[0].split()[0]
                    url = url.rstrip('",)}')
                    if any(c in url for c in "<>") or any("\u4e00" <= c <= "\u9fff" for c in url):
                        bad.append(f"{f.relative_to(pkg).as_posix()}:{i}: {url[:80]}")
                        break
                    idx = line.find(token, idx + 1)
    record(P0, "URL 无占位符", not bad, "全部正常" if not bad else "; ".join(bad[:3]))


def check_frag(pkg: Path) -> None:
    # 2026-10-24：以前直接在产物目录里跑 build，等于当场给它写进
    # fragments/index.json、memdb.sqlite3、state/decay.json —— 复制部署时这些派生层
    # 跟着上车，新设备一开始就是「已有索引」的状态，还会带打包机的旧时间戳
    # （新设备演练时发现的）。现在整包复制到临时目录再跑，产物目录保持干净。
    with tempfile.TemporaryDirectory(prefix="verify-frag-") as td:
        work = Path(td) / pkg.name
        shutil.copytree(pkg, work)
        _check_frag_in(work)


def _check_frag_in(pkg: Path) -> None:
    r = run(pkg, "tools/frag.py", "build")
    ok = r.returncode == 0
    detail = tail(r.stdout + r.stderr)
    record(P0, "frag.py build", ok, detail)
    if not ok:
        return
    n = 0
    try:
        idx = json.loads((pkg / "fragments" / "index.json").read_text(encoding="utf-8"))
        if isinstance(idx, list):
            n = len(idx)
        else:
            n = len(idx.get("fragments") or idx.get("items") or idx.get("片段") or [])
    except Exception as e:  # noqa: BLE001
        record(P0, "fragments/index.json", False, f"读不出来: {e}")
        return
    record(P0, "fragments/index.json", n > 0, f"{n} 条片段")

    r2 = run(pkg, "tools/frag.py", "lint")
    record(P1, "frag.py lint", r2.returncode == 0, tail(r2.stdout + r2.stderr))

    r3 = run(pkg, "tools/frag.py", "search", "记忆")
    record(P1, "frag.py search（空库）", None, tail(r3.stdout + r3.stderr, 2))


def check_routes(pkg: Path) -> None:
    target = pkg / "AGENTS.md"
    if not target.exists():
        record(P1, "routes.py 写入 AGENTS.md", None,
               "包内没有根 AGENTS.md（pack 产物由用户工作区持有），跳过")
        return
    r = run(pkg, "tools/routes.py")
    ok = r.returncode == 0
    if ok:
        text = target.read_text(encoding="utf-8", errors="replace")
        block = text.split("<!-- ROUTES:BEGIN -->")[-1].split("<!-- ROUTES:END -->")[0]
        rows = [l for l in block.splitlines() if l.strip().startswith("|")]
        ok = len(rows) >= 3
        record(P0, "routes.py 写入 AGENTS.md", ok, f"{len(rows)} 行表格")
        return
    record(P0, "routes.py 写入 AGENTS.md", False, tail(r.stdout + r.stderr))


def check_health(pkg: Path) -> None:
    r = run(pkg, "tools/memory_check.py")
    record(P1, "memory_check.py", r.returncode == 0, tail(r.stdout + r.stderr, 4))


def check_update(pkg: Path, net: bool) -> None:
    if not net:
        record(P1, "update.py check", None, "跳过（加 --net 才测）")
        return
    try:
        r = run(pkg, "tools/update.py", "check", timeout=60)
        record(P1, "update.py check", r.returncode == 0, tail(r.stdout + r.stderr))
    except subprocess.TimeoutExpired:
        record(P1, "update.py check", None, "超时（网络）")


def check_assets(pkg: Path) -> None:
    kind = pack_kind(pkg)
    if kind == "framework":
        want = ["AGENTS.md", "SKILL.md", "README.md", "VERSION", "manifest.json", "LICENSE",
                "references", "tools", "bin", "setup.ps1", "config.example.json"]
        banned = ["ENTRY.md", "templates/AGENTS.md"]
    else:  # pack：根 AGENTS.md / manifest.json 本来就不该在包里
        want = ["SKILL.md", "README.md", "VERSION", "LICENSE", "references", "tools", "bin",
                "setup.ps1", "config.example.json", "templates/AGENTS.md"]
        banned = ["ENTRY.md", "AGENTS.md", "manifest.json"]
    missing = [w for w in want if not (pkg / w).exists()]
    record(P0, f"必备文件齐全（{kind}）", not missing,
           "齐全" if not missing else "缺: " + ", ".join(missing))
    present = [b for b in banned if (pkg / b).exists()]
    record(P0, f"无私人文件（{kind}）", not present,
           "干净" if not present else "不该在: " + ", ".join(present))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", help="现成的产物目录（不导出分支）")
    ap.add_argument("--keep", action="store_true", help="保留临时目录")
    ap.add_argument("--net", action="store_true", help="联网测 update.py check")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="verify-pack-"))
    pkg = tmp / "pkg"
    pkg.mkdir()
    if args.src:
        src = Path(args.src).resolve()
        if not src.is_dir():
            print(f"目录不存在: {src}")
            return 1
        shutil.copytree(src, pkg, dirs_exist_ok=True)
        origin = str(src)
    else:
        if not export_from_branch(pkg):
            print("导出 framework 分支失败（没 git？或分支不存在）")
            return 1
        origin = f"git archive {BRANCH}"

    print(f"产物来源: {origin}")
    print("-" * 60)
    check_assets(pkg)
    check_syntax(pkg)
    check_urls(pkg)
    check_frag(pkg)
    check_routes(pkg)
    check_health(pkg)
    check_update(pkg, args.net)
    print("-" * 60)

    width = max(len(r[1]) for r in _results)
    for level, name, status, detail in _results:
        mark = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}[status]
        print(f"{mark} [{level}] {name.ljust(width)}  {detail}")

    p0_fail = [r for r in _results if r[0] == P0 and r[2] == "FAIL"]
    p1_fail = [r for r in _results if r[0] == P1 and r[2] == "FAIL"]
    print("-" * 60)
    print(f"P0 失败 {len(p0_fail)} · P1 失败 {len(p1_fail)}")
    if args.keep:
        print(f"临时目录保留在: {pkg}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if p0_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
