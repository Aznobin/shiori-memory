"""记忆模块的路径与打包配置：所有脚本只从这一处取路径，不再写死。

用法：
    from config import cfg
    cfg["workspace"]        # 笔记库绝对路径
    cfg["python"]           # 本机 python 解释器
    cfg.path("home")        # 同 cfg["home"]，取不到返回 ""

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
        "dir": "每日总结",
        "prefix": "日记",
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
