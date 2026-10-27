#!/usr/bin/env python3
"""把「记忆大类表」生成进工作区 AGENTS.md 的标记块里（每个会话都会被注入）。

用法：python tools/routes.py        # 重建
      python tools/routes.py --show  # 只打印，不写文件
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import cfg  # noqa: E402
from paths import (  # noqa: E402
    CONTENT_DIR,
    ROOT,
    SKELETON_DIR,
    module_files,
    partition_dirs,
    skeleton_node_files,
)

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
    ("人格·说话", "`813` `812` `811` `806`", "语气与人设、怎么称呼、口癖与禁忌", "`813` ★ 每次开局必读；其它口吻素材 ○随机抽小节"),
    ("人格·立场", "`809` `808`", "常驻行为规则、我的立场与账", "**已直接常驻** `AGENTS.md` §8，不用读文件"),
    ("人格·行为", "`807` `805` `804` `802`", "碎碎念、斗图、主动话题池、口癖与口语映射", "`807` ○随机抽；`804`/`805`/`802` 开口时读"),
    ("人格·我是谁", "`803`", "我自己的性格：稳定倾向、矛盾、变化轨迹", "●被问「你是什么性格／你变了吗」时读"),
    # —— 用户记忆区：01 往上递增 ——
    ("她这个人", "`001` `002` `919` `003`", "背景、兴趣、性格侧写", "`001` ●命中即读；`003` ●聊到她性格时读"),
    ("学习与笔记", "`201` `202` `208` `209` `211` `212`", "学习习惯、标签白名单、英语词汇与语法、课表与学期节点、PPT 制作、日语", "●按话题"),
    ("技术学习线", "`203` `204` `205` `206` `207` `214`", "Java / 算法 / Vue / C / 杂项 / 全栈路线", "●按话题，别整套读"),
    ("项目", "`301`", "<项目> 目标、后端细节、日记规范、进度", "●要碰代码时读"),
    ("环境与设备", "`302` `303` `304`", "硬件与排障、服务器与个人站、DSH 模块清单", "●装机 / 换设备 / 报错 / 连不上服务器时"),
    ("日常与状态", "`101` `102`", "日常骨架、当天做了什么", "`101` ●命中即读；其它状态类 ●按话题"),
    ("健康", "`701`", "健康相关历史自述", "**敏感**，只在她主动提时读"),
    ("AI 与工具", "`702`", "用量基准、订阅与 API 的账", "●聊钱 / 额度时"),
    ("规划", "`213`", "专升本与统考规划", "●按话题"),
    # —— 系统区：90 以后，只装机制 ——
    ("系统·总图", "`918`", "架构大纲：唯一事实源与派生层、五层职责、写入数据流、十条不变量、改动五步、判据与反模式", "**改动任何东西之前先读它**；上下文被压缩后迷路也读它"),
    ("系统·机制", "`903` `904` `905` `906` `908` `909` `912` `915` `916` `917`", "检索与命中率、迁移与骨架、打包分发、自动注入、外部同类插件怎么把东西塞进上下文、参考资料出处、检索评测、口语说法词库、hook 验收", "●维护 / 换设备 / 打包 / 问「哪来的依据」「到底通没通」时"),
    ("系统·安全", "`913`", "安全墙：威胁模型、四道墙、注入识别清单、接机器人前的工程要求", "●接外部输入 / 接到聊天机器人 / 被要求越权时"),
    # 结构件 `922` / `921` 也算模块（memory_check 与 catalog 都把它们数进 48），
    # 挂在这一行末尾让三个脚本的「模块数」对齐；`926`/`927`/`925`/`923`/`928` 两边都排除。
    ("系统·节律", "`902` `901` `911` `922` `921`", "自维护巡检、重复问题台账、静默门；待确认收件箱与改动史", "●维护时才读；查「以前改过什么」读 `921`"),
    # 末位为 0 的骨架节点：程序生成、只放结构，登记进来只为 memory_check 的文件/路由双向一致
    ("系统·骨架节点", "`000` `100` `200` `300` `400` `500` `600` `700` `800` `900`", "程序生成的骨架占位：只放结构，不放记忆内容", "读 `000-总索引.md` 看全局结构；改结构跑 `tools/skeleton_nodes.py`"),
]


# 元规则模块（2026-10-26 她定）：讲「记忆库自己怎么修」的模块，非维护查询里降权。
# 2026-10-27 批 H：这串原来写死在 `tools/recall.py:122`，两位→三位迁移时靠人肉补，
# 补漏了就是「列表悄悄过期、降权规则静默失效」。现在挪到这里做单一事实源——
# 选这里不选 frontmatter 的理由：它是**打分口径**（同一批编号在别处还要按大类分组用），
# 放路由表旁边和 ROUTES 一起看，改一个地方就够；frontmatter 标记会让「读一行 frontmatter」
# 变成打分链路上的隐式依赖，反而更难查。
META_MODULE_IDS: tuple[str, ...] = (
    "903", "904", "905", "906", "908", "912", "915", "916", "917", "918", "304",
)


def meta_module_ids() -> set[str]:
    """返回元规则模块集合；有编号在 ROUTES 表里找不到就直接报错退出。

    只加校验，不改口径：集合内容与迁移前 `recall.py` 里那份完全一致。
    重编号（例如两位→三位）之后若只改了 ROUTES 没改 META_MODULE_IDS，
    旧编号会在这里当场炸掉，而不是让降权规则默默失灵。
    """
    known: set[str] = set()
    for _, mods, _, _ in ROUTES:
        known.update(re.findall(r"`(\d{2,3}[a-z]?)`", mods))
    stale = [i for i in META_MODULE_IDS if i not in known]
    if stale:
        raise SystemExit(
            "META_MODULE_IDS 过期：" + "、".join(stale) + " 不在 ROUTES 表里；"
            "重编号后必须同步 tools/routes.py 的 META_MODULE_IDS"
        )
    return set(META_MODULE_IDS)


def skeleton_paths_note() -> str:
    """「系统·骨架节点」行的说明：第二级子类目录的可达路径要写进来，不然没人知道它们在哪。

    1xx–8xx 的子类目录在 `memory/<分区>/`、9xx 在 `references/<分区>/`（2026-10-27 坐实第二级）；
    路径从磁盘长出来，别手抄——目录搬了这里跟着变。
    """
    dirs: list[str] = []
    for base, label in ((CONTENT_DIR, "memory"), (SKELETON_DIR, "references")):
        dirs.extend(f"`{label}/{d.name}/`" for d in partition_dirs(base))
    return "；".join([
        "子类节点（第二级，共 81 个，空壳不进检索）在 " + "、".join(dirs),
        "读 `000-总索引.md` 看两级结构",
        "改结构跑 `tools/skeleton_nodes.py`",
    ])


def mod_num(stem: str) -> str:
    """文件名前缀里的模块号；`923-巡检记录` → `923`。协议件 2026-10-27 起带标题，
    比较模块号时不能直接拿 stem 比，否则排除表全失效。"""
    m = re.match(r"^(\d{2,3}[a-z]?)", stem)
    return m.group(1) if m else stem


def present_ids() -> dict[str, str]:
    """扫 `memory/` 与 `references/`，返回 {模块号或结构件名: 文件 stem}。

    2026-10-24：新设备演练暴露——ROUTES 是主库写死的私人分类表，骨架里根本没有
    89/86/84/78 这些模块，却照样被渲染进别人的 AGENTS.md；反过来新用户建的 801-我是谁
    不在表里，memread 零命中、体检 exit 1。所以路由表必须从实际文件长出来。
    """
    out: dict[str, str] = {}
    for p in sorted(module_files().values()):
        stem = p.stem
        if mod_num(stem) in ("926", "927", "925", "923", "928", "924", "929"):
            continue
        m = re.match(r"^(\d{2,3}[a-z]?)", stem)
        out[m.group(1) if m else stem] = stem
    return out


def write_detail() -> tuple[int, int]:
    """重建 references/928-路由详表.md（体检按「第一格是模块全名」核对登记）。

    两级（2026-10-27 坐实第二级）：一级模块照旧；二级子类骨架节点另起一节，**第一格写裸编号**——
    写成 `110-子类占位` 会被体检判「路由表登记但文件不存在」（它只认一级模块文件名）。
    """
    rows = ["# 路由详表", "",
            "> 本文件由 `tools/routes.py` 按实际模块文件生成，别手改。", "",
            "| 模块 | 标题 |", "| --- | --- |"]
    n_mod = 0
    for p in sorted(module_files().values()):
        stem = p.stem
        if mod_num(stem) in ("926", "927", "925", "923", "928", "924"):
            continue
        head = p.read_text(encoding="utf-8")[:2000]
        m = re.search(r"(?m)^title:\s*(.+)$", head)
        title = (m.group(1).strip().strip('"') if m else stem)
        rows.append(f"| `{stem}` | {title} |")
        n_mod += 1
    nodes = skeleton_node_files()
    if nodes:
        rows += ["", "## 子类骨架节点（第二级，内容为空，不进检索）", "",
                 "| 节点 | 说明 | 位置 |", "| --- | --- | --- |"]
        for num, p in sorted(nodes.items()):
            where = p.parent.relative_to(ROOT).as_posix()
            rows.append(f"| `{num}` | 子类占位（骨架节点，内容为空） | `{where}/{p.name}` |")
    (SKELETON_DIR / "928-路由详表.md").write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    return n_mod, len(nodes)


def route_rows() -> list[tuple[str, list[str], str, str]]:
    """ROUTES 按 references/ 实际文件过滤，再追加本库自建模块。

    2026-10-24：memread 直接 import ROUTES 常量，新用户建的 00 永远不在表里 →
    「刚写完的记忆查不到」。路由必须从磁盘长出来，不能靠写死的私人分类表。
    """
    ids = present_ids()
    covered: set[str] = set()
    rows: list[tuple[str, list[str], str, str]] = []
    for name, mods, what, how in ROUTES:
        keys = [k for k in re.findall(r"`([^`]+)`", mods) if k in ids]
        if not keys:
            continue
        covered.update(keys)
        if name == "系统·骨架节点":
            how = skeleton_paths_note()
        rows.append((name, keys, what, how))
    extra = sorted(k for k in ids if k not in covered)
    if extra:
        rows.append(("本库其它模块", extra, "按实际模块文件自动登记", "按文件名与关键词查"))
    return rows


def render() -> str:
    lines = [BEGIN, "| 大类 | 模块 | 装什么 | 怎么读 |", "| --- | --- | --- | --- |"]
    for name, keys, what, how in route_rows():
        lines.append(f"| {name} | {' '.join('`' + k + '`' for k in keys)} | {what} | {how} |")
    lines.append(f"| — | 小类详表 | 每个模块一行（关键词 / 同义说法 / 敏感） | 在 `references/928-路由详表.md`，需要时再读 |")
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
    n_mod, n_node = write_detail()
    print(f"已写入 {agents}（{len(new.splitlines())} 行）· 路由详表 {n_mod} 个模块 + {n_node} 个子类节点")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
