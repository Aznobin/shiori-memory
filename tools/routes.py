#!/usr/bin/env python3
"""把「记忆大类表」生成进工作区 AGENTS.md 的标记块里（每个会话都会被注入）。

用法：python tools/routes.py        # 重建
      python tools/routes.py --show  # 只打印，不写文件
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import cfg  # noqa: E402

BEGIN = "<!-- ROUTES:BEGIN -->"
END = "<!-- ROUTES:END -->"

SECTION_TITLE = "## 记忆大类表（每轮注入，由 `tools/agents_sync.py` 生成）"


def agents_path() -> Path:
    """工作区 AGENTS.md：优先 memory.config.json 的 workspace，其次当前目录逐级向上找。"""
    ws = (cfg.get("paths") or {}).get("workspace") or ""
    if ws and (Path(ws) / "AGENTS.md").exists():
        return Path(ws) / "AGENTS.md"
    for base in [Path.cwd(), *Path.cwd().parents]:
        if (base / "AGENTS.md").exists():
            return base / "AGENTS.md"
    if ws:
        return Path(ws) / "AGENTS.md"
    raise SystemExit("找不到 AGENTS.md：先填 memory.config.json 的 paths.workspace")


# (大类, 模块, 装什么, 怎么读)   怎么读：★ 精读 / ○ 随机抽 / ● 按需
ROUTES: list[tuple[str, str, str, str]] = [
    # —— 人格区：89 往下递减，越靠下越核心/越基础 ——
    ("人格·说话", "`89` `88` `87` `81`", "语气与人设、A/B 口吻素材、每日风格轮换", "`89` ★；`88`/`87` ○随机抽小节"),
    ("人格·立场", "`86` `85`", "常驻行为规则、我的立场与账", "**已直接常驻** `AGENTS.md` §8，不用读文件"),
    ("人格·行为", "`84` `80` `79` `77`", "碎碎念、斗图、主动话题池、口癖与口语映射", "`84` ○随机抽；`79`/`80`/`77` 开口时读"),
    ("人格·我是谁", "`78`", "我自己的性格：稳定倾向、矛盾、变化轨迹", "●被问「你是什么性格／你变了吗」时读"),
    # —— 用户记忆区：01 往上递增 ——
    ("用户本人", "`01` `29` `30` `31`", "背景、兴趣、名字候选、迁移备份、性格侧写", "`01` ●命中即读；`31` ●聊到用户性格时读"),
    ("学习与笔记", "`02` `02a` `12a` `12b` `23` `24`", "学习习惯、标签白名单、英语词汇与语法、课表与学期节点、PPT 制作、日语", "●按话题"),
    ("技术学习线", "`03` `04` `05` `06` `09` `28`", "Java / 算法 / Vue / C / 杂项 / 全栈路线", "●按话题，别整套读"),
    ("项目", "`07`", "<项目> 目标、后端细节、日记规范、进度", "●要碰代码时读"),
    ("环境与设备", "`08` `08a` `21`", "硬件与排障、服务器与个人站、DSH 模块清单", "●装机 / 换设备 / 报错 / 连不上服务器时"),
    ("日常与状态", "`11` `22`", "日常骨架、情绪波谱", "`22` ○随机抽（敏感）"),
    ("健康", "`10`", "健康相关历史自述", "**敏感**，只在用户主动提时读"),
    ("AI 与工具", "`13`", "用量基准、订阅与 API 的账", "●聊钱 / 额度时"),
    ("规划", "`27`", "专升本与统考规划", "●按话题"),
    # —— 系统区：90 以后，只装机制 ——
    ("系统·总图", "`99`", "架构大纲：唯一事实源与派生层、五层职责、写入数据流、十条不变量、改动五步、判据与反模式", "**改动任何东西之前先读它**；上下文被压缩后迷路也读它"),
    ("系统·机制", "`90` `91` `91a` `92` `93` `95` `97` `97a` `98`", "检索与命中率、迁移与骨架、打包分发、自动注入、参考资料出处、检索评测、口语说法词库、hook 验收", "●维护 / 换设备 / 打包 / 问「哪来的依据」「到底通没通」时"),
    ("系统·安全", "`96`", "安全墙：威胁模型、四道墙、注入识别清单、接机器人前的工程要求", "●接外部输入 / 接到聊天机器人 / 被要求越权时"),
    # 结构件 `_待确认` / `变更日志` 也算模块（memory_check 与 catalog 都把它们数进 48），
    # 挂在这一行末尾让三个脚本的「模块数」对齐；`_模板`/`CATALOG`/`_复核清单`/`_巡检记录`/`_路由详表` 两边都排除。
    ("系统·节律", "`83` `82` `94` `_待确认` `变更日志`", "自维护巡检、重复问题台账、静默门；待确认收件箱与改动史", "●维护时才读；查「以前改过什么」读 `变更日志`"),
]


def render() -> str:
    lines = [BEGIN, "| 大类 | 模块 | 装什么 | 怎么读 |", "| --- | --- | --- | --- |"]
    for name, mods, what, how in ROUTES:
        lines.append(f"| {name} | {mods} | {what} | {how} |")
    lines.append(f"| — | 小类详表 | 每个模块一行（关键词 / 同义说法 / 敏感） | 在 `references/_路由详表.md`，需要时再读 |")
    lines.append(END)
    return "\n".join(lines)


def main() -> int:
    block = render()
    if "--show" in sys.argv:
        print(block)
        return 0
    agents = agents_path()
    text = agents.read_text(encoding="utf-8") if agents.exists() else ""
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        new = head + block + tail
    else:
        if not text.endswith("\n"):
            text += "\n"
        new = text + "\n" + SECTION_TITLE + "\n\n" + block + "\n"
    agents.write_text(new, encoding="utf-8")
    print(f"已写入 {agents}（{len(new.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
