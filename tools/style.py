#!/usr/bin/env python3
"""风格轮换：每天从 `81-风格轮换（每日一换）` 里确定性抽一种风格。

用法：
    python tools/style.py today      # 今天的风格 + 规矩（确定，同一天不变）
    python tools/style.py log        # 把今天的风格记进 81 的记录表
    python tools/style.py list       # 列出所有风格
    python tools/style.py set 名     # 今天强制换成某个风格（写 .style_override）
    python tools/style.py clear      # 撤掉强制覆盖
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "references" / "81-风格轮换（每日一换）.md"
OVERRIDE = Path(__file__).resolve().parent / ".style_override.json"


def read_md():
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return MD.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise SystemExit("读不了 81 模块，检查编码")


def parse_styles(text):
    m3 = re.search(r"(?m)^## 3\.", text)
    m4 = re.search(r"(?m)^## 4\.", text[m3.end():])
    block = text[m3.end(): m3.end() + m4.start()] if m4 else text[m3.end():]
    items = []
    for m in re.finditer(r"^### \d+\.\s*(.+)$", block, re.M):
        name = m.group(1).strip()
        body_start = m.end()
        nxt = block.find("\n### ", body_start)
        body = block[body_start: nxt if nxt != -1 else len(block)].strip()
        items.append((name, body))
    return items


def logged_rows():
    text = read_md()
    return re.findall(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]+?)\s*\|", text, re.M)


def pick(styles):
    today = date.today().isoformat()
    names = [s[0] for s in styles]
    # 今天已经记过 → 以记录为准，保证同一整天稳定
    for d, n in logged_rows():
        if d == today and n in names:
            return n, "记录"
    if OVERRIDE.exists():
        try:
            ov = json.loads(OVERRIDE.read_text(encoding="utf-8"))
            if ov.get("date") == today and ov.get("name") in names:
                return ov["name"], "强制"
        except Exception:
            pass
    idx = (int(today.replace("-", "")) * 2654435761) % len(styles)
    # 避免和上一天撞车
    prev = [n for d, n in logged_rows() if d < today]
    last = prev[-1] if prev else None
    for _ in range(len(styles)):
        if styles[idx][0] != last:
            break
        idx = (idx + 1) % len(styles)
    return styles[idx][0], "随机"



def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    styles = parse_styles(read_md())
    cmd = sys.argv[1] if len(sys.argv) > 1 else "today"
    if not styles:
        raise SystemExit("没解析到风格，检查 81 的 §3 格式")

    if cmd == "list":
        for i, (name, _) in enumerate(styles, 1):
            print(f"{i:>2}. {name}")
        print(f"\n共 {len(styles)} 种。")
    elif cmd == "today":
        name, src = pick(styles)
        body = dict(styles)[name]
        print(f"今日风格（{src}）：{name}")
        print(" " + "-" * 24)
        print(body)
    elif cmd == "log":
        name, src = pick(styles)
        today = date.today().isoformat()
        text = read_md()
        if f"| {today} |" in text:
            print(f"今天已记过：{today}（{name}）")
            return
        row = f"| {today} | {name} | {src} |\n"
        with MD.open("a", encoding="utf-8") as f:
            f.write(row)
        print(f"已记录：{row.strip()}")
    elif cmd == "set":
        if len(sys.argv) < 3:
            raise SystemExit("用法：style.py set 风格名（可写部分关键词）")
        key = sys.argv[2]
        hit = [s[0] for s in styles if key in s[0]]
        if not hit:
            raise SystemExit(f"没找到含「{key}」的风格：" + "、".join(s[0] for s in styles))
        OVERRIDE.write_text(json.dumps({"date": date.today().isoformat(), "name": hit[0]}, ensure_ascii=False), encoding="utf-8")
        print(f"今天强制：{hit[0]}")
    elif cmd == "clear":
        if OVERRIDE.exists():
            OVERRIDE.unlink()
        print("已清掉强制覆盖")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
