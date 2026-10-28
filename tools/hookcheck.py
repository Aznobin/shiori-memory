"""体检 DSH 的 hook 真实执行情况。

为什么要有这个：配置写着在、手工能跑，都不等于真实会话里跑过。
2026-10-20 我连栽两次（先说「已接好」，再说「已跑通」），都被当场抓住。

所以本工具只报三件事，且只报能拿出凭据的：
    1. 每个 turn 实际跑了几条 hook、各是 exit 几
    2. 跑出来的条数跟 hooks.json 里登记的期望条数对不对得上（少跑也算失败）
    3. 记忆引擎有没有真被调用过（状态文件里有没有真实 session id）

用法：
    python tools\\hookcheck.py                 # 自动找最近的会话日志
    python tools\\hookcheck.py --session d966   # 指定会话目录名片段
    python tools\\hookcheck.py --tail 4          # 每个 turn 最多列 N 条
    python tools\\hookcheck.py --expect 1        # 手动指定每个 point 的期望 hook 条数

退出码：0 = 最近一个完成的 turn 条数齐、全部 exit 0；1 = 否则
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

HOME = os.path.expanduser("~")
SESSIONS = os.path.join(HOME, ".dsh", "sessions")
HOOKS_JSON = os.path.join(HOME, ".codex", "hooks.json")
STATE_DIR = os.path.join(tempfile.gettempdir(), "memory-recall")
DEC = os.path.join(tempfile.gettempdir(), "hookcheck-decode.js")

# 这些 exitCode 的含义**有据可查**，不是猜的：
#   0          —— 命令正常返回（hook 协议把它记成 decision: pass）
#   1          —— 命令非 0 退出；2026-10-20 实测两种来源：ParserError（引号写法）、脚本自身报错
#   3221225794 —— 0xC0000142 = STATUS_DLL_INIT_FAILED；2026-10-20 实测在 pwsh-sandbox 的
#                 read-only 模式下出现，放成 danger-full-access 后消失。**关联关系是观测到的，
#                 不是唯一成因**，别把它当成定论。
EXIT_MEANING = {
    0: "OK",
    1: "非 0 退出（看 stderrSummary）",
    3221225794: "0xC0000142 STATUS_DLL_INIT_FAILED（本机实测：沙箱 read-only 下出现）",
}

# 会话日志是**多帧 zstd 拼接**，整体 zstdDecompressSync 会失败；
# 必须按 magic 0x28B52FFD 切帧逐段解。
JS = r"""
const fs = require("node:fs"), zlib = require("node:zlib");
const buf = fs.readFileSync(process.argv[2]);
const MAGIC = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);
const offs = [];
let i = 0;
while ((i = buf.indexOf(MAGIC, i)) !== -1) { offs.push(i); i += 4; }
const out = [];
for (let k = 0; k < offs.length; k++) {
	const end = k + 1 < offs.length ? offs[k + 1] : buf.length;
	try { out.push(zlib.zstdDecompressSync(buf.subarray(offs[k], end))); } catch (e) {}
}
process.stdout.write(Buffer.concat(out).toString("utf8"));
"""


def newest_session():
    """挑「最后一次真的跑过 hook」的会话，而不是最近被写过文件的会话。

    差别很实在：子智能体、闲置会话都会刷新 mtime，但它们可能一条 hook 都没有；
    按 mtime 挑会挑到空日志，然后误报「hook 从来没执行过」。
    正确口径：哪个日志里最后一条 hook/result 时间最晚，就是它。
    """
    if not os.path.isdir(SESSIONS):
        return None
    cand = []
    for proj in os.listdir(SESSIONS):
        d = os.path.join(SESSIONS, proj)
        if not os.path.isdir(d):
            continue
        for sess in os.listdir(d):
            f = os.path.join(d, sess, "session.v4.jsonl.zstd")
            if os.path.isfile(f):
                cand.append(f)
    cand.sort(key=os.path.getmtime, reverse=True)
    best, best_t = None, -1.0
    for f in cand[:10]:
        try:
            _, results, _, last, _ = parse_events(read_log(f))
        except SystemExit:
            continue
        if results and last > best_t:
            best, best_t = f, last
    return best or (cand[0] if cand else None)


def _scan(name):
    if not os.path.isdir(SESSIONS):
        return None
    best, best_t = None, -1
    for proj in os.listdir(SESSIONS):
        d = os.path.join(SESSIONS, proj)
        if not os.path.isdir(d):
            continue
        for sess in os.listdir(d):
            if name and name not in sess:
                continue
            f = os.path.join(d, sess, "session.v4.jsonl.zstd")
            if not os.path.isfile(f):
                continue
            if name:
                return f
            t = os.path.getmtime(f)
            if t > best_t:
                best, best_t = f, t
    return best


def read_log(path):
    with open(DEC, "w", encoding="utf-8") as f:
        f.write(JS)
    out = subprocess.run(
        ["node", DEC, path], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if out.returncode != 0:
        raise SystemExit("解压失败：%s" % (out.stderr or "").strip()[:300])
    return out.stdout.splitlines()


def parse_events(lines):
    """按 type 精确归类，不靠字符串包含——上一版就栽在这儿。

    同时收 `injected`：会话日志里由 hook 送进上下文的 `user/message`。
    这才是**交付**的唯一直接证据（2026-10-20 扒出来的那一层）：
    状态文件只能证明 recall.py 吐了字节，日志里出现这张卡才证明宿主真的
    把 additionalContext 塞进了上下文。

    判据是 `data.source.kind == "hooks-codex"` —— **不是** `message.source`
    （那个实测 86 条全空），也不是纯靠文本（subagent 的结案报告会引用
    「【记忆·按需注入】」这个标记，一撞就是假阳性，2026-10-20 真撞过一次）。
    实测 1:1：ab836630 有 4 条 `hooks-codex`、4 张卡；d966adaf 3/3。
    """
    invoked, results, turns_done, injected = [], [], set(), []
    last_time = 0
    for line in lines:
        if '"hooks-codex"' not in line and '"hook/' not in line and '"turn/end"' not in line:
            continue
        start = line.find('{"type"')
        if start < 0:
            continue
        try:
            ev = json.loads(line[start:])
        except ValueError:
            continue
        typ = ev.get("type")
        data = ev.get("data") or {}
        t = ev.get("time") or 0
        t = t / 1000.0 if t > 1e12 else t
        if typ == "hook/invoked":
            invoked.append(data)
        elif typ == "hook/result":
            # 时间必须跟着每条结果走：判据④ 要拿「这条 hook」和「下一条 hook」
            # 之间的窗口去配对卡片，而不是一个固定的 ±120 秒（验收时发现）。
            d = dict(data)
            d["_t"] = t
            results.append(d)
            last_time = max(last_time, t)
        elif typ == "turn/end":
            turns_done.add(data.get("turn"))
        elif typ == "user/message":
            # 只认宿主标了 hooks-codex 的那些；文本标记仅作为二次确认写进记录。
            src = data.get("source") or {}
            if src.get("kind") != "hooks-codex":
                continue
            text = ""
            for c in (data.get("content") or []):
                if isinstance(c, dict) and c.get("type") == "text" and (c.get("text") or "").strip():
                    text = c.get("text") or ""
                    break
            injected.append({
                "time": t,
                "kind": src.get("kind"),
                "text": text,
                "marked": text.lstrip().startswith("【记忆·按需注入】"),
            })
    return invoked, results, turns_done, last_time, injected


def expected_per_point():
    """从 hooks.json 数出每个 point 登记了几条 command hook。"""
    try:
        with open(HOOKS_JSON, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for point, groups in (cfg.get("hooks") or {}).items():
        n = 0
        for g in groups or []:
            for h in (g.get("hooks") or []):
                if (h.get("type") or "command") == "command" and isinstance(h.get("command"), str):
                    n += 1
        out[point] = n
    return out


def state_files(sess_id=""):
    """列出状态文件，并把每个的「最后一轮注入账」读出来。

    kind 只看文件名是**不够**的（验收时发现）：诚实验证时
    很可能拿真实 session id 去喂手工 payload，那时文件名会冒充真证据。
    所以真正判据落在 `log` 上，外加**按当前会话 id 对齐**（2026-10-20 同日补刀：
    同时开着的另一个窗口只要 chars>0 就会被认成「本轮交付」）。
    """
    if not os.path.isdir(STATE_DIR):
        return None, []
    rows = []
    for n in sorted(os.listdir(STATE_DIR)):
        p = os.path.join(STATE_DIR, n)
        kind = "real"
        low = n.lower()
        if low.startswith(("probe", "manual", "e2e", "diag")) or "probe" in low or "e2e" in low:
            kind = "手工喂的，不算证据"
        if sess_id and sess_id not in n:
            kind = "别的会话"
        try:
            with open(p, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        log = st.get("log") or []
        last = log[-1] if log else {}
        rows.append({
            "name": n,
            "kind": kind,
            "mtime": os.path.getmtime(p),
            "chars": last.get("chars"),
            "items": last.get("items"),
            "why": last.get("why") or "",
            "q": last.get("q") or "",
            "n": len(log),
            "seen": len(st.get("seen") or []),
        })
    return STATE_DIR, rows


def main():
    # GBK 控制台下 print('⚠') 会 UnicodeEncodeError 把整轮判据打成 exit 1；
    # 只放宽编码错误处理，不改编码本身，免得不带 PYTHONIOENCODING 时崩（2026-10-28）。
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="")
    ap.add_argument("--tail", type=int, default=6)
    ap.add_argument("--expect", type=int, default=0)
    args = ap.parse_args()

    path = _scan(args.session) if args.session else newest_session()
    if not path:
        raise SystemExit("找不到会话日志，检查 %s" % SESSIONS)
    print("会话日志：%s" % path)

    # 会话 id 只能取日志所在目录名。不按 id 对齐就是瞎认亲：
    # 验收时发现，同时开着的另一个会话只要 chars>0，就会被当成本轮交付。
    sess_id = os.path.basename(os.path.dirname(path))
    print("当前会话 id：%s" % sess_id)

    invoked, results, turns_done, last_result_time, injected = parse_events(read_log(path))
    if not results:
        print("日志里一条 hook/result 都没有 —— hook 从来没执行过")
        return 1

    exp = expected_per_point()
    n_expect = args.expect or (max(exp.values()) if exp else 0)
    print("hooks.json 登记的每条 point：%s" % (exp or "（读不到）"))
    print("hook/invoked %d 条 · hook/result %d 条\n" % (len(invoked), len(results)))

    by_turn = {}
    for r in results:
        by_turn.setdefault(r.get("turn"), []).append(r)
    turns = sorted(t for t in by_turn if isinstance(t, int))
    if not turns:
        print("hook/result 里没有带 turn 的条目")
        return 1

    last_turn = turns[-1]
    for turn in turns[-3:]:
        rows = by_turn[turn]
        codes = [r.get("exitCode") for r in rows]
        ok = sum(1 for c in codes if c == 0)
        closed = turn in turns_done
        verdict = "全通"
        if ok != len(rows):
            verdict = "有失败"
        # 条数必须按 point 分组核对。上一版用 max(exp.values()) 当除数，
        # 只登记 1 条时 `len(rows) % 1 == 0` 恒真 —— 等于没查（验收时发现）。
        if exp:
            got = {}
            for r in rows:
                got[r.get("point")] = got.get(r.get("point"), 0) + 1
            bad = ["%s %d/%d" % (p, got.get(p, 0), n) for p, n in exp.items() if got.get(p, 0) != n]
            if bad:
                verdict += "，条数不齐（%s）" % "、".join(bad)
        print("turn %s：%d 条，exit 0 的 %d 条 → %s%s" % (
            turn, len(rows), ok, verdict, "" if closed else "（turn 未结束，暂不定论）"))
        for r in rows[-args.tail:]:
            c = r.get("exitCode")
            tag = EXIT_MEANING.get(c, "未知")
            extra = ""
            if c not in (0, None):
                extra = "  " + (r.get("stderrSummary") or "").replace("\r", " ").replace("\n", " ")[:100]
            print("   %-24s exit=%-12s %s%s" % (r.get("handlerId"), c, tag, extra))
        print()

    d, rows = state_files(sess_id)
    print("状态文件 %s：" % (d or STATE_DIR))
    if d is None:
        print("   目录不存在（引擎从没落过盘）")
    elif not rows:
        print("   空（引擎从没落过盘）")
    for r in rows:
        stamp = time.strftime("%H:%M:%S", time.localtime(r["mtime"]))
        tail = "" if r["kind"] == "real" else r["kind"]
        if r["chars"] is not None:
            tail = ("注入 %s 字/ %s 条（%s）" % (r["chars"], r["items"], r["why"])) + (
                "  " + tail if tail else "")
        print("   %-50s %-8s %s" % (r["name"], stamp, tail))

    # ---------------------------------------------------------------- 判定
    # 走过的弯路，记在这儿免得再犯：
    #   v1 用整个 turn 判 —— turn 横跨改动前后，误杀。
    #   v2 用「最后 n_expect 条」判 —— 从两条命令改成一条时，那批里还夹着旧命令的 1，
    #      明明已经通了却报失败。
    #   v3 用「最后 2*n_expect 条」判 —— 探针删了以后又把历史算进来，同样误杀。
    #
    # v4（挑出来的真口子）：上面几条判的全部是
    # 「进程起来了」，从没判过「它真的把记忆交回去了」。
    # recall.py 在闸门拦下、或一条都没命中时，照样 exit 0、照样落状态文件，
    # 只是 stdout 一个字节都没有。所以必须加第三条判据，读引擎自己记的账：
    #   ③ 最后那轮注入的 chars > 0（真的吐了文本），或 why 明确是闸门拦下的
    #      （gate:xxx —— 这是设计行为，不是故障）
    #
    # v5（2026-10-20 补的一刀）：`chars` 是 `len(card)`，写在 %TEMP% 普通文件里，
    # 只证明 recall.py 构造出了卡并写了 stdout，**证明不了宿主 adopt 了它**。
    # 唯一直接证据是会话日志里出现带「【记忆·按需注入】」的 user/message
    # （实测 19:04:02 有，且紧跟在前一条 hook/result 之后）。所以第四条判据：
    #   ④ 日志里有一条 injected 卡，时间落在最后这条 hook 前后 120 秒内
    # 顺带修掉退出码混同：0=四件齐、2=通道通了但本轮没交付、1=通道都不通。
    print()
    problems = []
    last = results[-1]
    if last.get("exitCode") != 0:
        problems.append("最后一条 hook/result 是 exit=%s（%s）—— hook 没跑通"
                        % (last.get("exitCode"), last.get("handlerId")))

    # 只认「文件名 == 当前会话 id」的状态文件。放宽成「按 mtime 挑最近的」就会
    # 认错亲戚：另一个会话有交付、本会话 0 字，工具照样报绿灯（抓的）。
    def belongs(r):
        stem = r["name"][:-5] if r["name"].endswith(".json") else r["name"]
        return stem == sess_id or stem == "session-" + sess_id

    mine = [r for r in rows if belongs(r)]
    real = [r for r in mine if r["kind"] == "real"]
    last_t = last_result_time or 0
    fresh = [r for r in real if r["mtime"] >= last_t - 2]
    if not mine:
        problems.append("状态目录里没有本会话（%s）的文件 —— 引擎没被这一轮真调用" % sess_id)
    elif not fresh:
        problems.append("本会话（%s）的状态文件没在「最后这条 hook 之后」更新 —— 引擎没被真调用"
                        % sess_id)

    delivered = [r for r in fresh if (r["chars"] or 0) > 0]
    gated = [r for r in fresh if not (r["chars"] or 0) and r["why"].startswith("gate:")]
    legacy = [r for r in fresh if r["n"] == 0]
    empty = [r for r in fresh if not (r["chars"] or 0) and not r["why"].startswith("gate:") and r["n"]]

    # 交付的最硬证据：日志里真的出现了那张卡（指出 chars 不够）
    #
    # 配对不对用固定窗口，必须按「这条 hook 到下一条 hook」严格划分：
    # 窗口法实测三处误判（复现）——
    #   turn 19（19:03:22）窗口配到 19:04:41 的卡，那是 turn 20 的；
    #   turn 20（19:04:41）窗口配到 2 张（19:04:41 + 19:05:49）；
    #   最后那条 19:13:20 窗口配到 19:11:48 的卡 —— 明明 gate:wait 却会印「四件齐了」。
    # 严格配对：card.time >= hook.time 且 < 下一条 hook.time。
    #
    # 最后一个窗口原来用 `inf` 兜底，等于「之后任何一张卡都算它的」——
    # 手工补一张晚于最后一条 hook 的卡就能骗过（验收时发现）。
    # 上界取「会话日志最后一条事件的时间」：卡不可能晚于日志自己写完。
    all_ts = sorted([e["time"] for e in injected if e.get("time")] +
                    [r["_t"] for r in results if r.get("_t")])
    log_end = all_ts[-1] + 1 if all_ts else float("inf")
    hook_ts = sorted(r["_t"] for r in results if r.get("_t"))
    landed = None
    stale = None          # 落在上界之外、因而不被采信的卡
    for i, t0 in enumerate(hook_ts):
        t1 = hook_ts[i + 1] if i + 1 < len(hook_ts) else log_end
        mine_cards = [e for e in injected if t0 <= (e["time"] or 0) < t1]
        if i == len(hook_ts) - 1:
            landed = mine_cards[-1] if mine_cards else None
            stale = [e for e in injected if (e["time"] or 0) >= t1]
    print("日志里 hooks-codex 送的注入卡：%d 条（最后这条 hook 的窗口里 %d 条）"
          % (len(injected), 1 if landed else 0))
    if landed:
        head = (landed["text"] or "").splitlines()[0] if landed["text"] else ""
        print("   最近一条：%s  %s%s" % (
            time.strftime("%H:%M:%S", time.localtime(landed["time"] or 0)), head[:70],
            "" if landed.get("marked") else "（⚠ 文本没带注入标记，可疑）"))
    if stale:
        print("   ⚠ 有 %d 张卡晚于会话日志末尾，不采信（可能被手工补过）" % len(stale))

    # 工具回答的是「**最近这一轮**交没交」，不是「hook 通不通」。这两件事必须分开说，
    # 否则同一条会话隔四分钟再跑一次结论就翻面（验收时发现）。
    if delivered or landed:
        print("注：以上是「最近这一轮」的状态，会随对话推进而变；要问「通道到底通不通」，"
              "看历史里是否出现过『四件齐了』那一次。")

    if delivered:
        r = delivered[0]
        print("本轮交付：%s 注入 %s 字 / %s 条（%s）"
              % (r["name"], r["chars"], r["items"], r["why"]))
    elif gated:
        print("本轮交付：0 字 —— %s 被闸门拦下（%s）" % (gated[0]["name"], gated[0]["why"]))
    elif legacy:
        problems.append("%s 是记录账本之前的旧版本写的（没有 log 字段）—— 无法判定那轮到底有没有"
                        "交出记忆，需要下一次真实触发之后再看" % legacy[0]["name"])
    elif empty:
        print("本轮交付：0 字 —— %s 落盘了但 stdout 是空的（why=%s）"
              % (empty[0]["name"], empty[0]["why"] or "无记录"))
        problems.append("引擎落盘≠记忆交付：只证明进程起来了，没证明文本交回去了")

    # 旁证：磁盘上的 hooks.json 改了、但没看到对应的成功，才提一句「可能要重启」
    if problems:
        probe = os.path.join(tempfile.gettempdir(), "hook-probe.log")
        try:
            if os.path.getmtime(probe) > os.path.getmtime(HOOKS_JSON):
                print("旁证：hook-probe.log（%s）比 hooks.json（%s）还新 —— "
                      "进程内存里加载的可能是旧快照" % (
                          time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(probe))),
                          time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(HOOKS_JSON)))))
        except OSError:
            pass

    # 结案句必须把「通道」和「交付」分开说。抓的正是这个：
    # 上一版闸门拦下时不进 problems，结案句照样印「已落盘」，把「0 字」吞了。
    print()
    if problems:
        print("结论：还不能说「已经好了」。")
        for p in problems:
            print("  - " + p)
        return 1

    channel = "最后一条 hook exit 0，本会话状态文件已更新"
    if delivered and landed:
        print("结论：四件齐了——hook 跑了、引擎吐了 %s 字、日志里真的出现了那张卡（%s）。"
              % (delivered[0]["chars"],
                 time.strftime("%H:%M:%S", time.localtime(landed["time"] or 0))))
        return 0
    if delivered:
        # chars > 0 只证明 recall.py 构造了卡并写了 stdout。宿主要不要 adopt 它，
        # 只有在会话日志里看到那张卡才算数（补的这一刀）。
        print("结论：通道通了，引擎吐了 %s 字，但日志里没找到相邻的注入卡——"
              "**交付这一环没验实**。`chars` 只是 `len(card)`，证明不了宿主 adopt 了它。"
              % delivered[0]["chars"])
        return 2
    # 退出码也是判据的一部分。上一版这条分支返回 0，谁拿 `$LASTEXITCODE` 当通过标准，
    # 就会把「0 字」读成通过——指出的第二个口子。
    print("结论：通道通了（%s），但**这一轮没交付**（0 字：%s）——闸门拦下是设计行为，"
          "不代表记忆被注进上下文。要验「真的注进去了」，得等一次 chars > 0 的轮次。"
          % (channel, (gated[0]["why"] if gated else "无记录")))
    return 2


if __name__ == "__main__":
    sys.exit(main())
