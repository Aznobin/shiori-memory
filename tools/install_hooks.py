#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""install_hooks.py — 把「记忆按需自动注入」装到各家宿主（可移植、幂等）。

它只写两份宿主壳配置，核心引擎始终是 tools/recall.py：
  1. <home>/.codex/hooks.json      Codex 原生钩子；DSH 的 dsh-hooks-codex 桥也读它
  2. <home>/.config/opencode/plugins/memory-recall.js
     + memory-recall.config.json   OpenCode 插件

用法：
  python tools/install_hooks.py            # 安装/更新
  python tools/install_hooks.py --print-dsh # 只打印 DSH 侧要加的那几行 YAML

DSH 侧还要在 profile 的 cordis.patch.yml 里加一段 insert（--print-dsh 会给出），
因为它不是 bundle，装包之后必须显式挂载。
"""
import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MEMORY_ROOT = os.path.dirname(HERE)
TOOL = os.path.join(HERE, "recall.py")
HOME = os.path.expanduser("~")

OPENCODE_PLUGIN = r'''/**
 * memory-recall — OpenCode 插件壳（由 tools/install_hooks.py 生成）
 *
 * 每轮把用户最后一句话喂给 tools/recall.py，由引擎决定要不要注入。
 * 配置在同目录 memory-recall.config.json，可用环境变量 MEMORY_PYTHON /
 * MEMORY_RECALL 覆盖。
 */
import { execFileSync } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

function loadConfig() {
  let cfg = {};
  const p = join(HERE, "memory-recall.config.json");
  if (existsSync(p)) {
    try {
      cfg = JSON.parse(readFileSync(p, "utf8"));
    } catch {
      cfg = {};
    }
  }
  return {
    python: process.env.MEMORY_PYTHON || cfg.python || "python",
    tool: process.env.MEMORY_RECALL || cfg.tool || "",
    timeout: cfg.timeout || 20000,
  };
}

const TEXT_TYPES = new Set(["text", "reasoning"]);

function partsToText(parts) {
  if (!Array.isArray(parts)) return "";
  return parts
    .filter((p) => p && TEXT_TYPES.has(p.type) && typeof p.text === "string")
    .map((p) => p.text)
    .join("\n")
    .trim();
}

export const MemoryRecall = async () => {
  const cfg = loadConfig();
  const lastUserText = new Map();

  function recall(sessionID, text) {
    if (!cfg.tool) return "";
    try {
      const out = execFileSync(
        cfg.python,
        [cfg.tool, "hook", "--host", "plain", "--session", sessionID || "default"],
        {
          input: JSON.stringify({ session_id: sessionID, prompt: text }),
          encoding: "utf8",
          timeout: cfg.timeout,
          windowsHide: true,
        },
      );
      return (out || "").trim();
    } catch {
      return "";
    }
  }

  return {
    "chat.message": async (input, output) => {
      const sid = input?.sessionID || output?.message?.sessionID;
      const text = partsToText(output?.parts) || partsToText(input?.parts);
      if (sid && text) lastUserText.set(sid, text);
    },
    "experimental.chat.system.transform": async (input, output) => {
      const sid = input?.sessionID || "default";
      const text = lastUserText.get(sid);
      if (!text || !Array.isArray(output?.system)) return;
      const block = recall(sid, text);
      if (block) output.system.push(block);
    },
    dispose: async () => {
      lastUserText.clear();
    },
  };
};
'''


def quote(s):
    return "'%s'" % s.replace("'", "''")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        shutil.copyfile(path, path + ".bak")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def install_codex(py):
    cmd = "& %s %s hook --host codex" % (quote(py), quote(TOOL))
    cfg = {"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": cmd, "timeout": 20}]}
    ]}}
    path = os.path.join(HOME, ".codex", "hooks.json")
    return write(path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")


def install_opencode(py):
    base = os.path.join(HOME, ".config", "opencode", "plugins")
    p1 = write(os.path.join(base, "memory-recall.js"), OPENCODE_PLUGIN)
    cfg = {"python": py, "tool": TOOL, "timeout": 20}
    p2 = write(os.path.join(base, "memory-recall.config.json"),
               json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    return p1, p2


def dsh_snippet():
    cfg_path = os.path.join(HOME, ".codex", "hooks.json").replace("\\", "/")
    return (
        "# memory-recall: memory auto-injection via Codex-format hooks\n"
        "- insert:\n"
        "    - id: memory-recall-hooks\n"
        "      name: \"@deepseek-ai/dsh-hooks-codex\"\n"
        "      config:\n"
        "        configPath: %s\n"
        "        defaultTimeoutMs: 20000\n" % cfg_path
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-dsh", action="store_true")
    a = ap.parse_args()
    if a.print_dsh:
        print(dsh_snippet())
        return 0
    py = sys.executable
    print("python     :", py)
    print("engine     :", TOOL)
    print("codex      :", install_codex(py))
    p1, p2 = install_opencode(py)
    print("opencode   :", p1)
    print("opencode   :", p2)
    print("\nDSH 侧还需要在 profile 的 cordis.patch.yml 末尾加：\n")
    print(dsh_snippet())
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main())
