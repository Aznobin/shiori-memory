<h1 align="center">Shiori Memory</h1>

<p align="center"><b>Local-first memory for AI coding agents, natively inside DSH (DeepSeek Harness).</b></p>

<p align="center">No vector database. No API key. No extra service.</p>

<p align="center">
  <img alt="version" src="https://img.shields.io/badge/version-0.3.0-blue">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="python" src="https://img.shields.io/badge/python-%E2%89%A53.12-yellow">
  <img alt="platform" src="https://img.shields.io/badge/DeepSeek%20Harness-plugin-purple">
</p>

<p align="center">给 AI 编码助手用的纯 Markdown 长期记忆库：片段化存储、关键词路由、时间衰减、写入闸门。</p>

<p align="center">by Firin Chan & Aznobin</p>

> **适配范围**：目前只在 **DeepSeek Harness（DSH）** 上验证过（Windows 11 + Python 3.12），技能目录、脚本路径、注入方式都按 DSH 的规矩来。别的助手想用，得自己改；本库没有在别处验证过。

## 目录

- [这是什么](#这是什么)
- [它治什么毛病](#它治什么毛病)
- [五分钟跑起来](#五分钟跑起来)
- [目录长什么样](#目录长什么样)
- [片段是怎么回事](#片段是怎么回事)
- [工具都有哪些](#工具都有哪些)
- [记忆怎么不越堆越肿](#记忆怎么不越堆越肿)
- [隐私与安全](#隐私与安全)
- [装成技能用](#装成技能用)
- [框架版怎么取](#框架版怎么取)
- [授权](#授权)

## 这是什么

一句话：**把「记住关于你的那些事」拆成一个个小文件，开局只拿用得上的那几条，装进模型脑子里。**

平时 AI 助手最容易犯的毛病是**失忆**：换个对话就不认识你了，昨天说的偏好今天就忘。硬塞的办法是把所有资料一股脑塞进 prompt（提示词），结果又贵又容易被无关内容带偏。

这个库换个思路：资料按主题切成小碎片（fragment），配一份索引。需要什么查什么，一次只装几条。

- **零依赖**：不装向量库、不联网、不为写记忆额外跑模型，Python 标准库就够
- **纯文本**：全是 `.md`，你可以用任何编辑器改，也能进版本管理
- **可迁移**：把一个目录拷走就等于迁完
- **可分发**：有打包脚本，私人内容清空后别人拿到就是一份空骨架

## 它治什么毛病

| 毛病 | 现在的做法 |
| --- | --- |
| 整篇读进来之后开始编造 | 只给命中的片段，不给整篇 |
| 每次对话都重新烧一遍全文 | 索引先筛，只取几条 |
| 换台机器记忆就没了 | 一个目录拷走，重建两条命令 |
| 只加不删，越堆越大 | 时间衰减 + 冷热三态 + 手动归档 |
| 记忆写坏了没法回滚 | git 管着，随时 `git log` / `git revert` |

## 五分钟跑起来

```bash
# 1. 拿到框架版（空的，只有骨架和工具）
git clone https://github.com/Aznobin/shiori-memory.git
cd shiori-memory

# 2. 建索引：把每个片段的标题、关键词、日期抽出来
python tools/frag.py build

# 3. 查东西
python tools/frag.py search 笔记标签

# 4. 打包给模型看（模拟一次开局注入）
python tools/frag.py pack
```

跑完 `fragments/index.json` 就是索引，`search` 出来的结果带上片段编号和文件路径，直接读那一个文件就够。

## 目录长什么样

```text
SKILL.md            # 主入口：告诉模型先读哪、按什么路由
README.md           # 你正在看的这份
AGENTS.md           # 给编码助手的工作约定（可选，模板在 templates/）
references/         # 唯一的真源：按主题分的模块文件（01-99）
  _模板.md          # 新模块照这个写
  _待确认.md        # 没想好的先扔这里
  变更日志.md       # 改了什么，一条一行
  CATALOG.md        # 模块清单
  90-99             # 机制类：检索、打包、注入、安全、架构
templates/          # AGENTS.md / hooks / 各主机变体的模板
tools/              # 全部脚本，零第三方依赖
fragments/          # 自动生成，别手改
  index.json        # 索引：编号 / 标题 / 关键词 / 日期 / 状态 / part
  .dates.json       # 片段最后更新日期缓存
state/
  decay.json        # 衰减参数与计数（半衰期、冷热阈值、上次结算日）
dist/               # 打包临时产物，随时可删
```

**铁律**：`references/` 是唯一真源，`fragments/` 每次 `build` 全删重建。手改片段，下一轮就没了。

## 片段是怎么回事

`build` 会把每个模块按段落切开，一段一片，标题做文件名。规则很简单：**一段超过 4 行就在中间切**，切出来的兄弟片段标题尾部带 `（1）（2）（3）`。

于是有几种情况：

- 一段话被切成好几片时，它们在索引里共享同一个 `part.base`（去掉 `（n）` 的标题），各自带 `part.seq`
- `search` 默认把同一 `part.base` 的命中**折成一行显示**：`02a.002–02a.016〔同源 15 片 · 合计 1711 字〕`
- 想看细的就加 `--expand-parts`，恢复成逐条列出
- `pack` 会把相邻的同源片拼回整段再喂，但只拼到 12 行 / 600 字为止

这样检索单位够细（命中准），喂给模型的却是整段（不碎片化），两头都占。

## 工具都有哪些

| 命令 | 干什么 |
| --- | --- |
| `python tools/frag.py build` | 重建片段与索引 |
| `python tools/frag.py search 词` | 查片段，默认折叠同源 |
| `python tools/frag.py pack` | 打包成一份可以直接塞进 prompt 的文本 |
| `python tools/frag.py lint` | 体检：空标题、重复编号、坏元数据 |
| `python tools/frag.py stats` | 模块数、片段数、字数分布 |
| `python tools/memdb.py index` | 建 SQLite 全文索引（可选，提速用） |
| `python tools/decay.py --tick` | 跑一次时间衰减结算（同一天只算一次） |
| `python tools/decay.py --pairs` | 找「可能重复」的片段，只报不改 |
| `python tools/memwrite.py payload.json` | **写记忆的正路**：过闸门 → 插到指定小节 → 改 `updated` → 补日志 → build + index（`--dry` 只看不写，`--changelog "摘要"` 顺手记一行） |
| `python tools/write_gate.py` | 单独校验一批写入请求（`memwrite.py` 内部已经调它，手动跑是排查用） |
| `python tools/import_notes.py 目录` | **从旧笔记/日记里搬内容**：切块 → 清理 → 按关键词路由到大类 → 落成草稿。默认只扫描，`--write` 落 `state/imports/`，人工标 `OK` 后 `--apply` 才真写进分类模块（走 `memwrite` 同一条闸门）。`--selftest` 自检，`--show N` 看打头几条 |
| `python tools/framework.py --dry` | 打印将要发布的框架文件清单 |
| `python tools/framework.py --build` | 生成框架版并过隐私门 |
| `python tools/parity.py` | `tools/` 下同名 `.py` / `.ps1` 双份各跑一次，比 stdout 与退出码（只有成对且不一致才 exit 1；无对照只当信息行；默认跳过会写盘的 `pack` / `setup`，要跑加 `--include-heavy`） |
| `tools/check-modules.ps1` | 检查模块编号与清单一致性。**只有 ps1 版，无 py 对照**（跑：`pwsh -NoProfile -File tools/check-modules.ps1`）；`tools/` 下现在只剩这一个 ps1（`pack.ps1` / `memory-check.ps1` / `check-freshness.ps1` 2026-10-24 已删，回滚见 `变更日志`） |

## 从旧笔记搬内容（importer）

`tools/import_notes.py` 把散在别处的笔记（日记、旧笔记库、聊天导出）转成记忆草稿。

它**不写正文、不建模块**，只做四件事：切块 → 清理 → 路由 → 落草稿。

- 切块：认 `#` 标题、列表项、`>` 引用；过碎的合并，过长的切散，代码块里的行按原样保留但不算正文
- 清理：去 markdown 链接、去反引号、去重复空行、去 `Git: 1 次` 这类统计行和只有标签没有内容的半截行
- 路由：用现有关键词表命中大类；一个都没命中就进 `_待确认`，不硬塞
- 落草稿：写到 `state/imports/`，每条带 `trigger`（原始出处：文件 + 行号）和 `keys`（命中的词）

**草稿不审就是污染。** 人工打开草稿文件，在想要的条目上标 `OK`，其余留空，再跑 `--apply`——只有标了 `OK` 的才进分类模块，并且和手动写入走同一条闸门（模块号、片段体长度、关键词、日期格式全都要过）。

用法：

```bash
python tools/import_notes.py ~/笔记/日记            # 只扫描，报块数/草稿数/路由分布
python tools/import_notes.py ~/笔记/日记 --write    # 落草稿到 state/imports/
python tools/import_notes.py --apply                # 审完标 OK 之后，把 OK 的写进模块
```

实测一个 21 篇的日记目录：126 块 → 98 条草稿，路由 `07×54 · 11×19 · 08a×6 · 28×4`。

## 记忆怎么不越堆越肿

记忆库用久了必然肿，肿了就慢、就吵。这里靠四件事压着：

1. **时间衰减**：片段带 `updated` 日期，检索时乘一个 `0.7 + 0.3 × recency`，半衰期 90 天。老东西还在，只是不再抢前排。
2. **冷热三态**：`active` / `cold` / `archived`。长期没被召回的先降冷，再进归档；归档的不参与开局注入，但照样能搜到。状态写在 `state/decay.json`，`build` 不会把它冲掉。
3. **每日结算幂等**：衰减按天算，同一天跑十遍也只扣一次，不会因为开十个对话就把库清空。
4. **写入门（已接入 2026-10-24）**：`write_gate.py` 校验模块号、片段体长度、关键词、日期格式，不合格整批拒；`memwrite.py` 落盘前会先调它，不过闸门一条都不写。写入的正路是「产出 payload → 跑 `memwrite.py`」，不再手改模块文件。

## 隐私与安全

这个库的设计前提是：**它随时可能被公开**。所以：

- 私人内容（日记、健康、情绪、身份设定）单独成模块，发布脚本里有一道**隐私门**，扫到私人关键词就中止，不许先提交再修
- 框架版不含任何私人模块，`references/` 只留协议件和机制文档
- 拿到的框架版里 `references/01-89` 是空的，你需要自己往里填 —— 填什么由你决定

## 装成技能用（DSH）

这个目录本身就是 DSH 的技能包（skill）：`SKILL.md` 是入口文件，`references/` 是正文。`SKILL.md` 开头的 frontmatter（`name` + `description`）告诉 DSH 什么时候该调它。

```bash
# DSH 的技能目录
git clone https://github.com/Aznobin/shiori-memory.git C:\Users\<你>\.agents\skills\shiori-memory
```

放好之后不用额外注册，DSH 读到 `SKILL.md` 就知道什么时候用它；第一次用先跑 `python tools/frag.py build`。

**只做了 DSH 的适配**，别的助手没有验证过；要用得自己接。

## 框架版怎么取

两种方式，随便挑：

```bash
# 只要空骨架 + 工具
git clone https://github.com/Aznobin/shiori-memory.git

# 想要固定版本（克隆后进目录再切）
git checkout framework-v0.3.0
```

公库远端只有 `main` 一条分支，没有 `framework` 分支，所以**别写 `-b framework`**（会 `fatal: Remote branch framework not found`）；`framework-v0.3.0` 是打在 `main` 上的 **tag**，克隆下来再 `git checkout` 就能切到那个版本。

`main` 是**孤儿分支**（orphan branch）起点，不带私人仓库的历史，所以拿到的是一份干净的新起点，不会被别人的笔记污染。

`dist/` 目录里的 zip 只是打包临时产物，长期分发请看分支和 tag。

## 授权

[MIT License](LICENSE)，Copyright (c) 2026 Firin Chan & Aznobin。
