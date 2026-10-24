<h1 align="center">Shiori Memory 1.0.0</h1>

<p align="center">发布日期：2026-10-24</p>

第一个正式版。核心是「装好就能用」：解压、复制一个文件，剩下的它自己跑。

## 新增

### 记忆库本体

- **语气模块随包走**：每份包都带 `89-语气与交流`，开局必读。内含「去 AI 腔十条 + 发前三问」「语气模仿（聊天里学、写东西时用）」「人格：用户硬，你也硬」「禁止长篇大论」
- **`retire` 软删**：行尾挂「（已退休 日期：理由）」，正文一个字不删，切片自动跳过——纯文本没有容量压力，删等于丢
- **`import_notes.py` 导入器**：把日记、旧笔记转成记忆草稿，切块 → 清理 → 路由 → 落草稿；人工标 `OK` 后才写进库
- **`tick.py`**：一条命令跑完巡检；**`parity.py`**：比对 `.py` / `.ps1` 双份行为
- **`memwrite.py`** 成为写记忆的正路（payload → 闸门 → 定小节 → 改 `updated` → 补日志 → 重建索引），支持 `add` / `replace` / `retire` / `--new-module`
- **`memread.py`** 默认按模块边界截断（≤12000 字符 / ≤300 行），`--offset` 续读；读取口径统一成「整篇优先，不够再下沉片段层」

### 给普通用户的安全网

- **骨架禁区**：`tools\`、`bin\`、`templates\`、`framework-public\`、`AGENTS.md`、`SKILL.md`、`setup.ps1`、`config.example.json`、`references\9*.md` 属于作者，没有明确同意一个字都不许改
- **`SKELETON.sha256` + `skeleton_guard.py`**：67 项校验和，改过的 / 缺失的 / 多出来的全能列出来，而且它**不能自己重新盖章**
- **安全阀**：往外发数据、删重要文件、改坏库，三种情况先停手

### 解压即用

- 包根多放一份 `AGENTS.md`（以前只有 `templates/` 里那份，用户根本不知道复制哪个）
- `frag.py` 在缺索引时自动 build 一次——第一次问旧事就能用，不必先跑 `build`
- 路由表打包时自动填进包根 `AGENTS.md`，不再需要用户自己跑 `routes.py`

## 修掉的问题

- 分发包漏了开局必读的语气模块（装完只拿到机制、拿不到说话方式）
- 包根 `AGENTS.md` 的路由表是空的；路由表还点名了包里根本不存在的模块
- 分发包漏出私人标识：团队子智能体名、本机名、项目名、旧主机名、路径用户名
- `ENTRY.md`、`memory.config.json`、`frag_bench.json`（私人问法题库）、`__pycache__` 被误打进包
- `pack.py` 先删目录后校验，一个非法值就吃掉好包
- `memwrite --dry` 行数对不上（建小节时那 2 行被算进 `before`）
- 升级时跑的还是旧 `update.py`（鸡生蛋），现在会立刻用新版再跑一遍；`apply --dry` 可以先预演
- 写入闸门放行四值、写入端只支持两值
- 检索被协议模块压着：「用户」这类词永远先撞 90–99 机制模块，现在按主体分档
- 分发包的评测分数天生是假的（期望模块在包里不存在），现在按包内实际模块裁剪

## 包干净了

- `verify_pack`：P0 失败 0 · P1 失败 0
- 骨架校验 67 项全对；私人标识全包扫描 0 命中
- README 整篇重写，非 git 用户走 zip，不用装 git 也能用

## 分支

- `master`：发行版（别人下载这条）
- `main`：框架工作区

## 升级

```bash
python tools/update.py check         # 有没有新版
python tools/update.py apply --dry   # 先预演，一个字不动盘
python tools/update.py apply         # 换骨架
python tools/update.py rollback      # 换坏了退回去
```

升级只换骨架，不碰你的记忆正文：框架件无条件覆盖，结构件（`变更日志` / `CATALOG` / `_巡检记录` 等）一个字不动，`references/90`–`99` 冲突时写成 `.new` 由你合。

## 注意

- 目前只在 **DeepSeek Harness（DSH）+ Windows 11 + Python 3.12** 上验证过，别的助手要自己适配
- 第一次升级跑的是盘里那份旧 `update.py`，从 0.3.15 起会自动用新版重跑一遍；装的是更早版本的话，第一次升级完再跑一次 `apply --dry` 确认
