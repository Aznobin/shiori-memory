---
id: 917a
title: hook 事故与验收档案
keywords:
  - 交付证据
  - 事故档案
  - hook 崩溃
  - 验收踩坑
  - 证据链
sensitive: false
always: false
updated: 2026-11-02
distribute: keep
relates_to: []
---

# hook 事故与验收档案

> 从 `917-hook通道与验收.md` 拆出来的事故与证据（2026-11-02，原文件 133 行破 120 红线）。机制与挂载还在 `917`（§1 挂载 / §2 验收命令 / §6 通道≠交付 / §8 本机装法）；这份只放事故记录：判据踩坑、真交付长什么样、什么不算证据、实测窗口、崩溃定案、钩子壳备份。**小节号沿用原 `917` 的编号**，旧引用直接对得上。

## 3. 判据写法踩过的五次坑（`hookcheck.py` 里逐版留了注释） <!-- S:caption=判据写法踩过的五次坑 -->

- v1 用整个 turn 判 → turn 横跨改动前后，误杀。
- v2 用「最后 n 条」判 → 从两条命令改成一条时那批里还夹着旧命令的 `1`，通了却报失败。
- v3 用「最后 2n 条」判 → 探针删了以后把历史算进来，同样误杀。
- v4 只看最后一条 + 状态文件 → 闸门拦下时也印「已落盘」，把「0 字」吞了（同一个病换了一件衣服）。
- v5 按 mtime 挑最近的状态文件 → 认错亲戚，别的窗口有交付、本会话 0 字也报绿灯；`delivered[0]` 还取的是字母序第一个。
- 最终口径：**通道**（exitCode / 本会话状态文件）与**交付**（日志里那张卡）分开判、分开说、退出码分开。

<!-- S:caption=一次真交付的四条证据（2026-10-20） -->
## 4. 一次真交付长什么样（2026-10-20 19:05，四条证据齐）

- `hook/result` 最后一条 `exitCode: 0`。
- `%TEMP%\memory-recall\ab836630-18d0-422e-ba3f-35982f01b387.json` 的 `log` 最后一条：`chars: 336`、`items: 2`、`why: topic-shift`。
- **会话日志里 19:05:49 出现 `user/message`，内容以 `【记忆·按需注入】（topic-shift，2 条）` 开头** —— 这是唯一能证明「宿主 adopt 了 additionalContext」的直接证据。
- `python tools\hookcheck.py --session ab836630` → `exit 0`，结案句「四件齐了」。

## 5. 别再拿这些当证据（2026-10-20 逐条驳过，留档防复发）

- `seen` 非空：只能推出 `emit()` 被调用过（`recall.py` 同一 if 分支），推不出模型收到了字。
- 状态文件里的 `turn` / `seen`：是**某一时刻的快照**，隔一会儿再读就变了；写进文档当「硬证据」等于用过期快照冒充现状。
- `durationMs` 几百毫秒：推不出「这条不是探针」——`hook/invoked` 里没有 `command` 字段，本来就没有归属信息。
- `decision: pass`：全挂的轮次也是 `pass`（turn 2 是 `exit 3221225794`、turn 3 是 `exit 1` + ParserError），这个字段永远别当判据。
- **`chars` 本身也证明不了交付**（2026-10-20 补的一刀）：`chars` 是 `len(card)`，写在 `%TEMP%` 一个普通文件里，只证明 `recall.py` 构造出了卡并写了 stdout，**证明不了宿主 adopt 了它**。手工 `recall.py hook --session <真 id>` 喂一句，所有字段看起来跟真跑一模一样。所以判据 4（日志里那张卡）才是唯一硬证据。
- **同一天我在这个字段上栽过一次，值得留档**：我先查 `data.message.source` 得到 86 条全空，据此在对话里**公开「纠正」，说她说的 `source.kind` 不存在**。真相是字段在 **`data.source`**，`message.source` 是另一层。教训：**结构没查清就否定别人的结论，比不查更糟**——不查只是不知道，乱纠正会把对的东西推倒。

## 7. 实测窗口（2026-10-20 19:33）

- `hookcheck.py` 跑出来：最后一条 `codex:UserPromptSubmit:99 exit=0`，本会话状态文件 19:32:08 更新到 `session-d966adaf-….json`，但**本轮交付 0 字**（`gate:wait` 闸门拦下）。
- 口径别再混：**通道通 ≠ 记忆进了上下文**。要验交付只看 `chars > 0` 的轮次；`gate:wait` 是设计行为不是故障。
- 这轮还发现 `turn 30` 条数不齐（`UserPromptSubmit 2/1`），属于闸门与 hook 触发次数的正常错位，不算异常。
- **闭环缺口**：通道已通（`exit=0`）但交付仍是 `0 字`，要等一次 `chars > 0` 才算真进上下文。

## 9. 「挂 hook 就会话崩溃」到底是谁干的（2026-10-22 查日志定案）

她要求（原话已移除）。翻遍 `%USERPROFILE%\.dsh\sessions\` 16 个会话逐条查 `turn/end`：

- **hook 命令失败本身不崩会话**：`session-0c06bffa` 里 11 次 `hook/invoked` 全败（9 次 `exitCode 3221225794` + 3 次 `exit 1`），它 turn 1～6 全是 `{kind:completed}`，一路聊到 turn 8。协议把失败记成 `decision: pass` **静默吞掉**，会话根本不知道。
- **真崩只有 2 次，全是 `aborted: disposed`**，而且都是同一个原因：我拿 `plugin_manager set_plugin include:sandbox-policy enabled:false` 当热重载按钮，拆了 shell/sandbox 的依赖 fiber。见 §7 第 3 条。
- **剩下那些「崩了」是重启**：`cordis.patch.yml` 和 `hooks.json` 都只在启动/`apply()` 时读，装或改就得重启 GUI，重启就掐掉正在聊的那个对话。这是设计，不是 hook 的 bug。 <!-- S:caption=cordis.patch.yml 改动会导致会话崩溃 -->
- **`interrupted` / `aborted: user` 不算崩**：那是按了停止。

### 规避清单（按成本从低到高）

1. **别为 `hooks.json` 重启**：`plugin_manager set_plugin include:memory-recall-hooks enabled:false` 再 `true`，只重跑这个非核心插件的 `apply()`，会重读 `hooks.json`，不碰会话。
2. **`cordis.patch.yml` 一次写全**：patch 层没有热重载，新增 insert 条目必然要重启一次；所以 `sandbox-policy` 的 `danger-full-access`、`configPath`、`defaultTimeoutMs` 该配的一次配齐，别反复回去加。
3. **核心 fiber 永远别 disable**：`sandbox-policy` / `shell` / `permission` 这类是别人的依赖，disable = 当场 disposed 且把 patch 写成 `disabled: true` 反过来咬你。
4. 真要重启，挑她不在说话的时候（`idle.py` 那套静默门同理）；会话历史落在 `session.v4.jsonl.zstd` 里，重启不等于内容没了。
5. 改完先 `python tools\hookcheck.py` 看退出码，再谈别的事——`exit 1` 是通道不通，`exit 2` 是通了没交付，都是 hook 自己的事，与会话稳定性无关。

## 10. 钩子壳的备份（2026-10-23，她要求（原话已移除））

- **「自写钩子」指的是 memory-recall 这一套**：她要求（原话已移除）就是这条注入链，不是别的插件。核过 `~/.dsh/profiles/desktop/node_modules` 里 22 个 dsh 包，**全部**带 `repository` 指向 npm / GitHub，**没有本地自写插件包**；`cc-safety-net` 是第三方、`dsh-hooks-codex` / `dsh-hook-protocol` 是官方包，都不算自写。
- **壳已进 git**：三份宿主壳 + 一段 DSH 挂载 YAML 拷进记忆库 `templates/hooks/`，跟仓库走，另一台电脑拉下来就有——
  - `templates/hooks/codex-hooks.json`（原 `~/.codex/hooks.json`）
  - `templates/hooks/opencode-memory-recall.js` + `templates/hooks/opencode-memory-recall.config.json`（原 `~/.config/opencode/plugins/`）
  - `templates/hooks/dsh-cordis-insert.yml`（贴 profile `cordis.patch.yml` 末尾那段 insert）
  - `templates/hooks/README.md`（装法 + 教训）
- **引擎不备份**：壳只是喂参数的薄层，真引擎是 `tools/recall.py`（已在 git 里）。所以另一台电脑**不许手抄这三份**——路径带用户名，抄过去必错；正确姿势是拉仓库后跑 `python tools/install_hooks.py`，它按本机真实路径重写三份，并打印 DSH 那段 YAML。
- **DSH 侧还要装两个包才真跑**（不装就 `fiberPhase: null`）：`@deepseek-ai/dsh-hooks-codex@0.2.1-rc.2` 和 `@deepseek-ai/dsh-hook-protocol@0.2.1-rc.2`，用宿主自带 pnpm（11.28.5，`--frozen-lockfile` 已改名 `trust-lockfile`），**必须 add 两次**——第二次会把 hooks-codex 当多余依赖摘掉。
- **目录来历**：`templates/hooks/` 是 2026-10-23 新建，此前 git 里只有 `bin/recall-hook.cmd`、`bin/recall-hook.sh`（无扩展名壳）和 `tools/install_hooks.py`（生成器）。
