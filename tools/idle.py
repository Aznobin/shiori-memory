#!/usr/bin/env python
"""自启停静默门：定时到点时先问一句「她还在不在说话」。

口径见 `references/911-自启停与静默门.md:27-41`——**120 秒是唯一真值**，
`QUIET` 不许打 `lastFireAt`、不许动 `emptyFires`（等真没人了下一轮还得能 `OK`）。

用法：
    python tools/idle.py user            # 她每发一条真消息时打点
    python tools/idle.py fire [秒数]     # 到点先跑这个，按输出决定说不说话
    python tools/idle.py pause           # 让她闭嘴（她让停定时用）
    python tools/idle.py resume          # 解禁
    python tools/idle.py status          # 打印状态 JSON
    python tools/idle.py reset           # 清空状态
    python tools/idle.py --selftest      # 假时钟 + 临时状态文件，不碰真状态

状态文件 `<home>/.agents/state/idle.json`：工具状态，不进记忆库、不进 dist。
脚本不碰 git。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# 120 秒是唯一真值（911:27 / 911:35 / 804），别在别处再写一遍
QUIET_WINDOW = 120

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path.home() / ".agents" / "state"
STATE_FILE = STATE_DIR / "idle.json"

DEFAULT_STATE = {"lastUserAt": None, "lastFireAt": None, "emptyFires": 0, "paused": False}

# selftest 用：注入假时钟与临时状态文件，绝不落到真状态
_CLOCK = time.time
_STATE_PATH = STATE_FILE


def _now() -> float:
    return float(_CLOCK())


def _state_path() -> Path:
    return _STATE_PATH


def _assert_outside_repo(path: Path) -> None:
    """状态文件绝不许落在记忆库或 dist 里（会被同步出去）。"""
    resolved = path.resolve()
    for bad in (REPO_ROOT.resolve(), (REPO_ROOT / "dist").resolve()):
        if resolved == bad or bad in resolved.parents:
            raise SystemExit(
                f"✗ 状态文件路径落在被同步的仓库里：{resolved}\n"
                f"  必须是仓库外（默认 {STATE_FILE}），拒绝写入"
            )


def load_state(path: Path | None = None) -> dict:
    path = path or _state_path()
    state = dict(DEFAULT_STATE)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raw = {}
        if isinstance(raw, dict):
            for key in DEFAULT_STATE:
                if key in raw:
                    state[key] = raw[key]
    state["emptyFires"] = int(state.get("emptyFires") or 0)
    state["paused"] = bool(state.get("paused"))
    return state


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or _state_path()
    _assert_outside_repo(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: state.get(key, DEFAULT_STATE[key]) for key in DEFAULT_STATE}
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")


def cmd_user(state: dict, dry: bool) -> int:
    state["lastUserAt"] = _now()
    state["emptyFires"] = 0
    state["paused"] = False
    if not dry:
        save_state(state)
    print("OK")
    return 0


def cmd_fire(state: dict, seconds: float | None, dry: bool) -> int:
    now = _now()
    if state["paused"]:
        print("PAUSED")
        return 0

    if seconds is None:
        last_user = state["lastUserAt"]
        elapsed = None if last_user is None else now - float(last_user)
    else:
        elapsed = float(seconds)

    if elapsed is not None and elapsed < QUIET_WINDOW:
        # 她还在说话：一个字都不回，且状态一字不动
        print("QUIET")
        return 0

    last_user = state["lastUserAt"]
    last_fire = state["lastFireAt"] or 0
    if last_user is not None and float(last_user) > float(last_fire):
        state["emptyFires"] = 0
        state["lastFireAt"] = now
        if not dry:
            save_state(state)
        print("OK")
        return 0

    state["emptyFires"] = int(state["emptyFires"]) + 1
    state["lastFireAt"] = now
    if not dry:
        save_state(state)
    print(f"EMPTY {1 if state['emptyFires'] == 1 else 2}")
    return 0


def cmd_pause(state: dict, dry: bool) -> int:
    state["paused"] = True
    if not dry:
        save_state(state)
    print("PAUSED")
    return 0


def cmd_resume(state: dict, dry: bool) -> int:
    state["paused"] = False
    if not dry:
        save_state(state)
    print("OK")
    return 0


def cmd_status(state: dict) -> int:
    out = {
        "lastUserAt": _iso(state["lastUserAt"]),
        "lastUserAtEpoch": state["lastUserAt"],
        "lastFireAt": _iso(state["lastFireAt"]),
        "lastFireAtEpoch": state["lastFireAt"],
        "emptyFires": state["emptyFires"],
        "paused": state["paused"],
        "quietWindow": QUIET_WINDOW,
        "stateFile": str(_state_path()),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_reset(state: dict, dry: bool) -> int:
    for key, value in DEFAULT_STATE.items():
        state[key] = value
    if not dry:
        save_state(state)
    print("OK")
    return 0


def selftest() -> int:
    """假时钟 + 临时状态文件，覆盖七种情形；全程不碰真状态、不碰 git。"""
    import tempfile

    global _CLOCK, _STATE_PATH

    real_clock, real_path = _CLOCK, _STATE_PATH
    tmpdir = Path(tempfile.mkdtemp(prefix="idle-selftest-"))
    tmp_state = tmpdir / "idle.json"
    checks: list[tuple[str, bool]] = []
    t = [1_000_000.0]

    def run(args: list[str]) -> tuple[int, str]:
        out: list[str] = []

        class _Cap(io.StringIO):
            pass

        import contextlib

        buf = _Cap()
        with contextlib.redirect_stdout(buf):
            code = main(args, state_path_override=tmp_state)
        out.append(buf.getvalue().strip())
        return code, out[0]

    try:
        _CLOCK = lambda: t[0]
        _STATE_PATH = tmp_state

        # ① user 打点
        code, out = run(["user"])
        state = load_state(tmp_state)
        checks.append(("① user 打点", code == 0 and out == "OK" and state["lastUserAt"] == t[0]
                       and state["emptyFires"] == 0 and state["paused"] is False))

        # ② 119 秒 → QUIET，状态一字未变
        before = load_state(tmp_state)
        t[0] += 119
        code, out = run(["fire", "119"])
        after = load_state(tmp_state)
        checks.append(("② 119 秒 QUIET 且状态不变", out == "QUIET" and after == before))

        # ③ 恰好 120 秒 → OK（她 119 秒内没说话，且上次 fire 之后她回过话）
        t[0] += 1
        code, out = run(["fire", "120"])
        state = load_state(tmp_state)
        checks.append(("③ 120 秒 OK", out == "OK" and state["emptyFires"] == 0
                       and state["lastFireAt"] == t[0]))

        # ④ 没人回应：第一次 EMPTY 1、第二次 EMPTY 2
        t[0] += 300
        code, out1 = run(["fire"])
        state1 = load_state(tmp_state)
        t[0] += 300
        code, out2 = run(["fire"])
        state2 = load_state(tmp_state)
        checks.append(("④ EMPTY 1 → EMPTY 2", out1 == "EMPTY 1" and out2 == "EMPTY 2"
                       and state1["emptyFires"] == 1 and state2["emptyFires"] == 2))

        # ⑤ 中间 user 过则归零
        t[0] += 10
        run(["user"])
        t[0] += 200
        code, out = run(["fire"])
        state = load_state(tmp_state)
        checks.append(("⑤ user 后归零再 OK", out == "OK" and state["emptyFires"] == 0))

        # ⑥ paused 输出 PAUSED 且不计数
        run(["pause"])
        before = load_state(tmp_state)
        t[0] += 300
        code, out = run(["fire"])
        after = load_state(tmp_state)
        checks.append(("⑥ paused 不计数", out == "PAUSED" and after["emptyFires"] == before["emptyFires"]
                       and after["lastFireAt"] == before["lastFireAt"]))
        run(["resume"])
        checks.append(("⑥b resume 清 paused", load_state(tmp_state)["paused"] is False))

        # ⑦ 状态文件落在仓库外
        checks.append(("⑦ 状态文件在仓库外", REPO_ROOT.resolve() not in tmp_state.resolve().parents
                       and STATE_FILE.resolve() != tmp_state.resolve()))

        # 附带：拒绝把状态写进仓库
        try:
            _assert_outside_repo(REPO_ROOT / "dist" / "idle.json")
            refused = False
        except SystemExit:
            refused = True
        checks.append(("⑦b 仓库内路径被拒", refused))
    finally:
        _CLOCK, _STATE_PATH = real_clock, real_path
        try:
            for child in sorted(tmpdir.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                else:
                    child.rmdir()
            tmpdir.rmdir()
        except OSError:
            pass

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    print(f"idle selftest {len(checks) - len(failed)}/{len(checks)}")
    return 0 if not failed else 1


def main(argv: list[str] | None = None, state_path_override: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="自启停静默门（120 秒窗口，见 911）")
    parser.add_argument("--dry", action="store_true", help="只算不写状态文件")
    parser.add_argument("--selftest", action="store_true", help="假时钟 + 临时状态文件自检")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("user", help="她发真消息时打点")
    p_fire = sub.add_parser("fire", help="到点先跑这个")
    p_fire.add_argument("seconds", nargs="?", type=float, help="距上条消息经过的秒数")
    sub.add_parser("pause", help="闭嘴")
    sub.add_parser("resume", help="解禁")
    sub.add_parser("status", help="打印状态 JSON")
    sub.add_parser("reset", help="清空状态")
    args = parser.parse_args(argv)

    global _STATE_PATH
    if state_path_override is not None:
        _STATE_PATH = state_path_override
    elif args.selftest:
        return selftest()

    if args.cmd is None:
        parser.print_help()
        return 0

    _assert_outside_repo(_state_path())
    state = load_state()

    if args.cmd == "user":
        return cmd_user(state, args.dry)
    if args.cmd == "fire":
        return cmd_fire(state, args.seconds, args.dry)
    if args.cmd == "pause":
        return cmd_pause(state, args.dry)
    if args.cmd == "resume":
        return cmd_resume(state, args.dry)
    if args.cmd == "status":
        return cmd_status(state)
    if args.cmd == "reset":
        return cmd_reset(state, args.dry)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
