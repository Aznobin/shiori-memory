/**
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
