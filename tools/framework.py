#!/usr/bin/env python3
"""框架分支（framework）构建器：把「协议件 + 工具链 + 模板」摘成一个去个人化的 orphan 分支。

为什么要单开 orphan 分支：主库的 git 历史里躺着个人模块、日记正文、健康与情绪记录，
只要分支从 main 长出来，`git log -p` 就能把它们捞回去。orphan 分支没有 main 历史，
取用的人 `git clone -b framework` 只拿到框架本身。

用法：
    python tools/framework.py --dry     # 只打印清单、体积、隐私扫描预演，不写盘
    python tools/framework.py --build   # 建 .framework worktree → orphan 分支 framework → 提交 → 打 tag
    python tools/framework.py --build --push   # 提交后顺手推分支和 tag
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKTREE = ROOT / ".framework"
BRANCH = "framework"
# 发行版线：framework 的每次提交 --no-ff merge 进 Release，merge commit 署名她
# （2026-10-24 她定的：merge 名字写她的，改动写 Firin）。推远端时 Release 就是 skeleton 的 main。
RELEASE = "release"
# 版本号单一来源：只读仓库根的 VERSION，产物里的 VERSION 也用这个值重写。
# 以前这里硬编码过一次 "0.3.0"、VERSION 文件又是 "0.3.1"，同一次发布里两个版本号对不上。
FRAMEWORK_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
TAG = f"framework-v{FRAMEWORK_VERSION}"

KEEP_ROOT_FILES = [
    "SKILL.md", "README.md", "VERSION", "LICENSE",
    "config.example.json", "setup.ps1",
]
# 不进产物的根文件：ENTRY.md 是本机入口页（私库名、她的分类），别人拿到只需要 SKILL.md + AGENTS.md
DROP_ROOT_FILES = ["ENTRY.md"]
KEEP_DIRS = ["bin", "templates", "tools"]

# references/ 只留协议件：_模板 / _待确认 / 变更日志 / CATALOG + 系统区 90–99（含 91a、92a）
KEEP_REF_EXACT = {"_模板.md", "_待确认.md", "变更日志.md", "CATALOG.md"}
KEEP_REF_RE = re.compile(r"^9[0-9]a?-")

# tools/ 里不进框架分支的：本机状态、缓存、私有人格包
DROP_TOOL_NAMES = {"memdb.sqlite3", ".style_override.json"}
DROP_TOOL_DIRS = {"__pycache__", "persona"}

TEXT_SUFFIXES = {".md", ".py", ".json", ".ps1", ".psm1", ".cmd", ".sh",
                 ".yml", ".yaml", ".txt", ".toml", ".bat"}

# 隐私门：产物里出现任何一个都中止（先扫再提交，不许「先提交再修」）。
# 词表用拼接写：framework.py 自己也会进产物，直接写死等于把要扫的串带进包里。
GATE_TOKENS = [
    "Kri" + "ste", "Shi" + "zuku", "Au" + "rora", "Sei" + "ren",
    "每日" + "总结", "Obsi" + "dian", "猫" + "猫",
    "Nya" + "cat", "177" + "43", "Syl" + "te", "Clis" + "te",
    "希" + "露特", "Azunoshi" + "bainu", "星" + "涟",
]
GATE_RE = re.compile("|".join(GATE_TOKENS))

# 软门：账号名与公开仓库名只准出现在 LICENSE 和 README 的署名/仓库地址行
SOFT_TOKENS = ["Azno" + "bin", "Shio" + "ri"]
SOFT_RE = re.compile("|".join(SOFT_TOKENS))
SOFT_BYLINE = "Firin Chan & " + "Azno" + "bin"
SOFT_REPO = "github.com/" + "Azno" + "bin/" + "shio" + "ri-memory"
# raw.githubusercontent.com 这种域名里没有连续的 "github.com"，
# 所以放行判断要用「域名族 + 仓库名」，不能只比子串。
RE_REPO_LINE = re.compile(r"(?:github\.com|githubusercontent\.com)[:/]"
                          + "Azno" + "bin/" + "shio" + "ri-memory")


def soft_allowed(rel: str, line: str) -> bool:
    if rel == "LICENSE":
        return True
    if SOFT_BYLINE in line or SOFT_REPO in line:
        return True
    # 仓库地址行放行：https / ssh / raw 三种形态都算（update.py 的 RAW、SKELETON_REMOTE）
    if RE_REPO_LINE.search(line):
        return True
    if "Shio" + "ri Memory" in line:
        return True
    return False

WIN_HOME_RE = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s`'\"]+")

# 配置里 scrub 之外，框架分支还要抹掉的私人标识
EXTRA_SCRUB = {
    "notes": "<笔记库>",
    "home": "<家目录>",
    "daily": "日记",
    "notes-app": "笔记软件",
    "laptop": "<你的笔记本>",
    "user": "%USERNAME%",
    "user": "%USERNAME%",
    "yourname": "<你的英文名>",
    "yourname": "<你的英文名>",
    "yourname": "<你的英文名>",
    "youraccount": "<你的账号>",
    "youraccount": "<你的账号>",
    "proj": "<项目>",
    "项目": "<项目>",
    "用户": "用户",
}

CODE_SUFFIXES = {".py", ".ps1", ".psm1", ".cmd", ".sh", ".bat", ".json"}

# 代码里必须原样留着的公开标识（仓库地址、署名）。
# 为什么单独列：名字类替换会把 `b"..."` 里的账号名 换成非 ASCII 占位符，
# bytes 字面量当场 SyntaxError（0.3.2 的包里 framework.py 就是这么坏的）。
CODE_KEEP = [
    "github.com/" + "Azno" + "bin/" + "shio" + "ri-memory",
    "Azno" + "bin/" + "shio" + "ri-memory",
    "Firin Chan & " + "Azno" + "bin",
]

# 代码文件专用的私人词替换：目标串必须 ASCII 安全或只落在 str 字面量上（注释/字符串），
# 绝不产生中文占位符进 b"..."，否则 bytes 字面量当场 SyntaxError。
CODE_SCRUB = {
    "用户": "用户",
    "项目": "项目",
    "notes": "notes",
    "home": "home",
    "notes-app": "notes-app",
    "daily": "daily",
    "laptop": "laptop",
    "user": "user",
    "user": "user",
    "yourname": "yourname",
    "yourname": "yourname",
    "yourname": "yourname",
    "youraccount": "youraccount",
    "youraccount": "youraccount",
    "proj": "proj",
}


CHANGELOG_STUB = """---
id: changelog
title: 变更日志
keywords:
  - 变更日志
  - 变更
  - changelog
  - 记录
sensitive: false
always: false
updated: 2026-10-23
distribute: reset
---

# 变更日志

> 追加式记录，不删旧行，新的写在最上面。每次改动记忆模块都补一行。更早的行按 30 条一批归档到 `archive/`。
> **框架分支不携带个人变更史**：这里只留建库那一行，你自己的改动从这一行往下长。

| 日期 | 文件 | 改动 | 触发 |
| --- | --- | --- | --- |
| 2026-10-23 | 全库 | 建库（框架版 0.3.0）：协议件 + 工具链 + 模板；个人模块、日记、片段层不进分支 | 首次分发 |
"""

INBOX_STUB = """---
id: pending
title: 待确认
keywords:
  - 待确认
  - 未核实
sensitive: false
always: false
updated: 2026-10-23
distribute: keep
---

# 待确认

> 未核实的推测先写这里，核实后再搬进对应模块。一条一行，标上日期。

"""


def load_mapping() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """替换表分两段：(路径类, 名字类)。

    路径类：账号目录、家目录、笔记库路径 —— 代码文件也需要，否则绝对路径进包。
    名字类：这个人是谁（账号名、外号、项目名、口头禅）——
      只用在文档里；代码文件上会把 `b"..."` 里的账号名 换成中文占位符，
      带 bytes 字面量当场 SyntaxError，字符串字面量则变成坏 URL。
    长的先替换（`C:\\Users\\user\\Documents\\...` 必须先于 `user`）。
    """
    path_pairs: dict[str, str] = {}
    name_pairs: dict[str, str] = {}
    cfg_file = ROOT / "memory.config.json"
    if cfg_file.exists():
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        paths = cfg.get("paths") or {}
        phs = cfg.get("placeholders") or {}
        for key, real in paths.items():
            if real and phs.get(key):
                path_pairs[str(real)] = str(phs[key])
        for k, v in (cfg.get("pack", {}).get("scrub") or {}).items():
            if k:
                name_pairs[str(k)] = "" if v is None else str(v)
    name_pairs.update(EXTRA_SCRUB)
    order = lambda kv: len(kv[0])
    return (sorted(path_pairs.items(), key=order, reverse=True),
            sorted(name_pairs.items(), key=order, reverse=True))


QUOTE_RES = [
    # 原话类：她/用户/她的 原话、「接着说「…」」、她再钉「…」、日期后紧跟的引号句
    (re.compile(r"(?:她|用户)?的?原话[^「]{0,14}[「“][^」”]{0,400}[」”]"), "原话（已隐去）"),
    (re.compile(r"原话（已隐去）[「“][^」”\n]{0,400}[」”]"), "原话（已隐去）"),
    (re.compile(r"接着说[「“][^」”\n]{0,400}[」”]"), "接着说（已隐去）"),
    (re.compile(r"她再钉[^「\n]{0,8}[「“][^」”\n]{0,400}[」”]"), "她再钉（已隐去）"),
    (re.compile(r"她说[：:]?\s*[「“][^」”]{0,400}[」”]"), "她说（已隐去）"),
    (re.compile(r"（\d{4}-\d{2}-\d{2}\s*[「“][^」”\n]{0,400}[」”]）"), "（已隐去）"),
    # 私人口吻的长引号（带人称与语气词、且超过 12 字）：留结构不留原句
    (re.compile(r"[「“](?=[^」”\n]{12,})(?=[^」”\n]*(?:你|我|诶|啦|喵|呀|哦|嘛|呢|吗))[^」”\n]{0,400}[」”]"), "（已隐去）"),
]


def sanitize(text: str, mapping: tuple[list[tuple[str, str]], list[tuple[str, str]]],
             code: bool = False) -> str:
    path_pairs, name_pairs = mapping
    holders: dict[str, str] = {}
    if code:
        for i, keep in enumerate(CODE_KEEP):
            if keep in text:
                tag = f"\x00C{i}\x00"
                holders[tag] = keep
                text = text.replace(keep, tag)
        for real, ph in path_pairs:
            if real:
                text = text.replace(real, ph)
        for real, ph in sorted(CODE_SCRUB.items(), key=lambda kv: len(kv[0]), reverse=True):
            text = text.replace(real, ph)
        text = WIN_HOME_RE.sub("%USERPROFILE%", text)
        for tag, keep in holders.items():
            text = text.replace(tag, keep)
        return text
    pairs = path_pairs + name_pairs
    for real, ph in pairs:
        if real:
            text = text.replace(real, ph)
    text = WIN_HOME_RE.sub("%USERPROFILE%", text)
    # 产品名是公开的，先占位保护；其余库名是私人仓库名，换成中性说法
    text = text.replace("她本人", "用户本人").replace("她的电脑", "用户的电脑")
    protect = {"Shio" + "ri Memory": "\x00PM\x00", "shio" + "ri-memory": "\x00PR\x00"}
    for real, ph in protect.items():
        text = text.replace(real, ph)
    text = text.replace("Shio" + "ri.git", "memory.git").replace("Shio" + "ri", "记忆库")
    for real, ph in protect.items():
        text = text.replace(ph, real)
    # 系统件里夹着的私人原话（她的原句）不进框架分支：留结构，隐去内容
    for _ in range(2):
        for pat, repl in QUOTE_RES:
            text = pat.sub(repl, text)
    return text


def collect() -> tuple[list[Path], list[str]]:
    """返回 (要收进分支的文件, 被丢掉的东西说明)。"""
    keep: list[Path] = []
    dropped: list[str] = []

    for name in KEEP_ROOT_FILES:
        p = ROOT / name
        if p.is_file():
            keep.append(p)
        else:
            dropped.append(f"{name}（不存在）")

    for d in KEEP_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT)
            parts = rel.parts
            if any(part in DROP_TOOL_DIRS for part in parts):
                continue
            if p.name in DROP_TOOL_NAMES or p.suffix in (".pyc", ".sqlite3", ".zip"):
                continue
            # templates/hosts/ 是各台机器的私人 AGENTS 副本（含个人规则与原话），框架分支只留骨架
            if parts[:2] == ("templates", "hosts"):
                continue
            # templates/AGENTS.md 是她的规则版；产物根的 AGENTS.md 由 plan() 用公开版注入
            if parts[:2] == ("templates", "AGENTS.md"):
                continue
            keep.append(p)

    # 2026-10-24：references 里的模块光靠文件名白名单不够——`91a-AGENTS骨架.md`
    # 正文是她的整套骨架实例（§8 硬塞层、她的规则），只按 9x 前缀就整篇进了公开库（0.3.3 发漏）。
    # 所以再尊重一次 frontmatter 的 distribute：drop / blank 都不进骨架。
    for p in sorted((ROOT / "references").glob("*.md")):
        if p.name in KEEP_REF_EXACT or KEEP_REF_RE.match(p.name):
            dist_mode = ""
            try:
                m_dist = re.search(r"(?m)^distribute:\s*(\w+)", p.read_text(encoding="utf-8", errors="replace")[:800])
                dist_mode = (m_dist.group(1) if m_dist else "").strip()
            except OSError:
                dist_mode = ""
            if dist_mode in ("drop", "blank"):
                dropped.append(f"references/{p.name}（distribute: {dist_mode}）")
                continue
            keep.append(p)
        else:
            dropped.append(f"references/{p.name}")

    for name in ("archive", "fragments", "dist", "state", "tools/persona", "templates/hosts"):
        if (ROOT / name).exists():
            dropped.append(f"{name}/（整目录）")
    for name in DROP_ROOT_FILES:
        if (ROOT / name).exists():
            dropped.append(f"{name}（本机入口页，别人的骨架不需要）")
    for name in ("memory.config.json", ".hot.json"):
        if (ROOT / name).exists():
            dropped.append(f"{name}（本机状态）")
    return keep, dropped


def gate_scan(files: list[tuple[str, bytes]]) -> list[str]:
    hits: list[str] = []
    for rel, raw in files:
        for i, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
            if GATE_RE.search(line):
                hits.append(f"{rel}:{i}: {line.strip()[:160]}")
            elif SOFT_RE.search(line) and not soft_allowed(rel, line):
                hits.append(f"{rel}:{i}: (软门) {line.strip()[:160]}")
    return hits


def repo_url() -> str:
    """骨架库地址。写死就等于把某个人的 fork 发给所有拿到包的人，而且打包清洗会把
    用户名换成 <你的名字或 ID>，URL 里出现尖括号直接 InvalidURL / 语法错。
    现在从 memory.config.json 的 update.source 推；没配就留空，别人自己填。"""
    try:
        data = json.loads((ROOT / "memory.config.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ""
    src = str((data.get("update") or {}).get("source") or "").strip()
    m = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/", src)
    return f"https://github.com/{m.group(1)}/{m.group(2)}" if m else ""


def release_author() -> tuple[str, str]:
    """发版 merge 的署名：从 memory.config.json 的 git.releaseName / git.releaseEmail 读。

    2026-10-24 她定的「merge 名字写我的，改动写你的 Firin」。名字不能硬编码进
    framework.py——那份脚本本身会进分发包，硬编码等于把她的 git 身份发给所有人。
    """
    try:
        data = json.loads((ROOT / "memory.config.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return "Release", "release@local"
    g = data.get("git") or {}
    return (str(g.get("releaseName") or "Release"), str(g.get("releaseEmail") or "release@local"))


def plan() -> tuple[list[tuple[str, bytes]], list[str], int]:
    """在内存里生成产物（相对路径, 字节），不写盘。返回 (files, dropped, 原始体积)。"""
    mapping = load_mapping()
    keep, dropped = collect()
    out: list[tuple[str, bytes]] = []
    total = 0
    for src in keep:
        rel = src.relative_to(ROOT).as_posix()
        raw = src.read_bytes()
        total += len(raw)
        if src.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                out.append((rel, raw))
                continue
            # README 是逐字定稿的公开文案：不 scrub、不改写，直接原样带走
            if rel == "README.md":
                out.append((rel, raw))
                continue
            out.append((rel, sanitize(text, mapping, code=src.suffix.lower() in CODE_SUFFIXES).encode("utf-8")))
        else:
            out.append((rel, raw))

    # 版本号与两个协议件在产物里重写，不动主库
    out = [(rel, FRAMEWORK_VERSION.encode() + b"\n") if rel == "VERSION" else (rel, raw)
           for rel, raw in out]
    out = [(rel, CHANGELOG_STUB.encode()) if rel == "references/变更日志.md" else (rel, raw)
           for rel, raw in out]
    out = [(rel, INBOX_STUB.encode()) if rel == "references/_待确认.md" else (rel, raw)
           for rel, raw in out]
    # 框架产物的 SKILL.md 用公开版（主库那份是私人口径，不进产物）
    pub_skill = ROOT / "framework-public" / "public-SKILL.md"
    if pub_skill.is_file():
        out = [(rel, pub_skill.read_bytes()) if rel == "SKILL.md" else (rel, raw)
               for rel, raw in out]
    # 版权行不跟个人账号走；这里绝不能写自己的账号名——打包清洗会把账号换成
    # <你的名字或 ID>，它出现在 bytes 字面量里就直接 SyntaxError（0.3.2 就是这么废的）。
    out = [(rel, re.sub(rb"(?m)^Copyright \(c\) 2026 .*$",
                        b"Copyright (c) 2026 Firin Chan", raw))
           if rel == "LICENSE" else (rel, raw) for rel, raw in out]

    # 公开版 SKILL.md 里的版本号写成 {{VERSION}} 占位符，发版时统一替换 ——
    # 正文里再留一处硬编码版本，就会出现「VERSION 是 0.3.1、文档写 0.3.0」这种事。
    out = [(rel, raw.replace(b"{{VERSION}}", FRAMEWORK_VERSION.encode("utf-8")))
           if rel == "SKILL.md" else (rel, raw) for rel, raw in out]

    # 产物根的 AGENTS.md 用公开版：主库没有根 AGENTS.md（那是她会话里的本地约定），
    # templates/AGENTS.md 是她那份带个人规则的，都不能给陌生人，这里注入通用空骨架。
    if not any(rel == "AGENTS.md" for rel, _ in out):
        pub_agents = ROOT / "framework-public" / "public-AGENTS.md"
        if pub_agents.is_file():
            out.append(("AGENTS.md", pub_agents.read_bytes()))

    # _路由详表.md：体检靠它核对「每个模块都登记进路由表」。SKILL.md 里的大类表是
    # 裸编号（`90` `91`），匹配不上体检要求的 `90-名字` 形态；骨架又不含主库的小类详表，
    # 结果新用户第一次体检 20 个模块全判「未登记」（0.3.5 实测，红得跟坏了似的）。
    detail = ["# 路由详表", "",
              "> 由 `tools/framework.py` 按包内模块生成，别手改。", "",
              "| 模块 | 标题 |", "| --- | --- |"]
    for rel, raw in out:
        m = re.match(r"references/(\d{2}[a-z]?-[^/]+)\.md$", rel)
        if not m:
            continue
        txt = raw.decode("utf-8", "replace")
        tm = re.search(r"(?m)^title:\s*(.+)$", txt[:2000])
        title = (tm.group(1).strip().strip('"') if tm else m.group(1))
        detail.append(f"| `{m.group(1)}` | {title} |")
    for stem, note in (("_待确认", "收件箱，下划线开头不算模块"), ("变更日志", "改动历史")):
        if any(rel == f"references/{stem}.md" for rel, _ in out):
            detail.append(f"| `{stem}` | {note} |")
    out = [x for x in out if x[0] != "references/_路由详表.md"]
    out.append(("references/_路由详表.md", ("\n".join(detail) + "\n").encode("utf-8")))

    # manifest.json：给别人用的更新器靠它知道「哪些文件属于骨架、每个文件的 sha256 是多少」。
    # 只列上面已经定稿的文件；自己不进自己的清单（否则自引用算不出来）。
    manifest = {
        "version": FRAMEWORK_VERSION,
        "min_python": "3.10",
        "repo": repo_url(),
        "files": {rel: hashlib.sha256(raw).hexdigest() for rel, raw in out},
    }
    out.append(("manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")))
    return out, dropped, total


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd or ROOT),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def ensure_gitignore() -> None:
    p = ROOT / ".gitignore"
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    if ".framework/" not in text:
        p.write_text(text.rstrip("\n") + "\n\n# 框架分支的临时 worktree（framework.py --build 生成）\n.framework/\n",
                     encoding="utf-8")
        print("· .gitignore 已补 .framework/")


def clean_worktree() -> None:
    if WORKTREE.exists():
        git("worktree", "remove", str(WORKTREE), "--force")
    git("worktree", "prune")
    if WORKTREE.exists():
        shutil.rmtree(WORKTREE)
    # 2026-10-24 她定的：发行版走 merge 线，framework / release 两个分支都留着，
    # 不再每次 -D 重建。老 tag 会被新的 -f 覆盖，别的 tag 不动。


def build(push: bool, push_public: bool = False) -> int:
    ensure_gitignore()
    files, dropped, total = plan()
    hits = gate_scan(files)
    if hits:
        print(f"❌ 隐私门命中 {len(hits)} 处，已中止（没有写盘、没有提交）：")
        for h in hits:
            print("   ", h)
        return 2

    clean_worktree()
    has_branch = git("rev-parse", "--verify", BRANCH).returncode == 0
    if has_branch:
        r = git("worktree", "add", str(WORKTREE), BRANCH)
    else:
        r = git("worktree", "add", "--detach", str(WORKTREE))
    if r.returncode != 0:
        print("❌ git worktree add 失败：", r.stderr.strip())
        return 1
    if not has_branch:
        r = git("checkout", "--orphan", BRANCH, cwd=WORKTREE)
        if r.returncode != 0:
            print("❌ checkout --orphan 失败：", r.stderr.strip())
            return 1
    # 清空工作树（保留 .git），再按框架清单铺一遍。不再 `git rm` —— 分支上有历史时
    # 索引里只有已提交的文件，`rm -r .` 对新增/删除混着的情况会报错。
    for p in WORKTREE.iterdir():
        if p.name == ".git":
            continue
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()

    for rel, raw in files:
        dst = WORKTREE / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(raw)

    git("add", "-A", cwd=WORKTREE)
    msg = f"framework: {FRAMEWORK_VERSION}（去个人化框架分支：协议件 + 工具链 + 模板）"
    # 2026-10-24 她定的：改动提交署名 Firin，merge（发版）署名她。
    r = git("-c", "user.name=Firin", "-c", "user.email=firin@local",
            "commit", "-q", "-m", msg, cwd=WORKTREE)
    if r.returncode != 0:
        print("❌ commit 失败：", (r.stderr or r.stdout).strip())
        return 1
    sha = git("rev-parse", "--short", "HEAD", cwd=WORKTREE).stdout.strip()
    git("tag", "-f", TAG, sha)

    print(f"✅ framework 分支已建：{len(files)} 文件 / {total / 1024:.0f} KB（提交 {sha}，tag {TAG}）")

    # 发行版 merge 线：framework 收每次发版的内容（署名 Firin），再 --no-ff merge 进 release
    # （merge commit 署名她）。git 图上一眼看出哪一版是哪次发的，也不再用 force 覆盖远端。
    if git("rev-parse", "--verify", RELEASE, cwd=WORKTREE).returncode == 0:
        git("checkout", "-q", RELEASE, cwd=WORKTREE)
    else:
        git("checkout", "-q", "-b", RELEASE, cwd=WORKTREE)
    merge_name, merge_mail = release_author()
    r = git("-c", f"user.name={merge_name}", "-c", f"user.email={merge_mail}",
            "merge", "--no-ff", "-m", f"发布 {FRAMEWORK_VERSION}", BRANCH, cwd=WORKTREE)
    if r.returncode == 0:
        msha = git("rev-parse", "--short", "HEAD", cwd=WORKTREE).stdout.strip()
        print(f"✅ release 合并提交：{msha}（merge 署名 {merge_name}）")
    else:
        print("⚠️ release merge 没成功：", (r.stderr or r.stdout).strip()[:200])
    if push:
        r = git("push", "origin", f"{BRANCH}", f"{RELEASE}",
                f"refs/tags/{TAG}:refs/tags/{TAG}")
        print("push:", "ok" if r.returncode == 0 else f"offline / 被拒（{(r.stderr or r.stdout).strip()[:160]}）")
    if push_public:
        # 第一次从 orphan 换到 merge 线时远端历史对不上，允许一次 --force-with-lease；
        # 之后每次都走快进（release 只追加 merge 提交）。
        r = git("push", "skeleton", f"{RELEASE}:main", f"refs/tags/{TAG}:refs/tags/{TAG}",
                "--force-with-lease", cwd=WORKTREE)
        if r.returncode == 0:
            print("push-public: ok（别人 update.py 立刻能看到新版）")
        else:
            print(f"push-public: 失败（{(r.stderr or r.stdout).strip()[:200]}）")
            print("  提示：git remote add skeleton git@github.com:Aznobin/shiori-memory.git")
            return 1
    return 0


def dry() -> int:
    files, dropped, total = plan()
    hits = gate_scan(files)
    print(f"将收进 framework 分支：{len(files)} 个文件 / {total / 1024:.0f} KB（原文体积，未计替换）")
    by_dir: dict[str, int] = {}
    for rel, _ in files:
        by_dir[rel.split("/")[0]] = by_dir.get(rel.split("/")[0], 0) + 1
    for k, v in sorted(by_dir.items()):
        print(f"  {k:<12} {v} 文件")
    print("\nreferences 收：" + "、".join(sorted(r for r, _ in files if r.startswith("references/"))))
    print("\n丢掉：")
    for d in dropped:
        print("  -", d)
    print(f"\n隐私门预演：命中 {len(hits)} 处")
    for h in hits:
        print("   ", h)
    return 0 if not hits else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="框架分支构建器")
    ap.add_argument("--dry", action="store_true", help="只打印清单 / 体积 / 隐私预演，不写盘")
    ap.add_argument("--build", action="store_true", help="建 .framework worktree、orphan 分支、提交、打 tag")
    ap.add_argument("--push", action="store_true", help="--build 之后推分支与 tag")
    ap.add_argument("--push-public", action="store_true",
                    help="--build 之后把同一个提交推给公开骨架库（remote 名 skeleton）")
    args = ap.parse_args()
    if args.build:
        return build(args.push, args.push_public)
    return dry()


if __name__ == "__main__":
    sys.exit(main())
