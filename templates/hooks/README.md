# memory-recall 钩子备份（另一台电脑用）

> 2026-10-23 从本机拷出来的**三份宿主壳**。真正的引擎不在这里，是 `tools/recall.py`（已在 git 里），
> 壳只是「怎么把用户那句话喂给引擎」。

## 这三份是什么

| 文件 | 原位置 | 谁读它 |
| --- | --- | --- |
| `codex-hooks.json` | `%USERPROFILE%\.codex\hooks.json` | Codex 原生钩子；**DSH 的 `dsh-hooks-codex` 桥也读这一份** |
| `opencode-memory-recall.js` | `%USERPROFILE%\.config\opencode\plugins\memory-recall.js` | OpenCode 插件 |
| `opencode-memory-recall.config.json` | `%USERPROFILE%\.config\opencode\plugins\memory-recall.config.json` | 上面那个插件的配置（python 路径 + recall.py 路径） |

`dsh-cordis-insert.yml` 是 DSH 侧要贴进 profile `cordis.patch.yml` 的那一段（不是 bundle，装包 ≠ 挂上）。

## 怎么装回去（另一台电脑）

**别手抄这三份**，路径里带用户名，抄过去就错。正确顺序：

1. 先把记忆库本身拉下来（`user-memory` 整个仓库，含 `tools/`）
2. 跑生成器，它会按**本机**的真实路径重写这三份：
   ```
   python tools/install_hooks.py
   ```
   输出会打印 `codex / opencode` 三个落点 + DSH 那段 YAML
3. DSH 侧：把打印出来的 `insert:` 段贴进 `<home>\.dsh\profiles\desktop\cordis.patch.yml` 末尾，**重启 GUI**
4. 验证：`python tools/hookcheck.py`

所以这三份备份的价值是**对照**（本机长什么样、命令怎么写才对），不是直接覆盖用的。
真要直接覆盖，覆盖完必须改掉里面的 `%USERPROFILE%\...`。

## DSH 侧还要装两个包（不装 = 登记了也不跑，`fiberPhase: null`）

`where pnpm` 在本机找不到，用宿主自带的（11.28.5，不再支持 `--frozen-lockfile`，改叫 `trust-lockfile`）：

```
cd <home>\.dsh\profiles\desktop
node "<DeepSeek Harness>\resources\runtime\primary-runtime\dependencies\pnpm\bin\pnpm.cjs" add @deepseek-ai/dsh-hooks-codex@0.2.1-rc.2
node "<...>\pnpm.cjs" add @deepseek-ai/dsh-hook-protocol@0.2.1-rc.2
```

**必须 add 两次**：第二次会把 hooks-codex 当「多余依赖」摘掉，两条都得留在 `package.json` 里。

## 全部自写钩子就这一套（2026-10-23 核过）

`~/.dsh/profiles/desktop/node_modules` 里 22 个 dsh 包**全部**来自 npm / GitHub（都有 `repository` 字段），
没有本地自写插件包。所以「自写钩子」= 记忆库这套 memory-recall，壳在 `templates/hooks/`，引擎在 `tools/recall.py`。
`cc-safety-net` 是第三方（`kenryu42/cc-safety-net`），`dsh-hooks-codex` / `dsh-hook-protocol` 是官方包，都不算自写。

## 2026-10-22 那次炸的教训（别重犯）

- `install_hooks.py` 的 `quote()` **必须用单引号**包 exe（`& 'C:\...\python.exe' 'C:\...\recall.py'`）。
  早一版用双引号，DSH 走 PowerShell 时内层引号被吞 → `exit 1` + `ParserError` → hook 协议记 `decision: pass` 静默吞，看起来像「没注入」，实际是命令根本没跑起来。
- `hooks.json` 只在 `apply()` 启动时 `readFileSync` 一次；**改命令内容也要重载**，不是只改 patch 才要重启。
  不重启的办法：`plugin_manager set_plugin include:memory-recall-hooks enabled:false` 再来一次 `true`。
- 不许拿 `set_plugin ... enabled:false` 当热重载去点 `sandbox-policy` / `shell` / `permission`：那是别人的依赖 fiber，disable 会把会话当场 dispose。

详见 `references/917-hook通道与验收.md`。
