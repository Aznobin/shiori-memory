"""日结算：一条命令把每天收尾要跑的东西全串起来，不用她提醒。

python tools/daily.py          # 全跑（日记归档 → 片段 → 索引 → 体检 → 自省 → 推 git）
python tools/daily.py --dry    # 只列步骤，不执行

设计原则：
- 每一步失败都只记一句、继续往下走，不拦整轮（她睡觉前不该卡在某个脚本上）
- 结论压成一行写进 `references/_巡检记录.md`（只留最近 5 条）
- 只动记忆模块自身；日记原文、笔记库、代码仓库一律不碰
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
INSPECT = ROOT / "references" / "_巡检记录.md"
PY = sys.executable or shutil.which("python") or "python"


def run(cmd: list[str], timeout: int = 180) -> tuple[int, str]:
    """跑一条命令，返回 (退出码, 合并后的末尾输出)。永不抛异常。"""
    try:
        p = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()
    except Exception as exc:  # noqa: BLE001 - 日结算不允许被单步炸掉
        return 1, f"{type(exc).__name__}: {exc}"


def powershell() -> list[str]:
    """优先 pwsh，退回 Windows PowerShell；都没有就跳过 ps1 体检。"""
    for name in ("pwsh", "powershell.exe", "powershell"):
        exe = shutil.which(name)
        if exe:
            return [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    return []


def checker(py_name: str, ps_name: str) -> list[str]:
    """体检优先用跨平台的 Python 版；没有就退回 .ps1；都没有返回空。"""
    py_file = TOOLS / py_name
    if py_file.exists():
        return [PY, str(py_file)]
    ps = powershell()
    return ps + [str(TOOLS / ps_name)] if ps else []


def tail(text: str, n: int = 1) -> str:
    lines = [x for x in text.strip().splitlines() if x.strip()]
    return " / ".join(lines[-n:])[:200] if lines else ""


def parse(pattern: str, text: str, default: str = "?") -> str:
    m = re.search(pattern, text)
    return m.group(1) if m else default


def write_inspect(row: dict[str, str]) -> None:
    """把这一轮结论插进巡检记录的表头下，只留最近 5 条。

    2026-10-24 女儿发现的坑：分发包里的巡检记录是空白壳（只有说明文字、没有表格），
    原来这里找不到 `| ---` 就静默 return——于是体检结论永远写不进去，而且完全看不出
    原因，`memory_check` 的「没有任何体检日期」会一直报下去。缺表头就补一份四列的。
    """
    if not INSPECT.exists():
        return
    lines = INSPECT.read_text(encoding="utf-8").splitlines()
    sep = next((i for i, l in enumerate(lines) if re.match(r"^\|\s*-{2,}", l)), -1)
    if sep < 0:
        lines += ["", "| 日期 | 结论 | 新发现 | 动作 |", "| --- | --- | --- | --- |"]
        sep = len(lines) - 1
    new = "| {date} | {result} | {found} | {action} |".format(**row)
    head, body = lines[: sep + 1], lines[sep + 1 :]
    rows = [l for l in body if l.startswith("| ")]
    other = [l for l in body if not l.startswith("| ")]
    rows = [new] + rows[:4]
    INSPECT.write_text("\n".join(head + rows + other) + "\n", encoding="utf-8")


def main() -> int:
    # Windows 控制台默认 GBK，巡检结论里有 emoji / 生僻字时 print 会 UnicodeEncodeError 崩掉；
    # 别的工具都带了这句，就这里漏了。errors="replace" 保证再怪的字符也只换个问号，不炸。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    dry = "--dry" in sys.argv
    now = datetime.now()

    steps: list[tuple[str, list[str]]] = [
        ("日记归档", [PY, str(TOOLS / "diary_sync.py"), "--all-missing"]),
        ("片段重建", [PY, str(TOOLS / "frag.py"), "build"]),
        ("片段自检", [PY, str(TOOLS / "frag.py"), "lint"]),
        ("检索索引", [PY, str(TOOLS / "memdb.py"), "index"]),
        ("目录总览", [PY, str(TOOLS / "catalog.py")]),
        ("自省扫描", [PY, str(TOOLS / "persona.py"), "review", "--hours", "24"]),
        ("风格记录", [PY, str(TOOLS / "style.py"), "log"]),
    ]
    check_cmd = checker("memory_check.py", "memory-check.ps1")
    fresh_cmd = checker("check_freshness.py", "check-freshness.ps1")
    if check_cmd:
        steps.append(("体检", check_cmd))
    if fresh_cmd:
        steps.append(("同步校验", fresh_cmd))

    if dry:
        print("日结算将执行：")
        for name, cmd in steps:
            print(f"  - {name}: {' '.join(Path(x).name if i == 0 else x for i, x in enumerate(cmd))}")
        print("  - 推送: memgit.py autosync")
        return 0

    results: dict[str, tuple[int, str]] = {}

    for name, cmd in steps:
        code, out = run(cmd)
        results[name] = (code, out)
        flag = "ok" if code == 0 else "跳过"
        print(f"[{flag}] {name}: {tail(out) or '(无输出)'}")

    # ---- 汇总成一行结论 ----
    mods = parse(r"模块 (\d+) 个", results.get("体检", (0, ""))[1])
    hard = "0" if "❌" not in results.get("体检", (0, ""))[1] else "有"
    soft = len(re.findall(r"^  - ", results.get("体检", (0, ""))[1], re.M))
    frags = parse(r"片段 (\d+) 个", results.get("片段自检", (0, ""))[1])
    frag_bad = parse(r"问题 (\d+) 条", results.get("片段自检", (0, ""))[1])
    idx = parse(r"索引完成：(\d+) 个文件", results.get("检索索引", (0, ""))[1])
    fresh = "✅" if "全部同步" in results.get("同步校验", (1, ""))[1] else "⚠️"
    day = parse(r"共处理 (\d+) 天", results.get("日记归档", (0, ""))[1])
    persona = results.get("自省扫描", (0, ""))[1]
    p_say = parse(r"她 (\d+) 句", persona)
    p_fix = parse(r"一、她指出问题的地方（(\d+) 条", persona)
    p_soft = parse(r"二、我一开口就软/认错的句式（(\d+) 条", persona)

    result = (
        f"硬错误 {hard} / 软提醒 {soft} 条；模块 {mods}；片段 {frags}（问题 {frag_bad}）；"
        f"索引 {idx} 文件；同步 {fresh}；日记归档 {day} 天"
    )
    found = f"她 {p_say} 句 / 纠正 {p_fix} / 软开场 {p_soft}（自省 24h）"
    action = "全部自动完成，无需她提醒"

    print("\n=== 日结算结论 ===")
    print(result)
    print(found)

    write_inspect(
        {
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "result": result,
            "found": found,
            "action": action,
        }
    )
    print("[ok] 巡检记录已更新")

    code, out = run([PY, str(TOOLS / "memgit.py"), "autosync"])
    print(f"[{'ok' if code == 0 else '跳过'}] 推送: {tail(out, 2) or '(无输出)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
