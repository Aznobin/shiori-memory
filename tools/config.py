"""记忆模块的路径与打包配置：所有脚本只从这一处取路径，不再写死。

用法：
    from config import cfg
    cfg["workspace"]        # 笔记库绝对路径
    cfg["python"]           # 本机 python 解释器
    cfg.path("home")        # 同 cfg["home"]，取不到返回 ""
    cfg.reads("enabled")    # 「读 ≠ 用」两档的口径，配置里没写就用默认值

没有 memory.config.json 时退回 config.example.json（全空路径），
再退回环境变量 USERPROFILE 推断主目录。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT / "memory.config.json"
EXAMPLE = ROOT / "config.example.json"

DEFAULTS = {
    "version": "0.0.0",
    "paths": {},
    "placeholders": {},
    "pack": {
        "excludeDirs": ["archive", "__pycache__", ".git"],
        "excludeFiles": [".style_override.json", "memdb.sqlite3"],
        "regenerateCatalog": True,
    },
    "git": {"name": "memory-bot", "email": "bot@local"},
    "diary": {
        # 日记文件名形如 "<日记前缀> %Y年%m月%d日.md"，放 <workspace>/<日记目录>/
        "dir": "日记",
        "prefix": "日记",
    },
    "reads": {
        # 「读 ≠ 用」第一档（2026-11-02 批6）：命中就算「读过」，取用才叫「用过」。
        # 读账是运行期数据，只落 state/reads.json；这里只管口径与上限，不写任何状态。
        "enabled": True,
        "maxFrags": 60,
        "keep": 4000,
    },
    "write": {
        # 写入侧的四道闸口径（2026-11-02 批6）：证据锚 + 审计层三件。
        # 这里只管口径，运行期数据一律落 state/（write_idem.jsonl / write_receipts.jsonl
        # / recycle/<日期>.jsonl），不写任何状态。
        # evidence: enforce = 缺证据锚 / 对不上就拒；warn = 只提醒不阻断；off = 完全不管。
        "evidence": "enforce",
        "evidenceWindow": 3,      # 找 quote 时在 source_line 前后各看几行
        "evidenceMinQuote": 8,    # quote 至少几个字
        "idempotency": True,      # 幂等键闸（同 key 重放 = replay；同 key 异内容 = 整批零写）
        "revision": True,         # 并发令牌闸（expected_revision 不符 = 整批零写）
        "recycle": True,          # 软删回收站（retire 前先把原行抄进 state/recycle/）
    },
}


def _load(path: Path) -> dict:
    try:
        # 2026-10-24：必须容忍 BOM。Windows PowerShell 5.1 的 `Set-Content -Encoding UTF8`
        # 会写出带 BOM 的 JSON（setup.ps1 早先就这么干的），而 `json.loads` 遇到 \ufeff
        # 直接抛「Unexpected UTF-8 BOM」，于是每个脚本都退化成「路径全空」，
        # 报出来的却是「先填 paths.workspace」这种误导性文案。
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _build() -> dict:
    src = REAL if REAL.exists() else EXAMPLE
    data = _load(src)
    merged = {
        "version": data.get("version") or DEFAULTS["version"],
        "paths": dict(DEFAULTS["paths"]),
        "placeholders": dict(data.get("placeholders") or {}),
        "pack": {**DEFAULTS["pack"], **(data.get("pack") or {})},
        "git": {**DEFAULTS["git"], **(data.get("git") or {})},
        "diary": {**DEFAULTS["diary"], **(data.get("diary") or {})},
        "reads": {**DEFAULTS["reads"], **(data.get("reads") or {})},
        "write": {**DEFAULTS["write"], **(data.get("write") or {})},
    }
    merged["paths"].update(data.get("paths") or {})
    if not merged["paths"].get("home"):
        merged["paths"]["home"] = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return merged


cfg = _build()


def diary(key):
    """日记目录名 / 文件名前缀（`dir` / `prefix`），配置里没写就用默认值。"""
    return (cfg.get("diary") or {}).get(key) or DEFAULTS["diary"][key]


def git_identity():
    """这台机器上记忆库的提交身份；配置里没写就用默认值。"""
    return (cfg.get("git") or {}).get("name") or DEFAULTS["git"]["name"], \
           (cfg.get("git") or {}).get("email") or DEFAULTS["git"]["email"]


def reads(key):
    """「读 ≠ 用」两档的口径（`enabled` / `maxFrags` / `keep`）；配置里没写就用默认值。

    这里只读配置，不碰 state——读账文件本身由 tools/decay.py 管（state/reads.json）。
    """
    return (cfg.get("reads") or {}).get(key, DEFAULTS["reads"][key])


def write(key, default=None):
    """写入侧四道闸的口径（`evidence` / `evidenceWindow` / `evidenceMinQuote` /
    `idempotency` / `revision` / `recycle`）；配置里没写就用默认值。

    这里只读配置，不碰 state——幂等账、回执、回收站都在 state/ 下，由 tools/memwrite.py 管。
    """
    value = (cfg.get("write") or {}).get(key)
    if value is None:
        return DEFAULTS["write"].get(key, default)
    return value


class _Config:
    def get(self, key, default=""):
        return cfg["paths"].get(key) or default

    def __getitem__(self, key):
        return self.get(key)

    def path(self, key, must_exist: bool = False):
        value = self.get(key)
        if must_exist and value and not Path(value).exists():
            return ""
        return value

    def placeholder(self, key):
        return cfg["placeholders"].get(key, "<" + key + ">")

    def __repr__(self):
        return "config(" + ", ".join(k + "=" + (v or "-") for k, v in cfg["paths"].items()) + ")"


if __name__ == "__main__":
    print("config file:", REAL if REAL.exists() else EXAMPLE)
    print("version:", cfg["version"])
    for k, v in cfg["paths"].items():
        print(f"  {k:10s} {v or '(未设置)'}")
    print("pack:", json.dumps(cfg["pack"], ensure_ascii=False))
