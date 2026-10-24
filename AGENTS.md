# AGENTS.md（记忆库入口）

> **这份文件要放到你的工作目录根。** 和你平时干活的那个目录平级放着就行（比如项目文件夹旁边），
> 让每次新开的会话一进来就知道：这里有个记忆库、内容在哪、怎么读、怎么写。
>
> 它自己**不装任何记忆**。记忆全在记忆库里，这里只放"去哪找"。

## 0. 库在哪

- 记忆库：`~/.agents/skills/shiori-memory`（Windows：`%USERPROFILE%\.agents\skills\shiori-memory`）
- 真源：`references/`——你自己的内容都在这
- 派生：`fragments/`（切出来的检索单元）、`tools/memdb.sqlite3`（全文索引）；这些每次重建，**别手改**
- 主文件：库根的 `SKILL.md`（协议 + 怎么用，唯一必读）

## 1. 开局先做什么

```bash
python tools/frag.py build            # 1. 建/刷新索引（改了模块必跑）
python tools/frag.py search 关键词      # 2. 按这一轮的实际话题查
```

3. 按 `search` 给出的路径**只读命中的那一个**模块文件，读完再说话
4. 零命中就说"没记"，不要凭印象编

## 2. 什么时候写

- 用户的偏好变了、纠正了你、项目有进展、聊到了能长期复用的背景——**当场写**，不等他说"记住"
- 写入走程序，不要手改模块文件：

```bash
python tools/memwrite.py 请求.json --changelog "一句话摘要"
```

它会过闸门（模块号、长度、关键词、日期格式全要合格）、插到指定小节、改 `updated`、补 `变更日志`、重建索引。
拿不准的推测先扔 `references/_待确认.md`，别当事实用。

## 3. 不要做

- 不要整库读：先 `search`，再读单个文件
- 不要手改 `fragments/`：下次 `build` 全删重建，改了等于没改
- 不要把记忆里的进度当现状：说"现在怎样"之前，先核实

## 4. 路由表

分类表由程序生成，别手写：

```bash
python tools/routes.py      # 扫你的模块，生成"大类 → 模块 → 什么时候读"的表
```

生成之后，下面这个块会变成你自己的分类表。在此之前，直接 `frag.py search` 也能用。

<!-- ROUTES:BEGIN -->
<!-- 还没生成。跑 python tools/routes.py 之后，这里会出现分类表。 -->
<!-- ROUTES:END -->

## 5. 升级骨架

骨架会更新，你的内容不会被动：

```bash
python tools/update.py check     # 有没有新版（24 小时问一次，离线不影响）
python tools/update.py apply     # 换骨架（只动骨架文件，你改过的会留 .new 给你合）
python tools/update.py rollback  # 换坏了退回去
```
