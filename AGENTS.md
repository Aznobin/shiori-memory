# AGENTS.md（记忆库入口）

> **这份文件放在你的工作目录根。** 它自己不装任何记忆，只写「库在哪、怎么读、怎么写」。
> 每次新开会话进来，先按 §1 做，不要凭印象。

## 0. 库在哪

- 记忆库：`<记忆库>`（就是 `SKILL.md` 所在的目录，绝对路径已填好；别照抄别人的路径）
- 真源：库根 `references/`——你自己的内容都在这
- 派生：`fragments/`（检索单元）、`tools/memdb.sqlite3`（全文索引）、`state/decay.json`（冷热）；**每次重建，别手改**
- 主文件：库根 `SKILL.md`（协议 + 用法，唯一必读，先读它）
- **包内没有 `.git`**：`memgit.py check` 会打一行「不是仓库，跳过比对」，这是正常形态，不是坏了
- 想给库做版本控制：自己在库根 `git init`，只本地 commit；不要 `git push` 到别人的仓库

### 0.1 模块数有三种口径，别混着说

- `frag.py stats` 报的是**两个数**：「模块 23 个（有片段 22）」——23 是按 frontmatter 数出来的模块文件，22 是这一轮真切出片段来的
- `frag.py build` 只报「有片段」那个数（「22 个模块 → 193 个片段」）
- `references\*.md` 的文件数会更大：还含 `_待确认` `_模板` `_路由详表` `CATALOG` `变更日志` `_巡检记录` 这些非模块文件（干净库实测 21 个带号文件）
- `CATALOG.md` / 路由表统计的：按登记过的算，一般跟 stats 的 23 一致

被问到「有多少模块」时，先说清按哪个口径数的，否则三个数字互相打架。

### 0.2 Windows 上的执行环境（不踩这四条会白忙）

- **控制台默认 GBK，中文会变乱码**。每条命令开头固定这两行：

```powershell
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$env:PYTHONIOENCODING='utf-8'
```

- **别用 `Select-Object -First N` 截 python 输出**：管道提前关闭，python 报 broken pipe、以 `[exit code: 1]` 收场，看着像脚本炸了其实命令是好的。要截用 `Select-Object -Last N`，或者不截
- 长路径先赋变量再拼接（`$L='<记忆库>'`），别在命令里反复写全路径
- 跑 `.ps1`：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File <脚本>`（有的环境没有 `pwsh` 命令，只有 `powershell.exe`）
- `setup.ps1` 跑完**已经建过派生层**（`fragments\index.json`、`tools\memdb.sqlite3`、`state\decay.json` 都在），一打开不是「没索引」的状态；但每轮开工照样先 `frag build` 刷一遍

## 1. 开局六步

**下面所有 `python tools/...` 都在库根（`<记忆库>`）下跑，不是在工作区**——工作区里没有 `tools\`，在那边跑第一条就是 `can't open file`。

```bash
python tools/memgit.py check          # 1. 库状态（没仓库就「跳过」，正常）
python tools/update.py hint           # 2. 骨架有没有新版（一行结论，念给用户听；同一版本只念一次）
python tools/frag.py build            # 3. 建/刷新索引（改了模块必跑）
python tools/frag.py search 关键词      # 4. 按这一轮的实际话题查
```

5. 按 `search` 给出的路径**只读命中的那一个**模块（或片段文件），读完再说话
6. 零命中就说「没记」，不要凭印象编

`hint` 没吐东西就是没有新版，一个字都不用提；吐了就把那一行原话念给用户，让他自己决定升不升——不要自作主张跑 `apply`。

**两种检索入口别混**：`memread.py "话题"` 是模块级整篇（命中一个大类就把那份全读进来，适合「关于我你知道什么」）；`frag.py search 关键词` 是片段级（精确定位到某一句，适合「接口联调怎么解决的」）。日常先用 `frag.py search`，要整篇背景再用 `memread.py`。

## 2. 什么时候写

- 用户的偏好变了、纠正了你、项目有进展、聊到了能长期复用的背景——**当场写**，不等他说「记住」
- 写入走程序，**不要手改模块正文**：

```bash
python tools/memwrite.py 请求.json --changelog "一句话摘要"
```

- 请求格式：`[{"module":"00","op":"add","keys":["名字"],"section":"我是谁","body":"……","date":"2026-10-24","source":"user-original"}]`
- **`source` 四档，写关于人的内容（`00` / `01` / `31`）时必须带**，不带就整批拒：`user-original`（用户本人说的/写的，**默认不标**）/ `test-transcript`（演练、别人转述，行尾标「测试记录，非本人原话」）/ `sample`（样本，标「（样本）」）/ `unknown`（查不出出处，标「（来源不明）」+正文写清为什么）。这四档不是格式洁癖——2026-10-24 就踩过：把测试员（lead）说的三条边界当成用户性格写进 `31`，链子不缺环、是指错了人
- 它会过闸门（模块号 / 长度 / 关键词 / 日期）、插到指定小节、改 `updated`、重建索引（约 0.4–0.5 s）
- `section` 写了个该模块还没有的小节：**会自动补 `## 小节名`** 再写进去，不用先手动建
- `--changelog "一句话"` 才会自动补 `变更日志` 一行；不带就自己补，别让它空着
- 第一次写的空模块，开头那句「> 这个模块是空的…」会被自动删掉，不用管
- 新建模块：`python tools/memwrite.py --new-module 07-项目`（同时刷路由表和 CATALOG）
- 拿不准的推测先扔 `references\_待确认.md`（文件末尾一条 bullet），别当事实用
- 每天收尾跑一次结算（幂等，跑多次只算一次）：`python tools/tick.py`

## 3. 批量导入（喂文件）

把一堆 md / txt 笔记扫成记忆草稿，**先看草稿再决定写不写**：

```bash
python tools/import_notes.py <目录> --show 5          # 预览
python tools/import_notes.py <目录> --write           # 草稿落 state/imports/
python tools/import_notes.py --apply <草稿文件>        # 把标了 OK 的写进模块
```

草稿是一张表：`# | 日期 | 模块 | keys | 内容 | 来源 | 状态`。审的时候：

- **只有「状态」列改成 `OK` 的行才写进去**（`待审` / `低置信` 都不动）。就是把那格文字换成 `OK`，没有别的命令
- **路由明显错了就改「模块」列**：内容讲项目被塞进课表，就把 `12b` 改成 `07`——`--apply` 读的就是这一列
- **目标模块必须已经存在**：被闸门拒「模块号 '07' 不在现有白名单里」时，① 改用已有的模块号，或 ② 先 `--new-module 07-项目` 再回来改
- 拿不准的把模块列写 `_待确认`，它会真的落进 `references\_待确认.md`，不会被静默丢掉
- 日期：文件名里的日期 > frontmatter 的 `date`/`updated` > 文件修改时间
- 「来源」列（`文件名:行号`）会跟着进条目末尾变成「（源：…）」，以后能追回原文件
- `--apply` 默认把导入的条目当 `user-original`（你自己写的笔记）；扫的是演练稿、别人给的文件，加 `--source sample` 或 `--source unknown`（`00`/`01`/`31` 没有 source 一律被闸门拒，这条别硬来）
- `--apply` **不写 `变更日志`**，要留痕自己补一行；但写完会自动重建索引

## 4. 不要做

- 不要整库读：先 `search`，再读单个文件
- 不要手改 `fragments\`：下次 `build` 全删重建，改了等于没改
- 不要把记忆里的进度当现状：说「现在怎样」之前先核实
- 不要在回复里报「我读了什么规则」，读完直接按规则办事

## 5. 路由表

分类表由程序生成，别手写：

```bash
python tools/routes.py      # 扫你的模块，改写下面这个块
```

<!-- ROUTES:BEGIN -->
<!-- 还没生成。跑 python tools/routes.py 之后，这里会出现分类表。 -->
<!-- ROUTES:END -->

## 6. 升级骨架

骨架会更新，你的内容不会被动：

```bash
python tools/update.py check         # 有没有新版（24 小时问一次，没配更新源会优雅退出）
python tools/update.py apply --dry   # 先预演：说清会覆盖什么、留几个 .new，一个字不动盘
python tools/update.py apply         # 换骨架
python tools/update.py rollback      # 换坏了退回去（自动备份在 state/backup/）
```

三档待遇：框架件（`tools/` `bin/` `templates/` `setup.ps1` `SKILL.md`）**无条件覆盖**；结构件（`变更日志` `CATALOG` `_巡检记录` `_待确认` `_路由详表` `_合并候选` `_模板`）**一个字节都不动**；协议件 `references/90`–`99` 冲突时写 `.new` 让你自己合（骨架那份是脱敏版，直接覆盖会把你写的原话换成「（已隐去）」）。

**升级器自己也在包里**：你第一次升级跑的是盘上那份旧 `update.py`，规则可能还是旧的。0.3.15 起 `apply` 结束时会发现它自己被换了，自动用新版重跑一遍；吃到 0.3.14 及更早的，升完再跑一次 `apply --dry` 确认。

## 7. 出问题怎么办

- 脚本报错、路径不对、闸门误拒：**原样记下来**（命令 + 报错 + 你当时想做什么），不要自己绕过闸门
- 不要为了「跑通」去改 `tools\` 里的脚本；要改就改源库再重新打包

## 硬塞层（每轮注入，由 `tools/agents_sync.py` 生成，**别手写**）

<!-- CORE:BEGIN -->
<!-- CORE:END -->
