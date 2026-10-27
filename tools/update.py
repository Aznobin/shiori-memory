#!/usr/bin/env python3
"""骨架更新器：把「工具链 + 模板 + 协议件」换成新版，绝不碰你自己的记忆正文。

为什么需要它：记忆库分成两层——**骨架**（`tools/`、`templates/`、`SKILL.md`、`9x-*` 那些说明书）
和**内容**（`001`～`813` 那些装着你自己东西的模块）。骨架会一直改，内容是你的。更新只换骨架。

三种用法：

    python tools/update.py check      # 问一次远端：有没有新版（结果缓存 24 小时）
    python tools/update.py apply      # 换骨架（git 仓库内走 git pull，否则按 manifest 逐文件换）
    python tools/update.py rollback   # 换坏了退回去（只有非 git 路线会留备份）

设计上的三条硬规则：
1. **只动 manifest.json 里列的文件**。manifest 之外的一个字节都不碰，`001`～`813` 永远安全。
2. **不静默覆盖你改过的东西**。git 仓库内由 `git pull` 自己报冲突；非 git 路线把冲突文件写成
   `xxx.new` 放旁边，你自己合。
3. **检测失败一律静默**。没网、超时、被墙都不算错，退出码 0，不吓人。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
CACHE = STATE / "update.json"
BACKUP_DIR = STATE / "backup"

# 骨架库地址不写死。写死就意味着每个拿到包的人都在拉我的 fork，而且名字里带
# 用户名的话打包清洗会把它变成 <你的名字或 ID>，URL 里出现尖括号直接 InvalidURL。
# 现在从 memory.config.json 的 update.source 读，没配就算「未配置更新源」，静默跳过。
_RAW_RE = re.compile(
    r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/(?:refs/heads/)?([^/]+)/?$"
)

CACHE_HOURS = 24
TIMEOUT_CHECK = 3
TIMEOUT_FETCH = 60
MIN_PYTHON = (3, 10)


def out(msg: str = "") -> None:
    print(msg, flush=True)


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return default


def local_version() -> str:
    return read_text(ROOT / "VERSION", "0.0.0")


def parse_ver(s: str) -> tuple:
    parts = []
    for piece in s.strip().split("."):
        num = ""
        for ch in piece:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def raw_base() -> str:
    """骨架库的 raw 根，例如 https://raw.githubusercontent.com/OWNER/REPO/main。"""
    try:
        data = json.loads((ROOT / "memory.config.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ""
    src = (data.get("update") or {}).get("source") or ""
    return str(src).strip().rstrip("/")


def _split_source(base: str) -> tuple:
    m = _RAW_RE.match(base or "")
    return m.groups() if m else ("", "", "")


def zip_url(base: str) -> str:
    owner, repo, branch = _split_source(base)
    if not owner:
        return ""
    return f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"


def remote_url(base: str) -> str:
    owner, repo, _branch = _split_source(base)
    return f"<你的邮箱>:{owner}/{repo}.git" if owner else ""


def fetch(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "user-memory-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def api_manifest_version(base: str, timeout: int) -> str:
    """走 GitHub contents API 读 manifest.json 里的 version；拿不到就返回空串。

    为什么要绕 API：raw.githubusercontent.com 对单文件有约 5 分钟 CDN 缓存，刚发版时读 raw
    会拿到旧版（2026-10-24 shiori-new5 实测：VERSION 已 0.3.19，raw 还报 0.3.17，加 `?cb=` 也
    绕不过）。API 不缓存，匿名额度 60 次/小时，check 只用一次。
    """
    owner, repo, branch = _split_source(base)
    if not owner:
        return ""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/manifest.json?ref={branch}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "user-memory-updater", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    raw = base64.b64decode(data.get("content") or "")
    return str(json.loads(raw).get("version") or "")


def load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(data: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- check


def check(force: bool = False, quiet: bool = False) -> dict:
    """问远端版本。返回 {local, remote, has_new, checked_at, error}。全程不抛异常。"""
    local = local_version()
    now = time.time()
    cache = load_cache()
    fresh = (
        not force
        and cache.get("checked_at")
        and now - float(cache.get("checked_at") or 0) < CACHE_HOURS * 3600
    )
    if fresh:
        remote = cache.get("remote", "")
        error = cache.get("error", "")
        src_used = cache.get("src", "")
    else:
        remote, error = "", ""
        src_used = ""
        base = raw_base()
        if not base:
            error = "未配置更新源（memory.config.json → update.source）"
        else:
            # 2026-10-24 订正：原来写「manifest 不受 raw 缓存影响」，实测是错的——发版后第一次
            # check 读到的是上一版的 manifest（shiori-new5 报的）。现在先试 GitHub API（不缓存），
            # 再退回 raw（有约 5 分钟 CDN 缓存，可能读旧），最后才用 VERSION 兜底。
            try:
                remote = api_manifest_version(base, TIMEOUT_CHECK)
                if remote:
                    src_used = "api"
            except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            if not remote:
                try:
                    remote = str(
                        json.loads(fetch(f"{base}/manifest.json", TIMEOUT_CHECK)).get("version") or ""
                    )
                    error = ""
                    src_used = "raw"
                except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
                    error = error or f"{type(exc).__name__}: {exc}"
            if not remote:
                try:
                    remote = (
                        fetch(f"{base}/VERSION", TIMEOUT_CHECK).decode("utf-8", "replace").strip()
                    )
                    error = ""
                    src_used = "VERSION"
                except (urllib.error.URLError, OSError, TimeoutError) as exc:
                    error = error or f"{type(exc).__name__}: {exc}"
        cache.update(
            {
                "checked_at": now,
                "remote": remote,
                "error": error,
                "local_at_check": local,
                "src": src_used,
            }
        )
        save_cache(cache)

    result = {
        "local": local,
        "remote": remote,
        "has_new": bool(remote) and parse_ver(remote) > parse_ver(local),
        "checked_at": cache.get("checked_at", 0),
        "error": error,
    }
    if not quiet:
        if error and not remote:
            if error.startswith("未配置"):
                out(f"检测跳过：{error}。")
                out("配好之后才会问远端，离线不影响使用。")
            else:
                out(f"检测跳过（{error[:80]}）——离线不影响使用，下次启动再问。")
        elif not remote:
            out("远端没读到版本号，这次不更新。")
        elif result["has_new"]:
            out(f"有新骨架：{local} → {remote}")
            out("换骨架：python tools/update.py apply")
        elif parse_ver(remote) == parse_ver(local):
            out(f"已是最新：{local}")
        else:
            out(f"本地 {local} 比远端 {remote} 新——骨架库还没收到这个版本，发一次：")
            out("  python tools/framework.py --build --push --push-public")
            # 2026-10-24：远程读到的版本比本地还旧，另一种可能是 CDN 缓存（shiori-old 实测：
            # 包已是 0.3.23，check 还报 0.3.22）。走 raw 时把话说清楚，别让人以为包也旧。
            if src_used != "api":
                out(f"（这次读的是 {src_used or '缓存'}，raw 有约 5 分钟 CDN 缓存；"
                    "真要确认以包内 VERSION 为准）")
    return result


def hint() -> str:
    """给 hook 用：有新版本返回一行提示，否则返回空字符串。绝不抛异常、绝不联网超过 3 秒。"""
    try:
        result = check(quiet=True)
    except Exception:  # noqa: BLE001 - 检测永远不许把主流程搞崩
        return ""
    if not result["has_new"]:
        return ""
    cache = load_cache()
    if cache.get("reminded_for") == result["remote"]:
        return ""
    cache["reminded_for"] = result["remote"]
    save_cache(cache)
    return f"骨架有新版本 {result['remote']}（本地 {result['local']}）：python tools/update.py apply"


# ---------------------------------------------------------------- apply


def in_git_repo() -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True)


def apply_via_git(dry: bool = False) -> int:
    if git("status", "--porcelain").stdout.strip():
        out("工作区不干净（有未提交的改动）。先 commit 或 stash，再更新——不然冲突会被搅在一起。")
        out("  git add -A && git commit -m \"更新前存档\"")
        return 1
    remotes = git("remote", "-v").stdout
    if "shiori-memory" not in remotes:
        out("这个仓库还没配骨架库的 remote，先加一条：")
        skeleton = remote_url(raw_base()) or "<骨架库地址>"
        out(f"  git remote add skeleton {skeleton}")
        out("（已经在骨架库里 clone 的话，remote 名一般是 origin，直接 git pull 即可）")
        return 1
    if dry:
        out("（dry）会做这些事：")
        out("  1. git pull --ff-only skeleton main")
        out("  2. frag.py build + memdb.py index 重建")
        out("一个字没动盘。去掉 --dry 再跑就是真更新。")
        return 0
    out("拉取骨架……")
    r = git("pull", "--ff-only", "skeleton", "main")
    if r.returncode != 0:
        out("拉取失败（可能你没有推送权限，或者历史分叉了）：")
        out(f"  {(r.stderr or r.stdout).strip()[:300]}")
        out("试试手动：git fetch skeleton main && git merge --allow-unrelated-histories skeleton/main")
        return 1
    out("拉取完成。")
    return rebuild()


# 框架件：骨架自己的代码、协议、模板。
# 这些**无条件覆盖**——用户本来就不该改它们；真改了也留不住，因为新版可能改了同一个函数，
# 把旧版留在原地等于让人拿旧代码跑新数据（2026-10-24 实测：13 个 tools 全被写成 .new，
# 热更新等于没生效）。
FRAMEWORK_DIRS = ("tools/", "bin/", "templates/", "framework-public/")
FRAMEWORK_FILES = ("setup.ps1", "SKILL.md", "README.md", "LICENSE", "VERSION", ".gitignore")


def is_framework_path(rel: str) -> bool:
    return rel.startswith(FRAMEWORK_DIRS) or rel in FRAMEWORK_FILES


# 用户自己长出来的结构件：升级永远不碰它们。
# 教训（2026-10-24 新设备 shiori-old 实测）：`921` 是 `distribute: reset`，
# 包里只有表头，apply 一覆盖她 24 条日志全没了；`927` 是主库目录，覆盖成 55 模块。
# 这些东西的正确归属是「用户的库」，不是「骨架」。
USER_OWNED = (
    "references/921-变更日志.md",
    "references/927-记忆库总览.md",
    "references/923-巡检记录.md",
    "references/922-待确认inbox.md",
    "references/928-路由详表.md",
    "references/924-合并候选清单.md",
    "references/926-模块标题.md",
)


def is_user_owned(rel: str) -> bool:
    return rel in USER_OWNED


def apply_via_manifest(dry: bool = False) -> int:
    zip_src = zip_url(raw_base())
    if not zip_src:
        out("没配更新源，下不了骨架包。先写 memory.config.json 的 update.source，")
        out("例如 https://raw.githubusercontent.com/OWNER/REPO/main（OWNER/REPO 换成你自己的仓库）")
        return 1
    out("下载骨架包……")
    try:
        blob = fetch(zip_src, TIMEOUT_FETCH)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        out(f"下载失败：{type(exc).__name__}: {exc}")
        return 1

    tmp = STATE / "tmp_update"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    pack = tmp / "skeleton.zip"
    pack.write_bytes(blob)
    try:
        with zipfile.ZipFile(pack) as zf:
            zf.extractall(tmp)
    except zipfile.BadZipFile as exc:
        out(f"包坏了：{exc}")
        return 1

    roots = [p for p in tmp.iterdir() if p.is_dir()]
    if len(roots) != 1:
        out("包结构不对（应该只有一个顶层目录），放弃。")
        return 1
    src = roots[0]

    manifest_path = src / "manifest.json"
    if not manifest_path.is_file():
        out("包里没有 manifest.json——这是个没带清单的旧版包，不敢乱覆盖。")
        out("老办法：把整个包解开，手动把 tools/ templates/ SKILL.md 和 9x-*.md 拷过去。")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files") or {}

    local = local_version()
    # 上一次 update 留下的清单：有它就能判断「这个文件我到底改没改」，不依赖 git
    prev_files: dict = {}
    try:
        prev = json.loads((STATE / "manifest.json").read_text(encoding="utf-8"))
        if str(prev.get("version")) == local:
            prev_files = prev.get("files") or {}
    except (OSError, ValueError):
        pass

    backup = BACKUP_DIR / f"v{local}"
    changed, same, conflicted, added = [], [], [], []
    for rel in sorted(files):
        target = ROOT / rel
        source = src / rel
        if not source.is_file():
            continue
        new_bytes = source.read_bytes()
        if target.is_file():
            old_bytes = target.read_bytes()
            if old_bytes == new_bytes:
                same.append(rel)
                continue
            if not dry:
                backup.mkdir(parents=True, exist_ok=True)
                dst = backup / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(old_bytes)
            # 判断「用户改过没」：有 git 问 git；没 git 就对比上次清单里的 sha256。
            # 两样都没有（第一次更新）判断不了，那就覆盖 + 留备份，并在输出里说清楚。
            if is_user_owned(rel):
                continue  # 用户自己的结构件：升级一个字都不动
            if is_framework_path(rel):
                user_touched = False
            elif rel.startswith("references/9"):
                # 协议件（90-99）常带用户自己写进去的原话，骨架版是脱敏版，
                # 直接覆盖等于把她的原话换成「（已隐去）」——降级。写 .new 让她自己挑。
                user_touched = True
            elif in_git_repo():
                user_touched = bool(git("status", "--porcelain", "--", rel).stdout.strip())
            elif prev_files.get(rel):
                user_touched = hashlib.sha256(old_bytes).hexdigest() != prev_files[rel]
            else:
                user_touched = False
            if user_touched:
                # 先给 .new 挂一行来路说明（2026-10-24 shiori-old 定的方案）：
                # 隔天、换人、换会话，终端输出早没了，只剩这个文件还在。
                # 但说明只能放在正文之外——塞进正文会让 diff 永远多一行，合并的人每次都得判断留不留。
                protocol = rel.startswith("references/9")
                if not dry:
                    sidecar = target.with_name(target.name + ".new")
                    note = (
                        "<!-- 来源：骨架协议件（公开仓脱敏版），合完删本行 -->\n"
                        if protocol
                        else "<!-- 来源：你本地改过这份，新版放在旁边，合并时保你的 -->\n"
                    )
                    sidecar.write_bytes(note.encode("utf-8") + new_bytes)
                conflicted.append(rel)
            else:
                if not dry:
                    target.write_bytes(new_bytes)
                changed.append(rel)
        else:
            if not dry:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(new_bytes)
            added.append(rel)

    # 把这次新版清单存下来，下次更新才有基准判断「我改过没」
    if not dry:
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    out("")
    if dry:
        out("（dry）只是预演，一个字没动盘。去掉 --dry 再跑就是真更新。")
    out(f"覆盖 {len(changed)} · 新增 {len(added)} · 相同跳过 {len(same)} · 你改过（写成 .new）{len(conflicted)}")
    if conflicted:
        out("")
        out("这些文件没直接覆盖，新版放在同名 .new 旁边，自己合：")
        for rel in conflicted:
            tag = "协议件" if rel.startswith("references/9") else "你改过"
            out(f"  [{tag}] {rel} → {rel}.new")
    if changed or added:
        out("")
        out(f"旧版备份在：{backup if backup.exists() else '（无）'}")
        out("退回去：python tools/update.py rollback")
    shutil.rmtree(tmp, ignore_errors=True)
    if dry:
        return 0
    return rebuild()


def rebuild() -> int:
    for script, args in (
        ("tools/frag.py", ["build"]),
        ("tools/memdb.py", ["index"]),
        # CATALOG 是生成物，升级后不重建就会留着骨架里的主库目录（教训 2026-10-24）。
        ("tools/catalog.py", []),
    ):
        path = ROOT / script
        if not path.is_file():
            continue
        out(f"重建：{script} {' '.join(args)}")
        r = subprocess.run([sys.executable, str(path), *args], cwd=str(ROOT))
        if r.returncode != 0:
            out(f"⚠️ {script} 返回 {r.returncode}，更新本身完成了，但索引没重建成功，检查一下。")
            return 0
    out("完成。")
    return 0


def apply(via_git: bool = False, dry: bool = False) -> int:
    # reexec 那一遍必须绕过版本检查：第一遍已经把 VERSION 写成新版了，
    # 再按版本比就「已是最新」直接返回，新规则永远不会生效。
    # 2026-10-24 shiori-old 实测：两行输出都在，但第二遍什么都没做。
    if not os.environ.get("UPDATE_REEXEC"):
        local, remote = local_version(), check(quiet=True)["remote"]
        if remote and parse_ver(remote) <= parse_ver(local):
            out(f"已经是最新（本地 {local}），不用换。")
            return 0
    # 默认走「下载包 → 按 manifest 逐文件换」：骨架库是发布物，每次重建提交历史，
    # 用 git pull 会撞上历史不连续。manifest 路线对 zip 用户和 git 用户是同一套行为。
    if via_git:
        return apply_via_git(dry=dry)
    return apply_via_manifest(dry=dry)


# ---------------------------------------------------------------- rollback


def rollback() -> int:
    if not BACKUP_DIR.is_dir():
        out("没有备份可退（git 路线由 git 自己管：git reset --hard 上一版提交）。")
        return 1
    versions = sorted(
        [p for p in BACKUP_DIR.iterdir() if p.is_dir()],
        key=lambda p: parse_ver(p.name.lstrip("v")),
    )
    if not versions:
        out("备份目录是空的。")
        return 1
    latest = versions[-1]
    restored = 0
    for src in latest.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(latest)
        dst = ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        restored += 1
    out(f"已从 {latest.name} 恢复 {restored} 个文件。")
    return rebuild()


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description="骨架更新器（只换骨架，不碰记忆正文）")
    ap.add_argument("action", nargs="?", default="check", choices=["check", "apply", "rollback", "hint", "status"])
    ap.add_argument("--force", action="store_true", help="忽略 24 小时缓存，立刻问一次远端")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--via-git", action="store_true",
                    help="apply 时改用 git pull（只有骨架库历史连续时才行，默认不用）")
    ap.add_argument("--dry", action="store_true", help="apply / rollback 只预演，不动盘")
    args = ap.parse_args()

    if sys.version_info < MIN_PYTHON:
        out(f"Python 版本太低：{sys.version.split()[0]}，需要 {'.'.join(map(str, MIN_PYTHON))}+")
        return 1

    if args.action == "check":
        result = check(force=args.force, quiet=args.quiet)
        return 1 if result["has_new"] else 0
    if args.action == "hint":
        line = hint()
        if line:
            out(line)
        return 0
    if args.action == "apply":
        me = ROOT / "tools" / "update.py"
        before = hashlib.sha256(me.read_bytes()).hexdigest() if me.is_file() else ""
        rc = apply(via_git=args.via_git, dry=args.dry)
        # 升级器自己也在包里：这次 apply 跑的是盘上的旧代码，「结构件跳过、协议件 .new」
        # 这些新规则要到下一次升级才生效。所以如果 update.py 本次被换了，立刻用新版再跑一遍。
        # 不这么做的话，第一次升级永远走旧规则（2026-10-24 shiori-old 实测）。
        if rc == 0 and not args.dry and not os.environ.get("UPDATE_REEXEC"):
            after = hashlib.sha256(me.read_bytes()).hexdigest() if me.is_file() else ""
            if after and after != before:
                out("🔁 升级器自己也换了，用新版再跑一遍升级（让新规则当次生效）……")
                env = dict(os.environ, UPDATE_REEXEC="1")
                again = [sys.executable, str(me), "apply", *(["--git"] if args.via_git else [])]
                r = subprocess.run(again, cwd=str(ROOT), env=env)
                out("✓ 第二遍（新版逻辑）完成。" if r.returncode == 0 else f"⚠️ 第二遍返回 {r.returncode}，骨架已换好，规则可能还是旧的。")
        return rc
    if args.action == "rollback":
        return rollback()
    if args.action == "status":
        cache = load_cache()
        out(f"本地骨架：{local_version()}")
        out(f"远端骨架：{cache.get('remote') or '（还没查过）'}")
        checked = cache.get("checked_at")
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(checked))) if checked else "从未"
        out(f"上次检测：{when}")
        if cache.get("error"):
            out(f"上次错误：{cache['error']}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
