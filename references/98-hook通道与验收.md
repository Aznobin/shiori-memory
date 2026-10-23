---
id: "98"
title: hook 通道与交付验收
keywords:
  - hookcheck
  - 验收
  - 交付证据
  - hook/result
  - sandbox-policy
  - cordis.patch.yml
  - 通道
  - 静默吞
sensitive: false
always: false
updated: 2026-10-23
distribute: keep
relates_to: ["93"]
---

# hook 通道与交付验收

> 从 `93-记忆自动注入.md` 拆出来的（2026-10-20，`93` 回 120 行线内）。**引擎本身（闸门、怎么挂、运维）在 `93`；这一份只记 DSH 那条通道怎么接、怎么验收、我在验收上栽过的每一次。**

## 1. DSH 挂载（`hooks.json` + `cordis.patch.yml`）

- **2026-10-20 挂上，并当天翻车修正过一次**。`@deepseek-ai/dsh-hooks-codex@0.2.0-rc.2` 是**宿主自带**：它在 `app.asar` 内部 `/dsh/node_modules/@deepseek-ai/` 下，同目录还有它 peer 依赖的 `dsh-hook-protocol`，所以 profile 里**不用装 npm 包**，直接在 `cordis.patch.yml` 末尾 insert 就能解析。改完 `plugin_manager` 里 `include:memory-recall-hooks` 是 `enabled: true, fiberPhase: active`（patchId 同名），插件总数 210→211。
- **配置 active ≠ 真跑通（血的教训）**：一开始只验了「插件加载了 + 手工喂 payload 能吐 JSON」，就宣称「真的通了」，实际真实会话里一次都没执行过——`%TEMP%\memory-recall\` 里只有手工喂的文件。加 `cmd /c echo probe >> %TEMP%\hook-probe.log` 探针，探针文件压根不出现，才暴露。
- **两个真病根**：
  1. `runHook` 走 `ctx.shell` 直接调用（`dsh-hook-protocol` 的 `bash.execute(bash.resolve(request))`），落到部署层 `sandbox-policy` 的默认 **`read-only`**；Windows 受限令牌下 `pwsh` 直接 `exit 3221225794`（即 `0xC0000142`，DLL init failed）起不来。修法是在 `cordis.patch.yml` 补 `- id: sandbox-policy / name: "@deepseek-ai/dsh-sandbox-policy" / config: {mode: danger-full-access}`，跟会话预设对齐（改前备份 `cordis.patch.yml.bak-sandbox`）。
  2. 命令里用双引号包路径（`"C:\...\python.exe" "C:\...\recall.py" hook`），`pwsh -Command` 把首个 `"` 当成字符串起始、报 `ParserError`，`exit 1` 被 hook 协议记成 `decision: pass` **静默吞掉**。正确写法是调用运算符：`& 'C:\...\python.exe' 'C:\...\recall.py' hook --host codex`。
- 坑：`plugin_manager install_bundle` 对它会报 `{"code":"not-bundle"}`（不是 bundle），只能改 patch；patch 层**不进热重载**，必须重启 GUI 才加载（重启会掐掉当前会话，别在对话中途干）；改前先备份 `cordis.patch.yml.bak-memory-recall` / `.bak-sandbox`。
- `hooks.json` 只在 `apply()` 启动时 `readFileSync` 一次，**改 hook 命令内容也要重启 GUI**，不是只改 patch 才要重启。
- **怎么把内存里的旧快照换掉**：不用重启 GUI，`plugin_manager set_plugin include:memory-recall-hooks enabled:false` 再来一次 `true` 就会重新 `apply()` 并重读 `hooks.json`（2026-10-20 实测有效）。重启仍是最稳的办法，但不必再掐掉她的会话。
- **2026-10-22 又栽一次：hook 把自己装死**。她按本机 `install_hooks.py` 的方法装完后 DSH 直接停摆，复盘是三条链叠在一起：
  1. **工具没跟着改**：`install_hooks.py` 的 `quote()` 仍在用双引号包 exe（`"C:\...\python.exe" "C:\...\recall.py"`），DSH 走 PowerShell 时内层引号被吞，`exit 1` + `ParserError`，hook 协议记 `decision: pass` 静默吞——§1 第 2 条病根原样复发。只改过 `hooks.json`、没改生成器，重跑一次安装就回到旧写法。
  2. **无会话 → 部署默认沙箱**：hook 不带 session，`runHook` 走 `ctx.shell` 取 `sandbox-policy` 的部署默认 `read-only`，Windows 上 `exit 3221225794`（`0xC0000142` DLL init failed）；必须补 `mode: danger-full-access`（§1 第 1 条）。
  3. **拿 disable 当热重载，把运行中的会话拆了**（直接死因）：上一轮为了重载 `include:sandbox-policy` 调了 `plugin_manager set_plugin ... enabled:false`。它是 shell/sandbox 的依赖 fiber，disable 当场触发 `turn/end reason: {"kind":"aborted","reason":{"kind":"disposed"}}`，会话被 dispose；同时 patch 落成 `disabled: true`，把第 2 条刚补的 `danger-full-access` 反掉——越修越死。**核心 fiber 不许用 disable 当重载手段**：要重载就重启 GUI，或只 toggle 非核心的 `include:memory-recall-hooks`。
  4. **配置只在启动/`apply()` 时读**：`cordis.patch.yml` 与 `hooks.json` 都不是热配置；启动后改的文件不生效，必须重启。这次修完 03:42 重启，手测新命令 `exit 0`，stdout 是合法 `hookSpecificOutput` JSON。

## 2. 验收只认一条命令

`python tools\hookcheck.py [--session <id>]`，退出码就是判据：**0 = 四件齐**、**2 = 通道通了但本轮没交付**、**1 = 通道不通**。它查四件事：

1. 最后一条 `hook/result` 的 `exitCode == 0`；
2. **本会话**（按日志目录名对齐，不按 mtime 挑最近的）的状态文件在最后那条 hook 之后更新过；
3. 那轮 `log` 里的 `chars > 0`；
4. 会话日志里有由 hook 送进上下文的卡：`data.source.kind == "hooks-codex"`，且按**严格配对**落在「这条 `hook/result` 到下一条之间」的窗口里。

- **配对不能用固定时间窗**（小冬 2026-10-20 复现三处误判）：原先用最后一条 hook 前后 ±120 秒，结果 `turn 19`（19:03:22）配到了 `19:04:41` 的卡（那是 `turn 20` 的）、`turn 20` 配到 2 张、**最后那条 19:13:20 配到 19:11:48 的卡，明明 `gate:wait` 却印「四件齐了」**。改成 `card.time >= hook.time and < 下一条 hook.time` 才对。
- **这个工具回答的是「最近这一轮交没交」，不是「hook 通不通」**（小冬同一轮指出，很关键）：同一条会话隔四分钟再跑，结论就翻面（我自己就拿着旧的「四件齐」预期去验收，结果变成 `exit 2`）。现在输出里固定加一行提示把两件事分开。
- **判交付认 `data.source.kind == "hooks-codex"`，这个字段真的存在**：实测 `ab836630` 有 4 条、4 张卡；`d966adaf` 3 条、3 张卡；`8894a112` 1 条、1 张——**1:1 对应**，而且不会误命中 subagent 的结案报告。
- **纯靠文本判一定会假阳性**：先用 `"记忆·按需注入" in text`，结果 subagent 的结案报告因为引用了这个标记被当成注入卡；改 `startswith("【记忆·按需注入】")` 才挡住。现在两者并用：`source.kind` 做主判据，`startswith` 只作为「文本是否带标记」的二次提示打给人看。
- **条数判据必须乘上 point 数**：`hooks.json` 只登记 1 条 `UserPromptSubmit` 时，`len(rows) % 1 == 0` 恒真 —— 上一版等于没查（小冬指出）。改成按 point 分组核对。
- **归属问题无解**：`hook/invoked` 事件里没有 `command` 字段（`dsh-hooks-codex/lib/index.js:149-155` 的 `appendHookInvoked` 只写 `turn/point/dialect/handlerId/matcher`），工具没法区分哪条 `0` 是探针、哪条是 recall。所以探针只能删掉，别留着当"辅助验证"——它只会污染判据。

## 3. 判据写法踩过的五次坑（`hookcheck.py` 里逐版留了注释）

- v1 用整个 turn 判 → turn 横跨改动前后，误杀。
- v2 用「最后 n 条」判 → 从两条命令改成一条时那批里还夹着旧命令的 `1`，通了却报失败。
- v3 用「最后 2n 条」判 → 探针删了以后把历史算进来，同样误杀。
- v4 只看最后一条 + 状态文件 → 闸门拦下时也印「已落盘」，把「0 字」吞了（同一个病换了一件衣服）。
- v5 按 mtime 挑最近的状态文件 → 认错亲戚，别的窗口有交付、本会话 0 字也报绿灯；`delivered[0]` 还取的是字母序第一个。
- 最终口径：**通道**（exitCode / 本会话状态文件）与**交付**（日志里那张卡）分开判、分开说、退出码分开。

## 4. 一次真交付长什么样（2026-10-20 19:05，四条证据齐）

- `hook/result` 最后一条 `exitCode: 0`。
- `%TEMP%\memory-recall\ab836630-18d0-422e-ba3f-35982f01b387.json` 的 `log` 最后一条：`chars: 336`、`items: 2`、`why: topic-shift`。
- **会话日志里 19:05:49 出现 `user/message`，内容以 `【记忆·按需注入】（topic-shift，2 条）` 开头** —— 这是唯一能证明「宿主 adopt 了 additionalContext」的直接证据。
- `python tools\hookcheck.py --session ab836630` → `exit 0`，结案句「四件齐了」。

## 5. 别再拿这些当证据（小冬 2026-10-20 逐条驳过，留档防复发）

- `seen` 非空：只能推出 `emit()` 被调用过（`recall.py` 同一 if 分支），推不出模型收到了字。
- 状态文件里的 `turn` / `seen`：是**某一时刻的快照**，隔一会儿再读就变了；写进文档当「硬证据」等于用过期快照冒充现状。
- `durationMs` 几百毫秒：推不出「这条不是探针」——`hook/invoked` 里没有 `command` 字段，本来就没有归属信息。
- `decision: pass`：全挂的轮次也是 `pass`（turn 2 是 `exit 3221225794`、turn 3 是 `exit 1` + ParserError），这个字段永远别当判据。
- **`chars` 本身也证明不了交付**（小冬 2026-10-20 补的刀）：`chars` 是 `len(card)`，写在 `%TEMP%` 一个普通文件里，只证明 `recall.py` 构造出了卡并写了 stdout，**证明不了宿主 adopt 了它**。手工 `recall.py hook --session <真 id>` 喂一句，所有字段看起来跟真跑一模一样。所以判据 4（日志里那张卡）才是唯一硬证据。
- **同一天我在这个字段上栽过一次，值得留档**：我先查 `data.message.source` 得到 86 条全空，据此在对话里**公开「纠正」小冬，说她说的 `source.kind` 不存在**。真相是字段在 **`data.source`**，`message.source` 是另一层。教训：**结构没查清就否定别人的结论，比不查更糟**——不查只是不知道，乱纠正会把对的东西推倒。

## 6. 「通道通了」≠「记忆交付了」

- `recall.py` 在闸门拦下（默认每 4 轮才注入一次）、或一条都没命中时，**照样 `exit 0`、照样落状态文件，只是 stdout 一个字节都没有**。
- 所以只看 exitCode + 状态文件，证明的是「进程起来了」，从来没证明「它读到了 prompt、算出了片段、把文本交回去了」。
- 修法：`recall.py` 的 `cmd_hook` 每轮往状态文件写一条 `log`（`at` / `turn` / `q` / `gate` / `why` / `items` / `chars`），**空手也留痕**；`hookcheck.py` 读这本账，结案句分两句说——「四件齐了」／「通道通了，但**这一轮没交付**」。
- 判据里 `gate:*` 归为「设计行为不算故障」，但**绝不等于「已交付」**。这两个概念在结案句里必须分开，不然又是不说实话。

## 7. 实测窗口（2026-10-20 19:33）

- `hookcheck.py` 跑出来：最后一条 `codex:UserPromptSubmit:99 exit=0`，本会话状态文件 19:32:08 更新到 `session-d966adaf-….json`，但**本轮交付 0 字**（`gate:wait` 闸门拦下）。
- 口径别再混：**通道通 ≠ 记忆进了上下文**。要验交付只看 `chars > 0` 的轮次；`gate:wait` 是设计行为不是故障。
- 这轮还发现 `turn 30` 条数不齐（`UserPromptSubmit 2/1`），属于闸门与 hook 触发次数的正常错位，不算异常。
- **闭环缺口**：通道已通（`exit=0`）但交付仍是 `0 字`，要等一次 `chars > 0` 才算真进上下文。

## 8. <你的笔记本> 本机装法（2026-10-22，与 §1「宿主自带」不同）

- 本机宿主是 `@deepseek-ai/dsh-desktop-runtime` **0.2.1-rc.2**，`app.asar` 里**没有** `dsh-hooks-codex` / `dsh-hook-protocol`（§1 的「宿主自带」只对 0.2.0-rc.2 那份包成立，别照搬）。**必须往 profile 装包**，否则插件登记了也不跑：`fiberPhase: null` = 模块解析不到。
- 装法（`where pnpm` 找不到，只能用 bundled 的；workdir `%USERPROFILE%\.dsh\profiles\desktop`）：
  `node "%USERPROFILE%\AppData\Local\Programs\DeepSeek Harness\resources\runtime\primary-runtime\dependencies\pnpm\bin\pnpm.cjs" add @deepseek-ai/dsh-hooks-codex@0.2.1-rc.2`
  **再单独 add 一次** `@deepseek-ai/dsh-hook-protocol@0.2.1-rc.2`——第二次 add 会把 hooks-codex 当「多余依赖」摘掉（`- @deepseek-ai/dsh-hooks-codex`），两条必须都在 `package.json` 里。改前备份 `package.json.bak-hookfix` + `pnpm-lock.yaml.bak-hookfix`。
- 这版 pnpm 是 **11.28.5**，没 `--no-frozen-lockfile`（报 `[ERROR] Unknown option: 'frozen-lockfile'`，改名 `trust-lockfile` 了），直接 `add` 即可。
- `plugin_manager install_bundle` 对 hooks-codex 报 `{"code":"not-bundle"}`（没有 `dsh.bundle`），**只能靠 patch insert 挂**；装包成功 ≠ 挂上。
- **核心 fiber 不许拿 disable 当重载手段**：`set_plugin include:sandbox-policy enabled:false` 会让 shell/sandbox 当场 disposed（`turn/end reason: disposed`），并把 patch 落成 `disabled: true` 把 `danger-full-access` 修复反掉。要重载只 toggle 非核心的 `include:memory-recall-hooks`，或直接重启 GUI。

## 9. 「挂 hook 就会话崩溃」到底是谁干的（2026-10-22 查日志定案）

原话（已隐去）。翻遍 `%USERPROFILE%\.dsh\sessions\` 16 个会话逐条查 `turn/end`：

- **hook 命令失败本身不崩会话**：`session-0c06bffa` 里 11 次 `hook/invoked` 全败（9 次 `exitCode 3221225794` + 3 次 `exit 1`），它 turn 1～6 全是 `{kind:completed}`，一路聊到 turn 8。协议把失败记成 `decision: pass` **静默吞掉**，会话根本不知道。
- **真崩只有 2 次，全是 `aborted: disposed`**，而且都是同一个原因：我拿 `plugin_manager set_plugin include:sandbox-policy enabled:false` 当热重载按钮，拆了 shell/sandbox 的依赖 fiber。见 §7 第 3 条。
- **剩下那些「崩了」是重启**：`cordis.patch.yml` 和 `hooks.json` 都只在启动/`apply()` 时读，装或改就得重启 GUI，重启就掐掉正在聊的那个对话。这是设计，不是 hook 的 bug。
- **`interrupted` / `aborted: user` 不算崩**：那是按了停止。

### 规避清单（按成本从低到高）

1. **别为 `hooks.json` 重启**：`plugin_manager set_plugin include:memory-recall-hooks enabled:false` 再 `true`，只重跑这个非核心插件的 `apply()`，会重读 `hooks.json`，不碰会话。
2. **`cordis.patch.yml` 一次写全**：patch 层没有热重载，新增 insert 条目必然要重启一次；所以 `sandbox-policy` 的 `danger-full-access`、`configPath`、`defaultTimeoutMs` 该配的一次配齐，别反复回去加。
3. **核心 fiber 永远别 disable**：`sandbox-policy` / `shell` / `permission` 这类是别人的依赖，disable = 当场 disposed 且把 patch 写成 `disabled: true` 反过来咬你。
4. 真要重启，挑她不在说话的时候（`idle.py` 那套静默门同理）；会话历史落在 `session.v4.jsonl.zstd` 里，重启不等于内容没了。
5. 改完先 `python tools\hookcheck.py` 看退出码，再谈别的事——`exit 1` 是通道不通，`exit 2` 是通了没交付，都是 hook 自己的事，与会话稳定性无关。

## 10. 钩子壳的备份（2026-10-23，她说（已隐去））

- **「自写钩子」指的是 memory-recall 这一套**：她说的「就是hook」就是这条注入链，不是别的插件。核过 `~/.dsh/profiles/desktop/node_modules` 里 22 个 dsh 包，**全部**带 `repository` 指向 npm / GitHub，**没有本地自写插件包**；`cc-safety-net` 是第三方、`dsh-hooks-codex` / `dsh-hook-protocol` 是官方包，都不算自写。
- **壳已进 git**：三份宿主壳 + 一段 DSH 挂载 YAML 拷进记忆库 `templates/hooks/`，跟仓库走，另一台电脑拉下来就有——
  - `templates/hooks/codex-hooks.json`（原 `~/.codex/hooks.json`）
  - `templates/hooks/opencode-memory-recall.js` + `templates/hooks/opencode-memory-recall.config.json`（原 `~/.config/opencode/plugins/`）
  - `templates/hooks/dsh-cordis-insert.yml`（贴 profile `cordis.patch.yml` 末尾那段 insert）
  - `templates/hooks/README.md`（装法 + 教训）
- **引擎不备份**：壳只是喂参数的薄层，真引擎是 `tools/recall.py`（已在 git 里）。所以另一台电脑**不许手抄这三份**——路径带用户名，抄过去必错；正确姿势是拉仓库后跑 `python tools/install_hooks.py`，它按本机真实路径重写三份，并打印 DSH 那段 YAML。
- **DSH 侧还要装两个包才真跑**（不装就 `fiberPhase: null`）：`@deepseek-ai/dsh-hooks-codex@0.2.1-rc.2` 和 `@deepseek-ai/dsh-hook-protocol@0.2.1-rc.2`，用宿主自带 pnpm（11.28.5，`--frozen-lockfile` 已改名 `trust-lockfile`），**必须 add 两次**——第二次会把 hooks-codex 当多余依赖摘掉。
- **目录来历**：`templates/hooks/` 是 2026-10-23 新建，此前 git 里只有 `bin/recall-hook.cmd`、`bin/recall-hook.sh`（无扩展名壳）和 `tools/install_hooks.py`（生成器）。
