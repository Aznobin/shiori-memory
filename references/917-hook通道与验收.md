---
id: 917
title: hook 通道与交付验收
keywords:
  - hookcheck
  - 验收
  - hook/result
  - sandbox-policy
  - cordis.patch.yml
  - 通道
  - 静默吞
sensitive: false
always: false
updated: 2026-11-02
distribute: keep
relates_to: []
---

# hook 通道与交付验收

> 从 `908-记忆自动注入.md` 拆出来的（2026-10-20，`908` 回 120 行线内）。**引擎本身（闸门、怎么挂、运维）在 `908`；这一份只记 DSH 那条通道怎么接、怎么验收、我在验收上栽过的每一次。**

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

- **配对不能用固定时间窗**（2026-10-20 复现过三处误判）：原先用最后一条 hook 前后 ±120 秒，结果 `turn 19`（19:03:22）配到了 `19:04:41` 的卡（那是 `turn 20` 的）、`turn 20` 配到 2 张、**最后那条 19:13:20 配到 19:11:48 的卡，明明 `gate:wait` 却印「四件齐了」**。改成 `card.time >= hook.time and < 下一条 hook.time` 才对。
- **这个工具回答的是「最近这一轮交没交」，不是「hook 通不通」**（同一轮指出，很关键）：同一条会话隔四分钟再跑，结论就翻面（我自己就拿着旧的「四件齐」预期去验收，结果变成 `exit 2`）。现在输出里固定加一行提示把两件事分开。
- **判交付认 `data.source.kind == "hooks-codex"`，这个字段真的存在**：实测 `ab836630` 有 4 条、4 张卡；`d966adaf` 3 条、3 张卡；`8894a112` 1 条、1 张——**1:1 对应**，而且不会误命中 subagent 的结案报告。
- **纯靠文本判一定会假阳性**：先用 `"记忆·按需注入" in text`，结果 subagent 的结案报告因为引用了这个标记被当成注入卡；改 `startswith("【记忆·按需注入】")` 才挡住。现在两者并用：`source.kind` 做主判据，`startswith` 只作为「文本是否带标记」的二次提示打给人看。
- **条数判据必须乘上 point 数**：`hooks.json` 只登记 1 条 `UserPromptSubmit` 时，`len(rows) % 1 == 0` 恒真 —— 上一版等于没查（验收时发现）。改成按 point 分组核对。
- **归属问题无解**：`hook/invoked` 事件里没有 `command` 字段（`dsh-hooks-codex/lib/index.js:149-155` 的 `appendHookInvoked` 只写 `turn/point/dialect/handlerId/matcher`），工具没法区分哪条 `0` 是探针、哪条是 recall。所以探针只能删掉，别留着当"辅助验证"——它只会污染判据。

## 3–5、7、9、10 已移出（2026-11-02） <!-- S:caption=事故与验收档案整批移出 -->

**原 §3–§5（判据踩坑 / 一次真交付 / 别再拿这些当证据）、§7 实测窗口、§9 挂 hook 崩溃定案、§10 钩子壳备份整节移到 `917a-hook 事故与验收档案.md`**（2026-11-02，本文件破 120 行红线，见 `902` §6）：事故记录、证据链、崩溃排查与备份清单全在那份；机制留在这里——§1 挂载、§2 验收命令、§6 通道≠交付、§8 本机装法。

## 6. 「通道通了」≠「记忆交付了」

- `recall.py` 在闸门拦下（默认每 4 轮才注入一次）、或一条都没命中时，**照样 `exit 0`、照样落状态文件，只是 stdout 一个字节都没有**。
- 所以只看 exitCode + 状态文件，证明的是「进程起来了」，从来没证明「它读到了 prompt、算出了片段、把文本交回去了」。
- 修法：`recall.py` 的 `cmd_hook` 每轮往状态文件写一条 `log`（`at` / `turn` / `q` / `gate` / `why` / `items` / `chars`），**空手也留痕**；`hookcheck.py` 读这本账，结案句分两句说——「四件齐了」／「通道通了，但**这一轮没交付**」。
- 判据里 `gate:*` 归为「设计行为不算故障」，但**绝不等于「已交付」**。这两个概念在结案句里必须分开，不然又是不说实话。

## 8. 本机装法（2026-10-22，与 §1「宿主自带」不同）

- 本机宿主是 `@deepseek-ai/dsh-desktop-runtime` **0.2.1-rc.2**，`app.asar` 里**没有** `dsh-hooks-codex` / `dsh-hook-protocol`（§1 的「宿主自带」只对 0.2.0-rc.2 那份包成立，别照搬）。**必须往 profile 装包**，否则插件登记了也不跑：`fiberPhase: null` = 模块解析不到。
- 装法（`where pnpm` 找不到，只能用 bundled 的；workdir `%USERPROFILE%\.dsh\profiles\desktop`）：
  `node "%USERPROFILE%\AppData\Local\Programs\DeepSeek Harness\resources\runtime\primary-runtime\dependencies\pnpm\bin\pnpm.cjs" add @deepseek-ai/dsh-hooks-codex@0.2.1-rc.2`
  **再单独 add 一次** `@deepseek-ai/dsh-hook-protocol@0.2.1-rc.2`——第二次 add 会把 hooks-codex 当「多余依赖」摘掉（`- @deepseek-ai/dsh-hooks-codex`），两条必须都在 `package.json` 里。改前备份 `package.json.bak-hookfix` + `pnpm-lock.yaml.bak-hookfix`。
- 这版 pnpm 是 **11.28.5**，没 `--no-frozen-lockfile`（报 `[ERROR] Unknown option: 'frozen-lockfile'`，改名 `trust-lockfile` 了），直接 `add` 即可。
- `plugin_manager install_bundle` 对 hooks-codex 报 `{"code":"not-bundle"}`（没有 `dsh.bundle`），**只能靠 patch insert 挂**；装包成功 ≠ 挂上。
- **核心 fiber 不许拿 disable 当重载手段**：`set_plugin include:sandbox-policy enabled:false` 会让 shell/sandbox 当场 disposed（`turn/end reason: disposed`），并把 patch 落成 `disabled: true` 把 `danger-full-access` 修复反掉。要重载只 toggle 非核心的 `include:memory-recall-hooks`，或直接重启 GUI。
