#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记忆库 git 助手（user-memory 私有仓库用）

用法：
  python tools/memgit.py init                 初始化仓库（本地）
  python tools/memgit.py status               看有没有改动、领先/落后几个提交
  python tools/memgit.py check                开局核对：和 git 最新比对，落后就自动拉（一行 GIT: 结果）
  python tools/memgit.py commit ["说明"]      全部暂存并提交，说明省略就抄变更日志第一行
  python tools/memgit.py pull                 只拉不比对（`check` 的简化版，留作脚本调用）
  python tools/memgit.py autosync ["说明"]    写记忆后自动跑：提交 → 拉 → 推，失败只提示
  python tools/memgit.py sync ["说明"]        同上，但要人工看结果
  python tools/memgit.py remote <url>         绑定远端（首次用）
  python tools/memgit.py --selftest           自检提交说明兜底（临时仓库，不碰真库）
"""

import datetime
import os
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from config import git_identity  # noqa: E402
from paths import module_paths  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
GITIGNORE_HINT = "tools/memdb.sqlite3、dist/、*.zip、memory.config.json 已在 .gitignore 里"


def _git_env():
    """让网络子进程自己也会快失败（2026-10-21）

    - `GIT_TERMINAL_PROMPT=0`：没凭据就直接失败，不挂在看不见的输入提示上
    - `GIT_SSH_COMMAND`：连不上 4 s 自己断，不跟一条死连接干耗
    """
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if "GIT_SSH_COMMAND" not in env:
        env["GIT_SSH_COMMAND"] = "ssh -o ConnectTimeout=4 -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
    return env


def _kill_tree(proc):
    """超时后连子进程一起收掉（Windows 上尤其必要）

    `git fetch` 会 fork 出 `git-remote-https` / `ssh`，只 kill 父进程时孙进程
    还捏着 stdout 管道，`communicate()` 会一直等到它自己超时——2026-10-21 实测
    设了 3 s 超时，整条命令硬是拖了 21.3 s 才回来。
    """
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=5)
            return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def git(*args, check=True, timeout=None):
    cmd = ["git", *args]
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=_git_env(),
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        rc = 124
        note = f"timeout after {timeout}s"
        err = note + ("\n" + err.strip() if (err or "").strip() else "")
    # 网络操作卡死时的统一出口：伪装成一个「超时」失败，交给 git_net 换路子重试
    p = subprocess.CompletedProcess(args=cmd, returncode=rc,
                                    stdout=out or "", stderr=err or "")
    if check and p.returncode != 0:
        msg = (p.stderr or p.stdout or "").strip()
        print(f"[git {' '.join(args)}] 失败：{msg}")
        sys.exit(1)
    return p


IDENTITY = git_identity()

# 网络操作撞上代理层 TLS 坏掉时，绕开代理直连重试一次
PROXY_BYPASS = ("-c", "http.proxy=", "-c", "https.proxy=")
NET_ERR_HINTS = (
    "schannel",
    "ssl/tls",
    "unexpected eof",
    "handshake",
    "recv failure",
    "connection was reset",
    "could not connect to server",
    "openssl ssl_connect",
    "timeout after",  # 自己 git() 包装出来的超时（见 git_net 的 NET_TIMEOUT）
)


def _errtail(p, n: int = 160) -> str:
    """取 stderr/stdout 的最后一行做提示；两者都空时不能炸

    2026-10-21 实测：代理挂掉时 `git pull` 有时 rc≠0 却一个字都不输出
    （stderr/stdout 全空），原来直接 `.splitlines()[-1]` 抛
    `IndexError: list index out of range`，把「离线」伪装成脚本崩溃。
    """
    text = ((p.stderr or "") + (p.stdout or "")).strip()
    lines = text.splitlines()
    return lines[-1][:n] if lines else f"exit {p.returncode}（git 没有输出）"


def _why(p) -> str:
    """把 git 的失败原文归成一句准话（2026-10-27 批 H）。

    改这行的原因：`cmd_autosync` 原来不管什么原因都印「（离线？）」，把
    「远端有本地没有的提交 / rebase 撞冲突 / 认证没过」全糊成网络问题，
    照着提示去查网络只会白跑。分类只看 git 的输出，不猜。
    """
    low = ((p.stderr or "") + (p.stdout or "")).lower()
    if any(k in low for k in ("could not resolve host", "unable to access", "failed to connect",
                              "connection refused", "network is unreachable", "timed out",
                              "operation timed out", "proxy")):
        return "网络不通或代理没起（连不上远端）"
    if "conflict" in low or "patch failed" in low or "could not apply" in low:
        return "rebase 撞了冲突，要人工处理"
    if any(k in low for k in ("non-fast-forward", "fetch first", "rejected", "behind the remote")):
        return "远端有本地没有的提交（非快进），得先拉"
    if any(k in low for k in ("authentication", "permission denied", "publickey", "invalid username")):
        return "认证没过（密钥或权限）"
    return "原因见下"


_SSH_CACHE = None


def ssh_remote():
    """把 origin 换成 SSH 形态（https 走不通时的第三条路）

    骨架里不写死任何人的仓库地址：origin 是什么就推导什么，
    没绑远端就原样返回 "origin"，让后续 git 自己报错。
    """
    global _SSH_CACHE
    if _SSH_CACHE is not None:
        return _SSH_CACHE
    url = (git("remote", "get-url", "origin", check=False).stdout or "").strip()
    if not url:
        _SSH_CACHE = "origin"
    elif url.startswith("git@") or url.startswith("ssh://"):
        _SSH_CACHE = url
    else:
        # https://github.com/u/r.git → <你的邮箱>:u/r.git
        host, _, path = url.split("//", 1)[-1].partition("/")
        _SSH_CACHE = f"git@{host}:{path}" if host and path else url
    return _SSH_CACHE

NET_TIMEOUT = 45  # push 这类重操作的单步上限；卡死 3 分钟比失败更糟（会话被拖住）

# 拉取/核对是「开局顺手做的事」，只给几秒（2026-10-21 她定的规矩）：
# 「pull 如果失败就读取本地，没必要强行等云端，等个两三秒没反应就放弃，
#   之后重载再读就行了，所有的 pull 都是」。读旧版能干活，卡住什么都干不了。
FETCH_TIMEOUT = 3   # 单条路给 3 s（fetch 只是比对，不必等）
FETCH_BUDGET = 5    # 整轮 5 s 封顶：没反应就当离线，按本地版本读
                    # 2026-10-22 实测：3 s 太紧，`ls-remote` 单独给 8 s 一次就过，
                    # 同一分钟 `check` 却报「离线」；提到 5 s 仍守「没反应就读本地」
PULL_TIMEOUT = 2.5  # 单条路给 2.5 s
PULL_BUDGET = 5     # 整轮 5 s 封顶（真拉一个提交通常 1~2 s）


def git_net(*args, timeout=NET_TIMEOUT, budget=None):
    """带网络的操作（pull/push）：https 走不通就换三条路，全失败才算离线

    2026-10-21 实测：全局 `http.proxy` 指向 127.0.0.1:7897，CONNECT 回 200
    但 TLS 立刻 EOF（`schannel: failed to receive handshake`）；把代理关掉直连
    又是 `Recv failure: Connection was reset` / `Could not connect to server`。
    同一分钟里 `ssh -T <你的邮箱>` 正常认证、`git push` 走 SSH 一次就过。
    所以顺序是：① 按配置走 → ② 绕代理直连 → ③ 换 SSH remote。
    每步只在「像网络错误」时才往下走，别的错（冲突、权限、no upstream）立刻返回，
    免得把真错误也当成网络问题重试三遍。
    **每步都带 `NET_TIMEOUT`**：不加超时的话三条路各卡 60~100 s，`autosync`
    能把会话拖住三分多钟（实测 3m20s），提示还得倒着等失败才吐。
    **`budget` 是整轮预算**：给了就按它封顶，每条路只拿剩下的秒数，超了直接
    返回最后一次的结果——「等两三秒没反应就读本地」（2026-10-21）。
    """
    t0 = time.monotonic()

    def step(*a):
        left = timeout
        if budget is not None:
            # git() 的 timeout 传 0 等于立刻超时，最少给 1 s
            left = max(1.0, round(budget - (time.monotonic() - t0), 1))
        return git(*a, check=False, timeout=left)

    p = step(*args)
    if p.returncode == 0:
        return p
    tail = ((p.stderr or "") + (p.stdout or "")).lower()
    if not any(h in tail for h in NET_ERR_HINTS):
        return p
    if budget is not None and time.monotonic() - t0 >= budget:
        return p
    p2 = step(*PROXY_BYPASS, *args)
    if p2.returncode == 0:
        return p2
    if budget is not None and time.monotonic() - t0 >= budget:
        return p2
    # 前两条都挂在网络上，换 SSH 再试一次：把命令里的 origin 换成 SSH 地址
    if "origin" in args:
        ssh_args = [ssh_remote() if a == "origin" else a for a in args]
        p3 = step(*ssh_args)
        if p3.returncode == 0:
            # 走 SSH 之后 https 的 origin/main 引用还是旧的，会把「领先 3 个提交」
            # 一直报下去；push 成功即远端 == HEAD，pull 成功则下面 fetch 会自己更新
            if args and args[0] == "push":
                head = git("rev-parse", "HEAD", check=False).stdout.strip()
                if head:
                    git("update-ref", "refs/remotes/origin/main", head, check=False)
            return p3
    return p2


def has_repo():
    return (ROOT / ".git").exists()


def ensure_identity():
    """提交身份必须锁成 memory.config.json 里 `git` 那对（默认 memory-bot）

    全局身份常是使用者本人的 GitHub 账号，一旦仓库级配置丢了，
    几十条自动提交就会全算到本人头上、把贡献图和动态流刷满。
    丢了就当场补回来，不问他。
    """
    name, email = IDENTITY
    if not has_repo():
        return
    for key, want in (("user.name", name), ("user.email", email)):
        got = git("config", "--local", "--get", key, check=False).stdout.strip()
        if got != want:
            git("config", "--local", key, want, check=False)


def changed_files(root=None):
    """相对仓库根的文件名列表（暂存区 + 未暂存，去重）。"""
    root = root or ROOT
    if not (root / ".git").exists():
        return []
    try:
        p = subprocess.run(
            ["git", "status", "--porcelain", "-z"],
            cwd=str(root), capture_output=True, timeout=10,
        )
    except Exception:
        return []
    if p.returncode != 0:
        return []
    text = p.stdout.decode("utf-8", "replace")
    files = []
    for entry in text.split("\0"):
        if len(entry) < 4:
            continue
        name = entry[3:].strip().strip('"').replace("\\", "/")
        if " -> " in name:
            name = name.split(" -> ")[-1].strip()
        if name and name not in files:
            files.append(name)
    return files


def fallback_msg(root, files):
    """变更日志里没有对应本次改动的行时用：文件名 + 一句摘要，不复用上一条说明。"""
    names = [f.split("/")[-1] for f in files]
    shown = "、".join(names[:3]) + ("…" if len(names) > 3 else "")
    return f"记忆更新 {shown}（{len(names)} 个文件；无对应变更日志行）"


def default_msg(root=None):
    """变更日志里能对上本次改动就用它的「改动」列；对不上就用「文件名 + 一句摘要」兜底。"""
    root = root or ROOT
    files = changed_files(root)
    try:
        log_path = pathlib.Path(root) / "references" / "921.md"
        log = log_path.read_text(encoding="utf-8")
        rows = [ln for ln in log.splitlines() if ln.startswith("| 20")]
        if rows:
            cells = [c.strip() for c in rows[0].strip("|").split("|")]
            if len(cells) >= 3:
                row_files = cells[1]
                matched = any(
                    f in row_files or f.split("/")[-1] in row_files for f in files
                )
                if matched or not files:
                    body = cells[2].split("；")[0].split("。")[0].replace("`", "").replace("*", "")
                    if body:
                        return "记忆：" + (body[:38] + "…" if len(body) > 38 else body)
    except Exception:
        pass
    if files:
        return fallback_msg(root, files)
    return "记忆更新 " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def selftest():
    """临时仓库里验说明兜底：只写 `922.md`（变更日志里没有它那一行）时，说明不能复用上一条。"""
    import shutil
    import tempfile

    passes = 0
    total = 0

    def check(name, cond, extra=""):
        nonlocal passes, total
        total += 1
        if cond:
            passes += 1
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ {name} {extra}")

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        refs = root / "references"
        refs.mkdir(parents=True, exist_ok=True)
        (refs / "921.md").write_text(
            "| 日期 | 文件 | 改动 | 触发 |\n"
            "| --- | --- | --- | --- |\n"
            "| 2026-10-26 | `memory/809-独立人格与立场.md` | 【上一条说明】不该被复用 | 自测 |\n",
            encoding="utf-8",
            newline="",
        )
        for cmd in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "--local", "user.name", "selftest"],
            ["git", "config", "--local", "user.email", "selftest@local"],
        ):
            subprocess.run(cmd, cwd=str(root), capture_output=True, timeout=10)
        (refs / "922.md").write_text("待确认内容\n", encoding="utf-8", newline="")
        subprocess.run(["git", "add", "-A"], cwd=str(root), capture_output=True, timeout=10)
        (refs / "922.md").write_text("待确认内容改了\n", encoding="utf-8", newline="")

        files = changed_files(root)
        check("能取到改动文件名", any(f.endswith("922.md") for f in files), str(files))
        msg = default_msg(root)
        check(
            "只写 922.md 时说明与内容一致（不复用上一条）",
            "922.md" in msg and "上一条说明" not in msg,
            msg,
        )
        check(
            "兜底带文件数与「无对应变更日志行」",
            "个文件" in msg and "无对应变更日志行" in msg,
            msg,
        )

        (refs / "809-独立人格与立场.md").write_text("正文\n", encoding="utf-8", newline="")
        subprocess.run(["git", "add", "-A"], cwd=str(root), capture_output=True, timeout=10)
        matched = default_msg(root)
        check("有对应变更日志行时仍用那行说明", "不该被复用" in matched, matched)

        (refs / "921.md").unlink()
        subprocess.run(["git", "add", "-A"], cwd=str(root), capture_output=True, timeout=10)
        no_log = default_msg(root)
        check("变更日志缺失时也走文件名兜底", "922.md" in no_log, no_log)

        check("仓库没被自测改坏", (refs / "922.md").read_text(encoding="utf-8") == "待确认内容改了\n")

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"selftest {passes}/{total}")
    return 0 if passes == total else 1


def cmd_init(_args):
    if has_repo():
        print("已经是 git 仓库了")
        return
    git("init", "-b", "main")
    git("add", "-A")
    p = git("commit", "-m", "chore: 初始化记忆库", check=False)
    if p.returncode != 0:
        print("初始化提交没成功：" + (p.stderr or p.stdout).strip())
        print("（没有 git 身份就先跑：git config --global user.name / user.email）")
        return
    print("已初始化仓库，分支 main")
    print("下一步：python tools/memgit.py remote <你的私库地址>")


def cmd_remote(args):
    if not args:
        print("用法：python tools/memgit.py remote <<你的邮箱>:你/user-memory.git>")
        return
    if "origin" in git("remote", check=False).stdout:
        git("remote", "set-url", "origin", args[0])
        print("已更新 origin：" + args[0])
    else:
        git("remote", "add", "origin", args[0])
        print("已绑定 origin：" + args[0])


def cmd_status(_args):
    if not has_repo():
        print("还不是 git 仓库，先跑 python tools/memgit.py init")
        return
    branch = git("rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip() or "?"
    st = git("status", "--porcelain").stdout.strip()
    remotes = git("remote", check=False).stdout.strip()
    print(f"分支：{branch}　远端：{remotes or '无'}")
    if not st:
        print("工作区干净")
    else:
        lines = st.splitlines()
        print(f"有改动：{len(lines)} 个文件")
        for line in lines[:20]:
            print("  " + line)
        if len(lines) > 20:
            print(f"  …还有 {len(lines) - 20} 个")
    if "origin" in remotes:
        ahead = git("rev-list", "--count", "origin/main..HEAD", check=False).stdout.strip()
        behind = git("rev-list", "--count", "HEAD..origin/main", check=False).stdout.strip()
        if ahead.isdigit() or behind.isdigit():
            print(f"领先 {ahead or 0} 个提交，落后 {behind or 0} 个提交")


def do_commit(msg):
    ensure_identity()
    ensure_fragments()   # 提交前先切块：只提交不 build，检索层还是旧的（2026-10-20）
    git("add", "-A")
    if not git("status", "--porcelain").stdout.strip():
        print("没有改动，不用提交")
        return False
    p = git("commit", "-m", msg, check=False)
    if p.returncode != 0:
        out = (p.stderr or p.stdout).strip()
        print("提交失败：" + out)
        return False
    print("已提交：" + msg)
    return True


def cmd_commit(args):
    if not has_repo():
        print("还不是 git 仓库，先跑 python tools/memgit.py init")
        return
    do_commit(args[0] if args else default_msg())


def cmd_sync(args):
    if not has_repo():
        print("还不是 git 仓库，先跑 python tools/memgit.py init")
        return
    if "origin" not in git("remote", check=False).stdout:
        print("还没绑远端，先跑：python tools/memgit.py remote <私库地址>")
        return
    do_commit(args[0] if args else default_msg())
    pull = git_net("pull", "--rebase", "--autostash", "origin", "main",
                    timeout=PULL_TIMEOUT, budget=PULL_BUDGET)
    if pull.returncode != 0:
        print("拉取失败，可能撞冲突，先手动解决：" + (pull.stderr or pull.stdout).strip())
        return
    push = git_net("push", "-u", "origin", "main")
    if push.returncode != 0:
        print("推送失败：" + (push.stderr or push.stdout).strip())
        return
    print("已同步到远端")


def ensure_fragments():
    """片段层不进 git，拉完在本地重建一次，保证 INDEX.md 和 references 对得上

    新机器 clone 下来、或另一台机器改过记忆之后，fragments/ 是空的或过期的，
    只 pull 不重建就等于读了半份记忆，所以这里顺手补上（失败只提示，不拦）。
    """
    frag_py = ROOT / "tools" / "frag.py"
    index = ROOT / "fragments" / "INDEX.md"
    if not frag_py.exists():
        return
    refs = module_paths()
    stale = not index.exists()
    if not stale and refs:
        try:
            stale = max(p.stat().st_mtime for p in refs) > index.stat().st_mtime
        except OSError:
            stale = True
    if not stale:
        return
    p = subprocess.run(
        [sys.executable, str(frag_py), "build"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if p.returncode != 0:
        tail = ((p.stderr or "") + (p.stdout or "")).strip().splitlines()
        print("⚠️ 片段重建失败（先按旧片段读）：" + (tail[-1][:160] if tail else "未知错误"))


def ensure_managed_blocks():
    """拉完把工作区 AGENTS.md 对齐到本机这份记忆库（2026-10-21 / 2026-10-22 扩展）

    多设备的病根：`AGENTS.md` 不进 git，每台设备各有一份，硬塞层 / 大类表
    全靠人手抄，抄漏一台就"这台设备好像不太对劲"。现在分两层：
    ① `tools/agents_hosts.py`：整份按主机名铺下来（`templates/hosts/<主机名>.md`
    是 git 里的权威版，两台机器各一份，改规则时一起改）——这一层连 §1～§7 的
    散文规则都覆盖，换机器不用手动搬；
    ② `tools/agents_sync.py`：再对齐 §8 硬塞层与大类表两个托管块（真源变了
    之后不用等 hosts 重新铺）。
    失败只提示，不拦着读记忆。
    """
    for name, hint in (("agents_hosts.py", "整份 AGENTS.md 没铺下来（先按原样读）"),
                       ("agents_sync.py", "AGENTS.md 托管块没对齐（先按原样读）")):
        run_py = ROOT / "tools" / name
        if not run_py.exists():
            continue
        p = subprocess.run(
            [sys.executable, str(run_py), "--quiet"] if name == "agents_sync.py"
            else [sys.executable, str(run_py)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if p.returncode != 0:
            tail = ((p.stderr or "") + (p.stdout or "")).strip().splitlines()
            msg = tail[-1][:160] if tail else "未知错误"
            # 没建过备份是新机器的正常状态，不刷警告
            if name == "agents_hosts.py" and "没有本主机" in msg:
                continue
            print(f"⚠️ {hint}：" + msg)


def cmd_pull(_args):
    """开局重载用：只拉不提交，失败也不拦着读记忆"""
    if not has_repo():
        ensure_managed_blocks()
        ensure_fragments()
        return
    if "origin" not in git("remote", check=False).stdout:
        ensure_managed_blocks()
        ensure_fragments()
        return
    p = git_net("pull", "--rebase", "--autostash", "origin", "main",
                timeout=PULL_TIMEOUT, budget=PULL_BUDGET)
    out = ((p.stderr or "") + (p.stdout or "")).strip()
    if p.returncode != 0:
        print("⚠️ 拉取没成（离线/超时/冲突），按本地版本继续读，回头重载再对一次：" + _errtail(p))
    elif "up to date" in out or "Already" in out:
        pass
    else:
        print("已拉到远端最新的记忆")
    ensure_managed_blocks()
    ensure_fragments()


def cmd_autosync(args):
    """写完记忆立刻提交并推送；任何一步失败都只提示，不影响对话"""
    if not has_repo():
        print("autosync：这里不是 git 仓库，没提交也没推送（跳过）")
        return
    remotes = git("remote", check=False).stdout.strip()
    do_commit(args[0] if args else default_msg())
    if "origin" not in remotes:
        print("autosync：没绑 origin 远端，已提交到本地，跳过推送")
        return
    pull = git_net("pull", "--rebase", "--autostash", "origin", "main",
                   timeout=PULL_TIMEOUT, budget=PULL_BUDGET)
    if pull.returncode != 0:
        print(f"⚠️ 拉取没成（{_why(pull)}），本地已提交但还没推：" + _errtail(pull))
        return
    push = git_net("push", "origin", "main")
    if push.returncode != 0:
        print(f"⚠️ 推送没成（{_why(push)}），本地已提交但还没推：" + _errtail(push))
        return
    print("已推送（本地提交已进 origin/main）")


def cmd_check(_args):
    """开局核对：和 git 最新比一比，落后就自动拉；领先/分叉只报告不动用户的东西

    2026-10-21 她点名要的：「每次新对话都对比 git 最新」。原来的 `pull` 只是
    「拉得下来就拉」，拉不下来什么都不说，本地领先也看不出来；这条把四种状态
    都打印成一个 `GIT:` 开头的单行，agent 开局扫一眼就知道自己读的是不是最新的。
    退出码恒 0（离线不该拦着读记忆），状态全在那一行里。
    """
    if not has_repo():
        print("GIT: 不是仓库，跳过比对")
        return
    if "origin" not in git("remote", check=False).stdout:
        print("GIT: 没绑远端，跳过比对")
        return
    # 开局核对只给 3 s（她要求（原话已移除））：宁可报「没比成」，
    # 也不该让新对话卡半分钟，更不该为了对上云端把会话拖住
    f = git_net("fetch", "origin", "main", timeout=FETCH_TIMEOUT, budget=FETCH_BUDGET)
    if f.returncode != 0:
        print("GIT: 离线，没比成（按本地版本继续）：" + _errtail(f, 90))
        return
    head = git("rev-parse", "HEAD", check=False).stdout.strip()
    remote = git("rev-parse", "origin/main", check=False).stdout.strip()
    if not head or not remote:
        print("GIT: 拿不到提交号，跳过比对")
        return
    if head == remote:
        print(f"GIT: 已是最新 {head[:8]}")
        # 已最新也要顺手对齐：换设备时 AGENTS.md 是空的/手抄的，跟 git 是否最新无关
        ensure_managed_blocks()
        return
    ahead = git("rev-list", "--count", f"origin/main..HEAD", check=False).stdout.strip() or "0"
    behind = git("rev-list", "--count", f"HEAD..origin/main", check=False).stdout.strip() or "0"
    if behind != "0" and ahead == "0":
        p = git_net("pull", "--rebase", "--autostash", "origin", "main",
                    timeout=PULL_TIMEOUT, budget=PULL_BUDGET)
        if p.returncode == 0:
            now = git("rev-parse", "HEAD", check=False).stdout.strip()
            print(f"GIT: 落后 {behind} 个提交，已拉到最新 {now[:8]}")
            ensure_managed_blocks()
            ensure_fragments()
        else:
            print(f"GIT: 落后 {behind} 个提交，拉取失败（按本地版本继续）：" + _errtail(p, 90))
        return
    if ahead != "0" and behind == "0":
        print(f"GIT: 本地领先 {ahead} 个提交（还没推）：{head[:8]}")
        return
    print(f"GIT: 分叉了（领先 {ahead} / 落后 {behind}），要人工处理：{head[:8]} vs {remote[:8]}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--selftest", "selftest"):
        sys.exit(selftest())
    cmds = {
        "init": cmd_init,
        "remote": cmd_remote,
        "status": cmd_status,
        "check": cmd_check,
        "commit": cmd_commit,
        "pull": cmd_pull,
        "sync": cmd_sync,
        "autosync": cmd_autosync,
    }
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        print("提示：" + GITIGNORE_HINT)
        return
    ensure_identity()
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
