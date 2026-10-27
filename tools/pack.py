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
import hashlib
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

# 2026-10-27 阶段3补（Lead 裁定）：纯数字 QQ 号 / 数字开头的邮箱是隐私 id，不是「名字」，
# 统一抹成 <邮箱已移除>；普通邮箱仍走 <你的邮箱>。两条数字规则放前面，否则会被通用规则先吃掉。
EMAIL_NUM_QQ_RE = re.compile(r"(?i)\d{5,}@qq\.com")
EMAIL_NUM_RE = re.compile(r"(?i)\d{5,}@[A-Za-z0-9.-]+")
EMAIL_ANY_RE = re.compile(r"(?i)[\w.%+-]+@[\w-]+\.[A-Za-z]{2,}")


def sanitize_emails(text: str) -> str:
    text = EMAIL_NUM_QQ_RE.sub("<邮箱已移除>", text)
    text = EMAIL_NUM_RE.sub("<邮箱已移除>", text)
    return EMAIL_ANY_RE.sub("<你的邮箱>", text)


# 2026-10-27 阶段3补（Lead 裁定）：脱敏残渣清理。
# 真实成因（她抓的）：QQ 号在替换表里被拆成两半——前段变 %USERNAME%，后段 <邮箱已移除>
# 再被邮箱规则抹掉，拼出 `<你的署名> <你的邮箱>` 这种半截串。
# 残渣比裸号更糟：它既泄露「这里有真号」的形状，又让人看不懂。这里折回干净占位。
RESIDUE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"<你的名字或 ID>\s*<?%USERNAME%?\s*<邮箱已移除>>?"), "<你的署名> <你的邮箱>"),
    (re.compile(r"<你的名字或 ID>\s*<邮箱已移除>"), "<你的署名> <你的邮箱>"),
    (re.compile(r"<你的邮箱>"), "<你的邮箱>"),
    (re.compile(r"<你的邮箱>|<你的邮箱>"), "<你的邮箱>"),
]


def clean_residue(text: str) -> str:
    for rx, rep in RESIDUE_RULES:
        text = rx.sub(rep, text)
    return text


def selftest_sanitize() -> int:
    """sanitize 的邮箱 + 残渣规则自测：确认数字 id 命中、残渣被折平、普通邮箱不被误判成移除。"""
    cases = [
        ("联系 <邮箱已移除> 谢谢", "<邮箱已移除>", "<你的邮箱>"),
        ("owner <邮箱已移除>", "<邮箱已移除>", "<你的邮箱>"),
        ("备用 <邮箱已移除>", "<邮箱已移除>", "<你的邮箱>"),
        ("模板 <你的邮箱>", "<你的邮箱>", "<邮箱已移除>"),
        ("普通 <你的邮箱>", "<你的邮箱>", "<邮箱已移除>"),
    ]
    bad: list[str] = []
    for src, want, banned in cases:
        got = sanitize_emails(src)
        if want not in got:
            bad.append(f"{src!r} -> {got!r}（缺 {want}）")
        elif banned and banned in got:
            bad.append(f"{src!r} -> {got!r}（混进 {banned}）")
    residue_cases = [
        ("发行署 <你的署名> <你的邮箱>", "发行署 <你的署名> <你的邮箱>"),
        ("<你的署名> <你的邮箱>", "<你的署名> <你的邮箱>"),
    ]
    for src, want in residue_cases:
        got = clean_residue(sanitize_emails(src))
        if got != want:
            bad.append(f"残渣 {src!r} -> {got!r}（应为 {want!r}）")
    if bad:
        print("❌ sanitize 邮箱自测失败：")
        for b in bad:
            print(f"   {b}")
        return 1
    print(f"✅ sanitize 邮箱自测 {len(cases)}/{len(cases)} 通过"
          f"（数字 id → <邮箱已移除>）；残渣 {len(residue_cases)}/{len(residue_cases)} 通过")
    return 0


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
    # 占位优先用模块自己的通用名（`301-项目.md` → 项目），这样空槽还能被「项目」这类词命中；
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
    note = "\n> 这个模块是空的：按自己的情况往里加条目，一条只记一个事实，格式见 `926-模块标题.md`。\n"
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
    ap.add_argument("--selftest", action="store_true",
                    help="只跑 sanitize 的脱敏自测，不打包")
    args = ap.parse_args()

    if args.selftest:
        return selftest_sanitize()

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
        # v 允许是空串（「喵」→删掉、「Aurora\Shizuku\」→删掉）：原来写 `if k and v` 会把空串目标整条丢掉，
        # 结果包里留着该抹掉的词（2026-10-20 抓到，扫描命中 `喵`）
        if k and v is not None:
            mapping.append((str(k), str(v)))
    mapping.sort(key=lambda kv: len(kv[0]), reverse=True)

    # 任何盘符 + Users + 用户名，统一抹成占位符（2026-10-20 加）：光靠 scrub 表按名字替换会漏掉
    # 另一台机器的用户名（本机与台式机器名不同，写进文档就带出去了），换机一次就漏一次。
    WIN_HOME_RE = re.compile(r"(?i)\b[A-Z]:[\\/]+Users[\\/]+[^\\/\s`'\"]+")

    # 公开骨架库地址白名单：这些不算隐私，且是框架用户 update.py 的唯一入口，
    # 被 scrub 洗掉就等于热更新直接坏掉。owner/repo 从 update.source 推，不在这里硬编码名字
    # ——硬编码的话这份 pack.py 自己也会被洗成占位符，别人拿去重打包就带错了。
    KEEP_URLS: list[str] = []
    _base = str((cfg.get("update") or {}).get("source") or "").rstrip("/")
    _m = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/", _base)
    if _m:
        _owner, _repo = _m.group(1), _m.group(2)
        KEEP_URLS = [
            f"https://raw.githubusercontent.com/{_owner}/{_repo}",
            f"https://github.com/{_owner}/{_repo}",
            f"https://codeload.github.com/{_owner}/{_repo}",
            f"<你的邮箱>:{_owner}/{_repo}.git",
        ]

    def sanitize(text: str) -> str:
        # 公开骨架库地址不是隐私（骨架仓库本来就是公开的），
        # 但 scrub 表会把 URL 里的账号名一起洗掉（人名词条是全局替换），
        # 结果包里的 update.source / manifest.repo 全变占位符，urllib 直接抛 InvalidURL。
        # 所以先把这几条 URL 抠出来，洗完再塞回去（2026-10-24 实测踩到）。
        shields: dict[str, str] = {}
        for i, url in enumerate(KEEP_URLS):
            if url in text:
                token = f"\x00KEEPURL{i}\x00"
                shields[token] = url
                text = text.replace(url, token)
        for real, ph in mapping:
            if real:
                text = text.replace(real, ph)
        # json/yml 里路径是转义写的（%USERPROFILE%
        WIN_HOME_ESC_RE = re.compile(r"(?i)[A-Z]:[\\\\/]+Users[\\\\/]+[^\\\\/\s\"']+")
        text = WIN_HOME_ESC_RE.sub("%USERPROFILE%", text)
        text = WIN_HOME_RE.sub("%USERPROFILE%", text)
        # 2026-10-27 阶段 3c：用户名被换成占位符、邮箱尾巴（<你的邮箱>）却还在，
        # 906 这类会进包的骨架文档实测漏过一次；包内一律把真邮箱抹成占位符。
        # 2026-10-27 阶段3补：数字 id 邮箱（<邮箱已移除>）单列成 <邮箱已移除>。
        text = sanitize_emails(text)
        text = clean_residue(text)
        for token, url in shields.items():
            text = text.replace(token, url)
        return text

    # 2026-10-24：四值断言必须先跑、并且在动 dist 之前跑。
    # 原来的位置在复制完、开始处理 references 时，那时 dist/<版本> 已经被 rmtree 重建过了——
    # 断言一炸就留下一个半成品包（真的发生过：red test 塞了个 distribute: nuke 的探针，
    # 结果 dist/user-memory-0.3.1-20261024 被清空、只剩半个 references/）。
    ALLOWED = ("keep", "blank", "reset", "drop")
    src_refs = root / "references"
    bad: list[str] = []
    for f in sorted(src_refs.glob("*.md")):
        if f.name in ("927-记忆库总览.md", "926-模块标题.md"):
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
    # 2026-10-26：表情包图库（meme-packs/ 里的 441 张图 + index.db）是本地资源，
    # 进发行包体积翻十倍；换机靠记忆库 git 同步 + tools/meme_pack_deploy.ps1 建链接。
    ex_dirs.add("meme-packs")
    # 2026-10-27 阶段 3c：内容模块（000–899）住 memory/，那是用户自己的记忆，整目录不进包
    # （已搬进 memory/ 的私库文件不带 drop 标记，靠 frontmatter 拦不住，只能按目录排除；
    #   包内 memory/ 只放一份公开语气模板，见下面 813 那段）。
    ex_dirs.add("memory")
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
        # 2026-10-26：private/ 是依赖本机环境/私人图库的脚本，公开包永不包含
        if rel.parts[:2] == ("tools", "private"):
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
        if file.name in ("927-记忆库总览.md", "926-模块标题.md"):
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
            # 没编号的协议件（_巡检记录 / _待确认 / _模板）拿文件名当提示词，
            # 不然 keywords 会被填成「待填」，等于这个词废了（2026-10-24 发现）。
            hint = file.stem.split("-", 1)[1] if "-" in file.stem else file.stem.lstrip("_")
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

    # 2026-10-24（她指出）：包里发的 SKILL.md 一直是本机那份（name: user-memory、
    # 通篇第一人称），通用版只躺在 framework-public/ 里没被用上——等于每次发版都把私人入口发出去。
    public_skill = root / "framework-public" / "public-SKILL.md"
    if public_skill.exists():
        write(target / "SKILL.md", sanitize(public_skill.read_text(encoding="utf-8")))

    # 门面文件里的作者署名与项目名不该被洗成占位符（2026-10-24 她指出：
    # README 标题一度变成「<记忆机器人> Memory」，作者行变成「by Firin Chan & <你的名字或 ID>」）。
    owner_m = re.match(
        r"https://raw\.githubusercontent\.com/([^/]+)/",
        str((cfg.get("update") or {}).get("source") or ""),
    )
    owner = owner_m.group(1) if owner_m else ""

    for rel in ("SKILL.md", "README.md", "LICENSE", "setup.ps1", "templates/AGENTS.md",
                "references/926-模块标题.md", "config.example.json"):
        p = target / rel
        if p.exists():
            body = sanitize(read(p))
            if rel in ("README.md", "SKILL.md") and owner:
                body = body.replace("<你的名字或 ID>", owner)
            write(p, body)

    # 2026-10-24（她指出）：发出去的包缺「开局必读的语气模块」——
    # 那是别人装这个库的根本原因（要一个像人的说话方式），却是 drop 的。
    pub89 = root / "framework-public" / "public-813-语气与交流.md"
    if pub89.exists():
        # 源库的语气模块是私人件（在 memory/，整目录不进包），包内换成模板版。
        # 2026-10-27 阶段 3c：内容模块改住 memory/，模板也跟着搬，不然包里的
        # public-AGENTS.md 指着一个不存在的 references/813 路径。
        mem_target = target / "memory"
        mem_target.mkdir(parents=True, exist_ok=True)
        write(mem_target / "813-语气与交流.md", sanitize(pub89.read_text(encoding="utf-8")))
        print("  语气模块：包里放模板版 memory/813-语气与交流.md（开局必读）")

    # 2026-10-26 P1-C（她定的）：references/ 改成公开白名单，白名单外默认不进 dist。
    # 2026-10-27 阶段 3c：内容模块（000–899）已全搬进 memory/，这里只剩 9xx 系统区
    # 与骨架节点目录，白名单继续只放 90–99 系统区 + 926/927/928 三份索引。
    # 私人模块（08a、12a/12b、21a/21b、变更日志、_待确认、_巡检记录、
    # _合并候选、_复核清单、_表情包清单…）一律在包内删掉，不靠 frontmatter 黑名单。
    PUBLIC_REF_FIXED = {"927-记忆库总览.md", "926-模块标题.md", "928-路由详表.md"}
    # 2026-10-26 她定的：918-架构大纲 是内部总图（待办、决策、提交号太多），不进公开包。
    PUBLIC_REF_EXCLUDE = {"918-架构大纲.md"}
    if ref_target.is_dir():
        removed_refs = []
        for f in sorted(ref_target.glob("*.md")):
            # 2026-10-27 批 M：白名单剔除的文件已经从 `stat` 里记过一次，这里要按它
            # 原本的 distribute 值把计数退回来、改记到 drop，否则 PACKINFO 会写出
            # 「保留 keep 13」而包里实际只有 12 个（真发生过：918 / 929）。
            mode = front_field(read(f), "distribute", default_mode(f.stem))
            if f.name in PUBLIC_REF_EXCLUDE:
                if mode in stat:
                    stat[mode] -= 1
                stat["drop"] += 1
                f.unlink()
                removed_refs.append(f.name)
                continue
            # 2026-10-27 阶段 3c：编号三位化后这条 2 位正则全落空，9xx 骨架文档被整批
            # 踢出包（dist references 只剩节点目录）。改成兼容 2/3 位。
            if f.name in PUBLIC_REF_FIXED or re.match(r"^9\d{1,2}[a-z]?-", f.name):
                continue
            if mode in stat:
                stat[mode] -= 1
            stat["drop"] += 1
            f.unlink()
            removed_refs.append(f.name)
        if removed_refs:
            print(f"   references 白名单外剔除 {len(removed_refs)} 个（示例：{', '.join(removed_refs[:3])}）")

        # 2026-10-27 批 M：PACKINFO 的计数按实际产物核一遍——白名单剔除之后
        # 包里真留下的 references/*.md 必须正好等于 keep + blank + reset
        # （`926`/`927` 两份索引不参与计数，跟上面统计口径保持一致）。
        counted = [f for f in ref_target.glob("*.md") if f.name not in ("926-模块标题.md", "927-记忆库总览.md")]
        if len(counted) != stat["keep"] + stat["blank"] + stat["reset"]:
            raise SystemExit(
                f"❌ references 计数与实际产物不符：包里 {len(counted)} 个，"
                f"keep {stat['keep']} + blank {stat['blank']} + reset {stat['reset']}"
            )

    # templates/AGENTS.md 是本机的规则版（硬塞层 + 个人规则），给别人必须换成空骨架版
    pub = root / "framework-public" / "public-AGENTS.md"
    if pub.exists():
        body = sanitize(pub.read_text(encoding="utf-8"))
        if (target / "templates" / "AGENTS.md").exists():
            write(target / "templates" / "AGENTS.md", body)
        # 2026-10-24（她定的）：根上再放一份，解压就看得见、直接复制到工作区当入口。
        # 以前只在 templates/ 里有一份，用户根本不知道要复制哪个文件。
        write(target / "AGENTS.md", body)

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

    # 2026-10-24：927-记忆库总览.md 是从主库复制过来的，里面列着 55 个模块（01 基本背景、10 健康、
    # 12a 英语…全是私人内容）。进包必须按包里真实剩下的模块重建，只留 90–99 + 协议件。
    cat = ref_target / "927-记忆库总览.md"
    if cat.exists():
        rows = []
        for f in sorted(ref_target.glob("*.md")):
            if f.name in ("927-记忆库总览.md", "926-模块标题.md"):
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

    # 928-路由详表.md：体检靠它核对模块是否登记（同 framework 侧，见那里的注释）。
    detail = ["# 路由详表", "",
              "> 由 `tools/pack.py` 按包内模块生成，别手改。", "",
              "| 模块 | 标题 |", "| --- | --- |"]
    skip = {"927-记忆库总览.md", "926-模块标题.md", "928-路由详表.md", "923-巡检记录.md", "925-复核清单.md", "924-合并候选清单.md"}
    for f in sorted(ref_target.glob("*.md")):
        if f.name in skip:
            continue
        stem = f.stem
        if not (re.match(r"^\d{2}[a-z]?-", f.name) or stem in ("922", "921")):
            continue
        txt = read(f)
        title = front_field(txt, "title", stem).strip('"') or stem
        detail.append(f"| `{stem}` | {title} |")
    write(ref_target / "928-路由详表.md", "\n".join(detail) + "\n")

    # tools/recall_eval.py：评测集必须跟着包内模块走（2026-10-24 新设备演练）。
    # 主库的题引用 78/27/22/24… 这些模块分发包里根本不存在，新用户跑出来的
    # 百分比天生是假的。这里把「期望模块在包内一个都不存在」的题删掉，让每套库
    # 只评自己有的东西；recall_eval 里「SKIP 非 0 退 1」那条判据仍然留着当兜底。
    ev = target / "tools" / "recall_eval.py"
    if ev.exists():
        txt = read(ev)
        have: set[str] = set()
        for f in ref_target.glob("*.md"):
            mm = re.match(r"^(\d{2}[a-z]?)-", f.name)
            if mm:
                have.add(mm.group(1))
        head_mark = "CASES: list[tuple[str, set[str]]] = ["
        if have and head_mark in txt:
            before, rest = txt.split(head_mark, 1)
            body, tail = rest.split("\n]", 1)
            kept: list[str] = []
            n_cut = 0
            for line in body.split("\n"):
                mrow = re.match(r'^\s*\("(.+?)",\s*\{([^}]*)\}\),\s*(?:#.*)?$', line)
                if mrow:
                    mods = {x.strip().strip('"').strip("'")
                            for x in mrow.group(2).split(",") if x.strip()}
                    if mods & have:
                        kept.append(line)
                    else:
                        n_cut += 1
                else:
                    kept.append(line)          # 注释行原样保留
            txt = before + head_mark + "\n".join(kept) + "\n]" + tail
            write(ev, txt)
            kept_n = sum(1 for ln in kept if ln.strip().startswith("("))
            print(f"   recall_eval 评测集按包内模块裁剪：留 {kept_n} 条 · 删 {n_cut} 条")
    print(f"   _路由详表 已按包内模块重建：{len(detail) - 5} 个")

    # 收尾：把 relates_to 里指向「已被 drop 的模块」的编号摘掉。
    # 不摘的话，收包人看到的是一串指向不存在模块的引用（2026-10-20 第二遍排查发现的）。
    present_ids = set()
    for f in ref_target.glob("*.md"):
        m = re.match(r"^(\d+[a-z]?)-", f.name)
        if m:
            present_ids.add(m.group(1))
        present_ids.add(f.stem.split("-")[0])
    present_ids.update({"SKILL", "927"})
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

    # 2026-10-24：包根 AGENTS.md 的 ROUTES 块原来是空的（「跑 routes.py 之后这里会出现」），
    # 用户解压后第一眼读到的就是那份空表，得先跑一次才有人手填。这里打包时直接填好。
    routes_py = target / "tools" / "routes.py"
    if routes_py.exists():
        import subprocess
        try:
            r = subprocess.run([sys.executable, str(routes_py)], cwd=str(target),
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode == 0:
                print("   路由表已填入包根 AGENTS.md（ROUTES 块）")
        except Exception as exc:
            print(f"   ⚠ 路由表没填上：{type(exc).__name__}: {exc}")

    # 2026-10-26 她定的：公开包不能带她的原话。dist 里所有 .md/.py/.json/.txt
    # 按行把她的原话整段换成中性说明，源库正文不动。
    # 2026-10-27 批 D：老正则只认「原话」与「「」紧贴，`她要求（原话已移除）`、`她要求（原话已移除）`、
    # `她要求（原话已移除）` 全漏网（验收实测 913:25 / 913:112 / 917:107 三处）。现在 `她` 与引号之间
    # 允许 的 / 原话 / 说 / 括号 / 冒号 / 空白，引号支持 「」 与 『』，仍不跨行、内容 1–200 字。
    # 2026-10-27 批 E：终验又抓到 `她要求（原话已移除）`（recall.py:155），补进字符类；广扫
    # （正则 `她[^。\n]{0,8}(原话|说|要求)[^。\n]{0,6}[「『]…`）还扫出三处真引文：
    # `她要求（原话已移除）`、`她要求（原话已移除）`、`她要求（原话已移除）`、`她要求（原话已移除）`，中间词一并收进来；
    # 同一句里连着甩两个引号（903:108）也要一起吃，否则后半个还留在包里。
    # 刻意不收代码标签与文案（工具里描述脱敏行为、或列举用词对照的说明性引号）。
    # 2026-10-27 批 F：终验二又抓到 `她要求（原话已移除）`（903:77）——归属词与冒号之间
    # 夹了一段括号注释。现在在分隔符类之前允许一个可选括号注释（日期/补充说明，≤24 字）。
    # 2026-10-27 批 G：终验三证明只补「原话|说|要求」不够——`tools/memwrite.py:386` 那句的
    # 归属词是「定的」，照样漏。归属词集合扩成
    # `原话|说|说的|要求|定的|讲|讲过的`，括号注释段保留。
    quote_re = re.compile(
        r"她(?:的)?(?:亲口|凭主观感受)?(?:原话|说的|说|要求|定的|讲过的|讲)(?:的)?"
        r"(?:[（(][^）)\r\n]{0,24}[）)])?"
        r"[）)】\]:：\s是]*"
        r"[「『][^」』\r\n]{1,200}[」』](?:[、，,；;]?[「『][^」』\r\n]{1,200}[」』])*"
    )
    # 2026-10-27 批 G：短句兜底。这三句是她原话的碎片，源库正文已改成中性表述；
    # 这里再兜一层，谁写回去也会在包里被抹掉。
    # 注：pack.py 里原本没有 EXTRA_SCRUB（那是 framework.py 的名字替换表，语义不同），
    # 所以这里新建一张纯串表，跟着同一次脱敏跑。
    # 2026-10-27 批 H：这张表会把自己抹了——终验四在 dist 的 tools/pack.py 里看到三条映射
    # 的键都被换成了右边的值，映射变成自己到自己，兜底表等于失效。这张表在 pack.py
    # 自身里是「定义」不是「引用」，所以纯串替换跳过 pack.py 这一个文件（正则脱敏照跑）。
    plain_scrub = {
        "删是软删": "本库没有容量压力",
        "纯文本就算聊一年都没多少数据": "本库没有容量压力",
        "删等于丢": "本库没有容量压力",
    }
    SELF_REL = "tools/pack.py"
    quote_total = 0
    for qp in sorted(target.rglob("*")):
        if not qp.is_file() or qp.suffix.lower() not in (".md", ".py", ".json", ".txt"):
            continue
        raw_q = qp.read_text(encoding="utf-8", errors="replace")
        new_q, n_q = quote_re.subn("她要求（原话已移除）", raw_q)
        if qp.relative_to(target).as_posix() != SELF_REL:
            for old_s, new_s in plain_scrub.items():
                hits_s = new_q.count(old_s)
                if hits_s:
                    new_q = new_q.replace(old_s, new_s)
                    n_q += hits_s
        if n_q:
            write(qp, new_q)
            quote_total += n_q
            print(f"   原话脱敏 {qp.relative_to(target).as_posix()}:{n_q}")
    if quote_total == 0:
        print("   原话脱敏：0 处（未发现她的原话引用）")
    else:
        print(f"   原话脱敏合计：{quote_total} 处")

    write(target / "VERSION", ver + "\n")
    manifest = "\n".join([
        f"打包版本 v{ver}",
        f"生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"保留 keep {stat['keep']} / 清空 blank {stat['blank']} / 重置 reset {stat['reset']} / 不发 drop {stat['drop']}",
        "不发：" + "、".join(sorted(set(d.split("-")[0] for d in dropped))),
    ] + ([persona_note] if persona_note else []))
    write(target / "PACKINFO.txt", manifest)

    # 2026-10-24（她的要求）：给分发包写一份骨架校验清单。
    # 收包人没法一眼看出骨架被动没动过——清单让骨架能自证，而 skeleton_guard.py
    # 只读不写，谁改完也不能「跑一次自动盖章」。要重新盖章只有重新打包这一条路。
    # 2026-10-24（她抓到）：前面几步（catalog / routes / memory_check）会在包内 tools/
    # 下留下 __pycache__/*.pyc，那种字节码里带着本机路径与名字，跟着包发出去就是泄漏。
    # 必须在清点骨架之前删——否则清单会记下一个随即被删掉的 pyc，收包人一跑 guard 就报「不见了」。
    for cache in list(target.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)
    for pyc in list(target.rglob("*.pyc")):
        pyc.unlink(missing_ok=True)

    skeleton_items: list[str] = []
    for d in ("tools", "bin", "templates", "framework-public"):
        base = target / d
        if base.exists():
            skeleton_items += [p.relative_to(target).as_posix()
                               for p in sorted(base.rglob("*")) if p.is_file()]
    for name in ("AGENTS.md", "SKILL.md", "setup.ps1", "VERSION", "config.example.json"):
        if (target / name).exists():
            skeleton_items.append(name)
    for p in sorted((target / "references").glob("9*.md")):
        skeleton_items.append(p.relative_to(target).as_posix())
    skeleton_items = sorted(set(skeleton_items))
    sha_lines: list[str] = [
        "# 骨架清单：每行的 sha256 对得上才算没被动过。校验用 python tools/skeleton_guard.py",
        "# 你的记忆模块（references/ 里除 9x 以外的文件）不在清单里，随便改。",
    ]
    for rel in skeleton_items:
        sha_lines.append(f"{hashlib.sha256((target / rel).read_bytes()).hexdigest()}  {rel}")
    write(target / "SKELETON.sha256", "\n".join(sha_lines) + "\n")
    print(f"   骨架校验清单 SKELETON.sha256：{len(skeleton_items)} 项")

    # 2026-10-27 阶段3补（Lead 裁定）：公开包 memory/ 只允许放 framework-public 白名单里的
    # 「distribute: blank 语气模板」——新用户装完要能直接填，但不能夹带私库正文。
    # 这条是硬断言：违反就 exit 非 0，连 zip 都不打（不靠人自觉）。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import verify_pack  # noqa: E402

    mem_bad = verify_pack.dist_memory_violations(target)
    if mem_bad:
        print("❌ memory/ 断言失败（只允许 framework-public 白名单的 distribute: blank 模板）：")
        for b in mem_bad:
            print(f"   {b}")
        return 1
    print("   memory/ 断言通过：包内语气模板均来自 framework-public 白名单且 distribute: blank")

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
