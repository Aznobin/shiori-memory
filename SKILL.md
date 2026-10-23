---
name: shiori-memory
description: 纯 Markdown 长期记忆库（模块化 + 片段检索）。涉及个性化交流、偏好与背景回忆、学习或项目进度延续、写入与维护记忆时使用。先读本文件（唯一必读的主文件与入口索引），再按路由只加载命中的那一个或几个模块，不要整库读。
---

# Shiori Memory

给 **DeepSeek Harness（DSH）** 用的本地记忆库。`references/` 是真源，`fragments/` 是切出来的检索单元，开局只装用得上的几条。

**适配范围**：只在 DSH 上验证过（Windows 11 + Python 3.12）；技能目录、脚本路径、注入方式都按 DSH 的规矩来，别的助手没有验证过。

## 0. 读法（最高优先级）

- **本文件是唯一必读**：只放协议与路由，不放日常条目；正文全在 `references/` 里，一次只读命中的那个模块
- **每次开局先建索引**：`python tools/frag.py build`。索引过期就去重跑，别凭印象翻文件
- **不要整库读**：先 `search` 再按命中的路径读单个文件，`变更日志`、`_复核清单`、`archive/` 只在维护时看
- **库是空的也能用**：刚拿到手时没有任何主题模块，照 §4 建第一个

## 1. 三条硬规矩

1. **`references/` 是唯一真源，`fragments/` 每次 `build` 全删重建** —— 手改片段目录，下一轮就被覆盖，改了等于没改
2. **每次写入都要落到一个具体模块**，并在 `变更日志.md` 补一行（日期 + 改了什么）
3. **没被确认的推测先扔 `_待确认.md`**，不当事实用；拿不准就写进去等下次确认

## 2. 开局三步

```bash
python tools/frag.py build          # 1. 建索引（改了模块必跑）
python tools/frag.py search 关键词   # 2. 查命中的片段
```

```text
3. 按 search 给出的路径只读那一个片段文件，读完再说
```

`python tools/memdb.py index` 会额外建一份 SQLite 全文索引，库里片段上千之后查得更快，不是必需。

## 3. 编号与目录

| 范围 | 装什么 |
| --- | --- |
| `01`–`89` | **主题模块**，你自己的：偏好、背景、学习线、项目、环境……怎么分由你定 |
| `90`–`99` | **机制文档**，随包发：检索、迁移、打包、注入、安全、评测、架构大纲 |
| `_模板` `_待确认` `_复核清单` `_巡检记录` | 结构件，下划线开头，不算模块 |
| `变更日志` `CATALOG` | 结构件，不带编号 |

新增主题模块往后接号，别插空号；`CATALOG.md` 由 `python tools/catalog.py` 生成，别手改。

**想加人格/语气模块就自己建一个**（比如 `89`），这库里没有预设的语气设定 —— 库只提供骨架，装什么由你决定。

## 4. 建第一个模块

```bash
cp references/_模板.md references/01-我的偏好.md
python tools/frag.py build
```

模块文件结构：开头一行 `# 标题`，往下按段落写，段落之间空一行。`build` 会按段落切片段，**一段超过 4 行就在中间切开**，切出来的兄弟片段标题尾部带 `（1）（2）（3）`，在索引里共享同一个 `part.base`。

## 5. 写入协议

记忆写入走闸门，别直接改文件：

```bash
python tools/write_gate.py --batch 请求.json
```

请求里每条要写清 `module`（模块号）、`op`（`add` / `merge` / `weight`）、`body`、`keys`、`date`。模块号不在 `references/` 现有清单里、片段体超长、含代码围栏、日期不是 `YYYY-MM-DD`、关键词为空 —— 任意一条不过就**整批拒**并给出中文原因。

改完跑一次 `main` 提交，历史随时可回滚。

## 6. 别让它越堆越肿

| 机制 | 做法 |
| --- | --- |
| 时间衰减 | 片段带 `updated` 日期，检索乘 `0.7 + 0.3 × recency`，半衰期 90 天。老的还在，只是不抢前排 |
| 冷热三态 | `active` / `cold` / `archived`，状态存 `state/decay.json`，`build` 不会冲掉 |
| 每日结算 | `python tools/decay.py --tick`，同一天重复跑只算一次（幂等），不会开十个对话就把库清空 |
| 重复巡检 | `python tools/decay.py --pairs` 找疑似重复，只报不改 |
| 同源折叠 | 同一 `part.base` 的多片在 `search` 输出里折成一行，加 `--expand-parts` 展开 |

## 7. 常用命令

| 命令 | 用途 |
| --- | --- |
| `frag.py build` / `lint` / `stats` | 重建索引 / 体检 / 看规模 |
| `frag.py search 词` | 查片段（默认折叠同源） |
| `frag.py pack` | 打包成可直接塞进 prompt 的文本 |
| `frag.py auto` | 一次给「开局要读的那几块」 |
| `memdb.py index` | 建 SQLite 全文索引（可选） |
| `decay.py --tick` / `--pairs` | 衰减结算 / 重复巡检 |
| `write_gate.py --selftest` | 写入闸门自测 |
| `framework.py --dry` / `--build` | 打印 / 生成框架发布版（带隐私门） |

## 8. 发布前（重要）

要把记忆库公开或打包给别人，**必须**先跑：

```bash
python tools/framework.py --dry    # 看清单，确认没有私人模块
python tools/framework.py --build  # 生成，隐私门命中即中止
```

隐私门会扫私人关键词（真名、机器名、项目名、日记路径），命中就停，**不许先提交再补**。框架版里只有骨架、工具和机制文档，没有任何私人内容。

## 9. 版本

`0.3.0` · MIT License · by Firin Chan & Aznobin

适配范围：**只有 DSH**（DeepSeek Harness，Windows 11 + Python 3.12）。别的助手要用得自己改，也没在别处验证过。
