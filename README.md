<h1 align="center">Shiori Memory</h1>

<p align="center"><b>Local-first memory for AI coding agents, natively inside DSH (DeepSeek Harness).</b></p>

<p align="center">No vector database. No API key. No extra service.</p>

<p align="center">
  <img alt="version" src="https://img.shields.io/badge/version-1.0.0-blue">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="python" src="https://img.shields.io/badge/python-%E2%89%A53.12-yellow">
  <img alt="platform" src="https://img.shields.io/badge/DeepSeek%20Harness-plugin-purple">
  <img alt="format" src="https://img.shields.io/badge/%E7%BA%AF-Markdown-1f6feb">
  <img alt="deps" src="https://img.shields.io/badge/%E4%BE%9D%E8%B5%96-0-brightgreen">
  <img alt="tests" src="https://img.shields.io/badge/verify_pack-P0%200%20%C2%B7%20P1%200-success">
</p>

<p align="center"><sub><code>一个目录 = 整份记忆</code> · <code>换机器拷走就行</code> · <code>不上云也能用</code> · <code>骨架有校验和</code></sub></p>

<p align="center">给 AI 编码助手用的纯 Markdown 长期记忆库：片段化存储、关键词路由、时间衰减、写入闸门。</p>

<p align="center">by Firin Chan & Aznobin</p>

> **适配范围**：目前只在 **DeepSeek Harness（DSH）** 上验证过（Windows 11 + Python 3.12），技能目录、脚本路径、注入方式都按 DSH 的规矩来。如果希望用在别的助手上，需要您自己做一点适配——这一版没有在别处验证过。

## 目录

- [这是什么](#这是什么)
- [特色](#特色)
- [它治什么毛病](#它治什么毛病)
- [快速开始（三步，不用敲任何命令）](#快速开始三步不用敲任何命令)
- [目录长什么样](#目录长什么样)
- [片段是怎么回事](#片段是怎么回事)
- [命令都是自动跑的（不用你操作）](#命令都是自动跑的不用你操作)
- [从旧笔记搬内容（importer）](#从旧笔记搬内容importer)
- [记忆怎么不越堆越肿](#记忆怎么不越堆越肿)
- [装成技能用（DSH）](#装成技能用dsh)
- [框架版怎么取](#框架版怎么取)
- [升级骨架](#升级骨架)
- [授权](#授权)

## 这是什么

简单说：**把「关于你的那些事」拆成一个个小文件，开局只取用得上的那几条，放进模型的上下文。**

AI 助手最常被抱怨的毛病是**失忆**：换个对话就不认识你了，昨天说过的偏好今天就忘。一种常见的补法是把手上所有资料都塞进提示词，代价是既贵，又容易被无关内容带偏。

这个库换个做法：资料按主题切成小碎片（fragment），再配一份索引，需要什么查什么，一次只装几条。

## 特色

一共九条。前三条关于「像个记忆库」，后六条关于「像个有脾气的东西」。

1. **零依赖、纯文本** —— 不装向量库、不联网、不为写记忆额外跑模型，Python 标准库就够；全部是 `.md`，可以用任何编辑器打开，也方便纳入版本管理
2. **一次只装几条** —— 索引先筛，只取命中的片段，不把整篇塞进上下文；检索会先分辨「问的是人还是机器」，冷热三态负责让旧内容慢慢退后
3. **一个目录拷走就是迁移** —— 换电脑不必导出导入，也不用起服务；打包脚本会清空私人内容，别人拿到的是一份干净的骨架
4. **语气模块随包走** —— 每份包都带一份 `813-语气与交流`，**开局必读**：短句、不写括号动作、不总结不升华、允许说「我不同意」，并附**去 AI 腔十条 + 发前三问**；想换说话的味道，改这一个文件就够
5. **记你怎么说话**（语气模仿）—— 常用词、句子长短、标点习惯、口头禅，以及不同场合的两套口气。这些**会在聊天里慢慢学到**，攒进 `813` 的样本附录；请它代写日记、消息、汇报时用**你自己的**口气，聊天时仍是它自己的声音，判断和喜恶一个字都不会跟着改
6. **你硬它也硬** —— 不会因为你语气重就改口，只服证据和理由；不同意会直接说，说完会给活路。这跟萌不萌无关，是骨架
7. **骨架碰不得** —— `tools\`、`bin\`、`templates\`、`framework-public\`、`AGENTS.md`、`SKILL.md`、`setup.ps1`、`config.example.json`、`references\9*.md` 属于作者，**没有得到您的明确同意，助手一个字都不该改**；想确认有没有被改动，跑 `python tools/skeleton_guard.py`——它会对照 `SKELETON.sha256` 列出改过的、缺失的、多出来的，而且**不能自己重新盖章**
8. **三种情况先停手**（安全阀）—— 往外发数据、删重要文件、改坏库，这三件事写进了入口当红线；删记忆只走 `retire`（软删：行尾留「已退休 日期：理由」，正文一个字不删，切片自动跳过）
9. **命令都是它自己跑的** —— 第一次问旧事时会自动建索引，该记的事会自动过写入闸门，收工会自动体检结算。**不需要您敲任何一条命令**

## 它治什么毛病

| 毛病 | 现在的做法 |
| --- | --- |
| 整篇读进来之后开始编造 | 只给命中的片段，不给整篇 |
| 每次对话都重新烧一遍全文 | 索引先筛，只取几条 |
| 换台机器记忆就没了 | 一个目录拷走，重建两条命令 |
| 只加不删，越堆越大 | 时间衰减 + 冷热三态 + 手动归档 |
| 记忆写坏了没法回滚 | `memdb.py snapshot` 打 zip 快照（留最近 20 份）；装了 git 也能用 `git log` / `git revert` |
| 说话一股 AI 味 | 语气模块开局必读，去 AI 腔十条直接查 |
| 让它代笔，写出来不像自己 | 记住你的用词与句式，替你写时套回去；立场不跟着改 |
| 你说什么它都说「好的」 | 只服证据与理由；不同意直接说，顶完给活路 |
| 助手顺手改了你不想让它动的东西 | 骨架禁区写进入口，改没改过用 `skeleton_guard.py` 对照清单核 |

## 快速开始（三步，不用敲任何命令）

**不需要装 git、不需要联网、不需要装数据库、也不需要先跑什么初始化命令。** 一共三步：

**1. 解压** —— 下载 zip，解到任意目录（比如 `%USERPROFILE%\shiori-memory`）：

> https://github.com/Aznobin/shiori-memory/archive/refs/heads/master.zip

**2. 复制一个文件** —— 把库根那份 `AGENTS.md` 复制到你的工作目录根：

```text
Windows       copy AGENTS.md C:\你的工作目录\
macOS/Linux   cp AGENTS.md ~/你的工作目录/
```

**3. 开工** —— 打开你的 AI 助手，正常说话就行。助手进到这个目录时会自己读到 `AGENTS.md`。

到这里就结束了，没有第四步。

> **关于 python**：后面那些 `python tools/...` 是**助手在读记忆、写记忆时自己调用的**，不是留给您用来部署的。不需要您敲、不需要先跑一遍才算装好、不需要起服务、也不需要建数据库。机器上能找到 python（多数 AI 编码助手的环境自带）就够了。

### 为什么是「复制这个文件」

`AGENTS.md` 不是说明书，是**入口**：它告诉每一次新会话「这里有个记忆库、库在哪、怎么读、怎么写」。

**它的开头只有三行**：库在哪、先当普通助手、需要时再查。规矩都放在后面的小节里，标着「用到再看」——**不建议让助手一进门就背全部规矩**，那样它容易变成一台念章程的机器。

> 文件里的 `<记忆库>` 是占位符。复制出去之后，把那一行换成库的绝对路径（`SKILL.md` 所在的那个目录）。忘了换也可以，文件里写明了「还是尖括号就问用户一句」。

`SKILL.md` 是库里面的协议与路由（读法、写法、命令），`AGENTS.md` 是库外面那个带路的路标——一个对内，一个对外。

如果不想手工摆，也可以跑 `setup.ps1 -Workspace <你的工作目录>`：它会填好路径、把 `AGENTS.md` 复制过去、顺手建好索引。**但它不是必须的**，解压之后照着上面复制一样能跑。

没装 git 的话，用上面的 zip 就够了。

装了 git 的话：

```bash
git clone https://github.com/Aznobin/shiori-memory.git
```

之后建议推一份到自己的私有仓库：换机器克隆下来就能接着用，改坏了也能回滚。公开库只放框架，请不要放记忆。

## 目录长什么样

```text
SKILL.md            # 主入口：库内的协议与路由，告诉模型先读哪、按什么查
AGENTS.md           # 记忆库入口：复制到你的工作目录根，新会话靠它找到这里
README.md           # 你正在看的这份
references/         # 唯一的真源：按主题分的模块文件（01-99）
  926.md          # 新模块照这个写
  922.md        # 没想好的先扔这里
  921.md       # 改了什么，一条一行
  927.md        # 模块清单
  90-99             # 机制类：检索、打包、注入、安全、架构
templates/          # hooks 与各主机变体的模板
tools/              # 全部脚本，零第三方依赖
fragments/          # 自动生成，别手改
  index.json        # 索引：编号 / 标题 / 关键词 / 日期 / 状态 / part
  .dates.json       # 片段最后更新日期缓存
state/
  decay.json        # 衰减参数与计数（半衰期、冷热阈值、上次结算日）
dist/               # 打包临时产物，随时可删
```

**一条铁律**：`references/` 是唯一真源，`fragments/` 每次 `build` 都会全删重建。手改片段，下一轮就没了。

## 片段是怎么回事

`build` 会把每个模块按段落切开，一段一片，标题做文件名。规则不复杂：**一段超过 4 行就在中间切开**，切出来的兄弟片段标题尾部带 `（1）（2）（3）`。

于是有几种情况：

- 一段话被切成好几片时，它们在索引里共享同一个 `part.base`（去掉 `（n）` 的标题），各自带 `part.seq`
- `search` 默认把同一 `part.base` 的命中**折成一行显示**：`202.002–202.016〔同源 15 片 · 合计 1711 字〕`
- 想看细的，加 `--expand-parts`，恢复成逐条列出
- `pack` 会把相邻的同源片拼回整段再喂，但最多拼到 12 行 / 600 字

这样检索单位够细（命中准），喂给模型的却是整段（不碎片化），两头都照顾到。

## 命令都是自动跑的（不用你操作）

这套东西的前提是：**你只跟助手说话，命令由它自己调用。** 下面这张表说的是「什么时候自动发生」，不是「你要执行的步骤」。

| 什么时候 | 自动发生了什么 |
| --- | --- |
| 你第一次问到以前的事 | 自动建索引（几秒），然后只读命中的那一两个片段 |
| 你说了一件该记的事（偏好变了、纠正了它、项目有进展） | 有子智能体就派给它去写、自己接着说；没有就当场写。两条路都会走闸门：查模块 → 插到该去的小节 → 改 `updated` → 补一行变更日志 → 重建索引 |
| 每天收工（配了定时才有） | 自动跑一次衰减结算和体检 |
| 骨架出了新版本 | 开局时提示你一句「有新骨架 A → B」，**升不升由你决定**，它不会自作主张去换 |

**不需要你做的事**：不必先跑 `build` 才算装好，不必手动建库建表，不必起服务，不必写 SQL，也不必背命令。这些要么是自动的，要么是助手的事。

**记忆是谁写的**：如果你的助手环境支持子智能体，它会派一个专门管记忆的助手（比如 `memo`）去写，自己接着说、不站着等；不支持就在那一轮自己写。两种方式你都不用管，也不用提醒它「记得写」。

> 下面这张表是它背后调用的命令，留给您查证用：想知道它到底动了什么、或者想自己手动跑一次时可以看。

| 命令 | 干什么 |
| --- | --- |
| `python tools/frag.py build` | 重建片段与索引（平时不用手动跑：第一次 `search` / `memread` 会自动建） |
| `python tools/frag.py search 词` | 查片段，默认折叠同源 |
| `python tools/frag.py pack` | 打包成一份可以直接塞进 prompt 的文本 |
| `python tools/frag.py lint` | 体检：空标题、重复编号、坏元数据 |
| `python tools/frag.py stats` | 模块数、片段数、字数分布 |
| `python tools/memdb.py index` | 建 SQLite 全文索引（可选，提速用） |
| `python tools/decay.py --tick` | 跑一次时间衰减结算（同一天只算一次） |
| `python tools/decay.py --pairs` | 找「可能重复」的片段，只报不改 |
| `python tools/memwrite.py payload.json` | **写记忆的正路**：过闸门 → 插到指定小节 → 改 `updated` → 补日志 → build + index（`--dry` 只看不写，`--changelog "摘要"` 顺手记一行）。三种 op：`add` 加一条、`replace` 换掉一行（靠 `old` 定位）、`retire` 软删一条（见下节） |
| `python tools/write_gate.py` | 单独校验一批写入请求（`memwrite.py` 内部已经调它，手动跑是排查用） |
| `python tools/import_notes.py 目录` | **从旧笔记或日记里搬内容**：切块 → 清理 → 按关键词路由到大类 → 落成草稿。默认只扫描，`--write` 落 `state/imports/`，人工标 `OK` 后 `--apply` 才真写进分类模块（走和 `memwrite` 同一条闸门）。`--selftest` 自检，`--show N` 看打头几条 |
| `python tools/framework.py --dry` | 打印将要发布的框架文件清单 |
| `python tools/framework.py --build` | 生成框架版并过隐私门 |
| `python tools/parity.py` | `tools/` 下同名 `.py` / `.ps1` 双份各跑一次，比 stdout 与退出码（只有成对且不一致才 exit 1；无对照只当信息行；默认跳过会写盘的 `pack` / `setup`，要跑加 `--include-heavy`） |
| `tools/check-modules.ps1` | 检查模块编号与清单一致性。**只有 ps1 版，无 py 对照**（跑：`pwsh -NoProfile -File tools/check-modules.ps1`）；`tools/` 下现在只剩这一个 ps1（`pack.ps1` / `memory-check.ps1` / `check-freshness.ps1` 已于 2026-10-24 删除，回滚见 `921`） |

## 从旧笔记搬内容（importer）

`tools/import_notes.py` 用来把散在别处的笔记（日记、旧笔记库、聊天导出）转成记忆草稿。

它**不写正文、不建模块**，只做四件事：切块 → 清理 → 路由 → 落草稿。

- 切块：认 `#` 标题、列表项、`>` 引用；过碎的合并，过长的切散；代码块里的行按原样保留，但不算正文
- 清理：去 markdown 链接、去反引号、去重复空行、去 `Git: 1 次` 这类统计行，以及只有标签没有内容的半截行
- 路由：用现有关键词表命中大类；一个都没命中的进 `922`，不硬塞
- 落草稿：写到 `state/imports/`，每条带 `trigger`（原始出处：文件 + 行号）和 `keys`（命中的词）

**草稿不审就是污染。** 请打开草稿文件，在想要的条目上标 `OK`，其余留空，再跑 `--apply`：只有标了 `OK` 的才会进分类模块，而且和手动写入走同一条闸门（模块号、片段体长度、关键词、日期格式都要过）。

用法：

```bash
python tools/import_notes.py ~/笔记/日记            # 只扫描，报块数/草稿数/路由分布
python tools/import_notes.py ~/笔记/日记 --write    # 落草稿到 state/imports/
python tools/import_notes.py --apply                # 审完标 OK 之后，把 OK 的写进模块
```

实测一个 21 篇的日记目录：126 块 → 98 条草稿，路由 `07×54 · 11×19 · 08a×6 · 28×4`。

## 记忆怎么不越堆越肿

记忆库用久了必然会肿，肿了就慢、就吵。这里靠五件事压着：

1. **时间衰减**：片段带 `updated` 日期，检索时乘一个 `0.7 + 0.3 × recency`，半衰期 90 天。旧内容还在，只是不再抢前排。
2. **冷热三态**：`active` / `cold` / `archived`。长期没被召回的先降冷，再进归档；归档的不参与开局注入，但照样能搜到。状态写在 `state/decay.json`，`build` 不会把它冲掉。
3. **每日结算幂等**：衰减按天算，同一天跑十遍也只扣一次，不会因为开了十个对话就把库清空。
4. **写入门（2026-10-24 接入）**：`write_gate.py` 校验模块号、片段体长度、关键词、日期格式，不合格整批拒；`memwrite.py` 落盘前会先调它，不过闸门一条都不写。写入的正路是「产出 payload → 跑 `memwrite.py`」，不再手改模块文件。
5. **软删，不硬删（2026-10-24）**：想说「这条别再用了」，不必把行从文件里抹掉——用 `memwrite.py` 的 `op: "retire"` 把它标成退休：

   ```json
   [{"module": "101", "op": "retire", "old": "读过哪本书第一章那半句", "reason": "跨模块重复：长期兴趣状态在 01，这里只记当天事件"}]
   ```

   `old` 用来定位那一行（必须唯一命中），`reason` 必填。写进去的效果是行尾挂一段 `（已退休 YYYY-MM-DD：理由）`，**正文一个字不删**；`frag.py build` 时含这个标记的行不进片段、也不会被检索命中，`memdb.py` 对应标 `deleted=1` 并留审计记录。

   为什么软删：本库是纯文本，一年的日记也就几兆——**本库没有容量压力**。硬删之后想追「当初为什么把它去掉」，就只剩 `921` 里一行字；软删的原文一直在，退休理由也一直在。真正硬删只在一件事上做：用户亲口说「不用记 / 忘掉」。

   已经退休过一次的行不会被重复标记（记为 `skipped`，不报错）。

## 装成技能用（DSH）

这个目录本身就是 DSH 的技能包（skill）：`SKILL.md` 是入口文件，`references/` 是正文。`SKILL.md` 开头的 frontmatter（`name` + `description`）告诉 DSH 什么时候该调用它。

把解压好的目录放到 DSH 的技能目录（不用 git，直接拷文件夹）：

```text
%USERPROFILE%\.agents\skills\shiori-memory
```

或者用命令行建目录再拷贝：

```powershell
New-Item -ItemType Directory -Force %USERPROFILE%\.agents\skills\shiori-memory
Copy-Item -Recurse 解压出来的目录\* %USERPROFILE%\.agents\skills\shiori-memory
```

放好之后不用额外注册，DSH 读到 `SKILL.md` 就知道什么时候用它；第一次查询会自动建索引，不必先跑 `build`。

**目前只做了 DSH 的适配**。如果要用在别的助手上，需要您自己验证与适配。

## 框架版怎么取

**不用 git**（推荐）：浏览器打开
`https://github.com/Aznobin/shiori-memory/archive/refs/heads/master.zip`
下载解压即可。

**用 git**：

```bash
git clone https://github.com/Aznobin/shiori-memory.git
```

想固定某个版本可以用 tag：`git checkout framework-v1.0.0`（tag 名里的版本号会随发行更新）。

公开仓库只有两条分支：`master`（发行）和 `main`（框架工作区），默认是 `master`。

## 升级骨架

```bash
python tools/update.py check            # 有没有新版（24 小时问一次；没配更新源会优雅退出）
python tools/update.py hint             # 只吐一行结论，给开局念（同一版本只提醒一次）
python tools/update.py apply --dry      # 预演：说清会覆盖什么、留几个 .new，一个字不动盘
python tools/update.py apply            # 换骨架
python tools/update.py rollback         # 换坏了退回去（自动备份在 state/backup/）
```

**升级只换骨架，不碰您的记忆正文。** 具体分三档：

- `tools/` `bin/` `templates/` `framework-public/`，以及 `setup.ps1`、`SKILL.md` 这些框架件**无条件覆盖**——它们属于框架本身，不由使用者维护。
- 结构件（`921`、`927`、`923`、`922`、`928`、`924`、`926`）**一个字都不动**——这些是您自己的库长出来的，覆盖等于毁账。
- 协议件（`references/90`–`918`）内容冲突时写成 `903-xxx.md.new`，由您自己合：骨架里那份是脱敏版，直接覆盖会把您写进去的原话换成「（已隐去）」。

**有一个需要知道的先后顺序问题**：升级器自己也在包里。您第一次升级跑的是**盘上那份旧 `update.py`**，所以这一轮仍按旧规则来（比如结构件也会生成 `.new`）。从 0.3.15 起，`apply` 结束时会发现 `update.py` 自己被换了，于是**立刻用新版再跑一遍**，让新规则当次生效。如果您装的是 0.3.14 及更早的版本，第一次升级完请再跑一次 `apply --dry` 确认。

升级完会自动重建 `frag.py build` + `memdb.py index` + `catalog.py`；您的条目、编号、日期都不会变。

## 授权

[MIT License](LICENSE)，Copyright (c) 2026 Firin Chan & Aznobin。
