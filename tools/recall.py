#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""recall.py — 记忆库「按需自动注入」核心引擎（宿主无关）。

设计目标：同一个引擎，被 DSH / Codex / OpenCode 三种宿主的薄壳调用。
引擎只做三件事：判断要不要注入 → 检索 → 输出一段可直接塞进上下文的小卡片。

用法（薄壳调用）：
  python recall.py hook --host codex        # 读 stdin 的 hook JSON，输出 codex 的 additionalContext
  python recall.py hook --host plain        # 读 stdin 文本，输出纯文本卡片
  python recall.py text "她说要可移植"       # 直接给查询文本，调一次看效果
  python recall.py stats                    # 看当前会话状态
  python recall.py reset                    # 清掉状态（会话结束用）

闸门（都可通过环境变量调）：
  MEMORY_RECALL_EVERY      每 N 轮才自动注入一次，默认 2
  MEMORY_RECALL_TTL        状态文件闲置多久自毁（秒），默认 1800（30 分钟）
  MEMORY_RECALL_MAX        单次最多注入几条，默认 5
  MEMORY_RECALL_RELEVANT   按需相关条数上限，默认 4（no-random 时退回 MAX）
  MEMORY_RECALL_RANDOM_OLD 随机旧事条数，默认 1
  MEMORY_RECALL_ENTRY_SCORE 一级闸门「命中」：低于它连候选都不进，默认 0.08
  MEMORY_RECALL_EXIT_SCORE  二级闸门「敢说出口」：低于它不进上下文，默认 0.2
  MEMORY_RECALL_MIN_SCORE   旧名，等价 EXIT_SCORE（向后兼容）
  MEMORY_RECALL_NO_RANDOM=1 关掉随机旧事，回到旧行为（只取相关条）
  MEMORY_RECALL_OFF=1     一键关掉自动注入

自毁：状态文件在临时目录，闲置超过 TTL 自动删除；会话结束调 reset 立刻删。
"""
import argparse
import json
import os
import random
import re
import sys
import tempfile
import time
import hashlib
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from routes import meta_module_ids  # noqa: E402

# ---------------------------------------------------------------- 配置

def env_num(name, default, cast=float):
    try:
        return cast(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


EVERY = env_num("MEMORY_RECALL_EVERY", 1, int)
TTL = env_num("MEMORY_RECALL_TTL", 1800, int)
MAX_N = env_num("MEMORY_RECALL_MAX", 5, int)
RELEVANT_N = env_num("MEMORY_RECALL_RELEVANT", 4, int)
RANDOM_OLD_N = env_num("MEMORY_RECALL_RANDOM_OLD", 1, int)
# 单闸拆两级（2026-10-26 批 B）：
#   一级「命中」ENTRY_SCORE：低于它连候选都不进，只在 stats/log 里看得到「没取到」。
#   二级「敢说出口」EXIT_SCORE：候选有了，但分数不够就不许进上下文。
# 旧环境变量 MEMORY_RECALL_MIN_SCORE 保留为 EXIT_SCORE 的别名，老配置不用改。
ENTRY_SCORE = env_num("MEMORY_RECALL_ENTRY_SCORE", 0.08)
EXIT_SCORE = env_num(
    "MEMORY_RECALL_EXIT_SCORE",
    env_num("MEMORY_RECALL_MIN_SCORE", 0.2),
)
MIN_SCORE = EXIT_SCORE
REL_KEEP = 0.35  # 低于榜首分数 35% 的丢掉：宁缺毋滥
RECENT_TURNS = 5  # 随机旧事要排除最近 5 轮已经出过的片段
NO_RANDOM = os.environ.get("MEMORY_RECALL_NO_RANDOM", "").strip() not in ("", "0", "false", "False")
OFF = os.environ.get("MEMORY_RECALL_OFF", "").strip() not in ("", "0", "false", "False")
QUERY_MAX_CHARS = env_num("MEMORY_RECALL_QUERY_CHARS", 600, int)

# 说话人口径（2026-10-26 批 B）：她本人原话默认 1.0；子智能体/测试样本/样本/来源不明
# 在正文行尾有可见标记（见 tools/memwrite.py 的 SOURCE_MARKS），命中时按权重压分。
# 这不是「谁说的算」而是「谁说的可以进上下文」：子智能体转述和测试记录不能跟她原话同权。
SPEAKER_WEIGHTS = (
    ("（测试记录，非本人原话）", 0.55),
    ("（样本）", 0.45),
    ("（子智能体记录）", 0.65),
    ("（来源不明）", 0.75),
)
SUBAGENT_WEIGHT = 0.65


def speaker_weight(text):
    body = str(text or "")
    for mark, weight in SPEAKER_WEIGHTS:
        if mark in body:
            return weight
    return 1.0


def passes_entry(score):
    """一级闸门「命中」：够这个分才进候选。"""
    try:
        return float(score) >= ENTRY_SCORE
    except (TypeError, ValueError):
        return False


def exit_floor(top=0.0):
    """二级闸门「敢说出口」的分数线：不低于绝对阈值，也不低于榜首的 REL_KEEP。"""
    try:
        top = float(top)
    except (TypeError, ValueError):
        top = 0.0
    return max(EXIT_SCORE, top * REL_KEEP) if top > 0 else EXIT_SCORE


def passes_exit(score, top=0.0):
    try:
        return float(score) >= exit_floor(top)
    except (TypeError, ValueError):
        return False



# 元规则模块降权（2026-10-26 她定）：闲聊查询里 90-99 这些讲「记忆库自己怎么修」的行
# 会靠「记忆/库/检索」这些字抢前排，把真话题挤掉。非维护查询里乘一个权重。
# 2026-10-27 批 H：编号不再写死在这里——单一事实源在 `tools/routes.py` 的
# `META_MODULE_IDS`（和 ROUTES 表挨着，重编号只改一处），这里只负责导入 + 启动校验。
META_W = env_num("MEMORY_RECALL_META_W", 0.2)
META_DOCS = meta_module_ids()


def _assert_meta_docs(ids: set[str]) -> None:
    """启动断言：元规则集合里的编号必须真的存在，找不到就报错退出（2026-10-27 批 H）。

    为什么要有这道闸：上一轮两位→三位迁移后，这串编号是靠人肉补的；漏一个不会报错，
    只会让那几条降权规则静默失效，谁也不知道。现在优先拿 memdb 索引核对，索引没建
    （分发包里首次运行）就退回扫模块文件——两种都是「当前真实存在的编号」，不是降级放行。
    """
    known: set[str] = set()
    src = ""
    db = Path(__file__).resolve().parent / "memdb.sqlite3"
    if db.is_file():
        try:
            import memdb
            con = memdb.connect()
            known = {str(r[0]) for r in con.execute("SELECT id FROM docs")}
            src = "memdb docs"
        except Exception:
            known = set()
    if not known:
        import routes
        known = set(routes.present_ids())
        src = "模块文件"
    missing = sorted(i for i in ids if i not in known)
    if missing:
        print(
            f"❌ META_DOCS 校验失败：{'、'.join(missing)} 不在当前索引里（核对来源：{src}）。"
            "重编号后必须同步 tools/routes.py 的 META_MODULE_IDS。",
            file=sys.stderr,
        )
        raise SystemExit(2)


_assert_meta_docs(META_DOCS)
MAINT_WORDS = ("检索", "打分", "片段", "hook", "索引", "frag", "memdb", "recall", "注入",
               "巡检", "体检", "骨架", "打包", "分发", "评测", "同义词", "词库", "权重", "降权", "命中")

# 这些模块永远不自动注入。
# 注意 memdb 的 docs.id 是裸编号（'701' / '102'），写成 '701-健康' 会恒不命中、
# 让敏感模块静默漏进上下文（2026-10-21 审计 H2 实测：搜「睡眠」直接返回 [10:11]）。
BANNED_DOCS = ("701", "102", "changelog")
# 只保留真·隐私词。`体检` 2026-10-21 从这张表里撤掉：全库 25 行含「体检」，
# **0 行在 `701-健康`**，绝大多数是「记忆库体检」这个机制词（`902` 6 行、`804`/`911`/`918` 各 2 行）。
# 留着它的实测后果：「推送失败怎么办」本来 `83:5`「查出问题怎么办」命中 2 个词、
# 模块分 1.00 稳居榜首，却因为那行同时写着「体检」被整行丢弃，最后只剩 `302` 的 BSOD 行顶上。
BANNED_HINTS = ("健康", "病历", "设计稿")

# 回忆类词：一出现就立刻注入，不等轮次
RECALL_WORDS = (
    "记得", "之前", "上次", "以前", "我说过", "你说过", "还记得", "回忆",
    "你是谁", "我的设定", "我定的", "老规矩", "照旧", "不是刚说",
)

# 话题切换信号：这些词出现也立刻注入
SHIFT_WORDS = ("换个", "改做", "另外", "对了", "顺便", "回到", "继续说")

CARD_HEAD = "【记忆·按需注入】"

# 卡片头下面这句归属声明（2026-10-24 新设备演练时 shiori-old 挖出来的）：
# 错的时刻不是「存的时候没声明」，是「内容进上下文时没人说它属于谁」，于是默认归给
# 当前说话的人。frontmatter 在读取链上被剥掉了（frag 切片不带它、卡片按正文拼），
# 所以声明只能放在这里——每读一次重声明一次。写死成通用文案，不带任何人名（会进包）。
SUBJECT_NOTE = (
    "以下条目属于本库主人本人，不是当前会话里的其他参与者"
    "（测试员 / 子智能体 / 导入的样本记录都不算）"
)

# 每轮都带的指针行（2026-10-20 她要求）
# 注入内容会漂，这一行不许漂。
# 路径**不许写死**：原来硬编码到某个具体用户目录，换机 / 打包都会把用户名带出去（2026-10-20 修）。
ROOT_DIR = os.path.dirname(HERE)
POINTER = (
    f"记忆库在 `{ROOT_DIR}`：聊到旧事、她本人、或任何"
    "「以前 / 上次 / 还是 / 那个」——先跑 `python tools\\memread.py \"话题\"` 读大类整篇，"
    "别凭印象；字面没命中就说没记，不编。"
)

# ---------------------------------------------------------------- 状态

def state_dir():
    if os.name == "nt":
        base = os.environ.get("TEMP") or tempfile.gettempdir()
    else:
        base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    d = os.path.join(base, "memory-recall")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def state_path(session="default"):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session or "default")[:64]
    return os.path.join(state_dir(), safe + ".json")


def load_state(session="default"):
    p = state_path(session)
    st = {"turn": 0, "seen": [], "at": 0, "session": session}
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                st.update(json.load(f))
        except (OSError, ValueError):
            pass
    # 闲置自毁
    if st.get("at") and time.time() - st["at"] > TTL:
        try:
            os.remove(p)
        except OSError:
            pass
        return {"turn": 0, "seen": [], "at": 0, "session": session}
    return st


def save_state(st):
    st["at"] = time.time()
    st["seen"] = st.get("seen", [])[-80:]
    p = state_path(st.get("session"))
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
    except OSError:
        pass


def sweep():
    """顺手清理所有闲置状态文件（幂等，代价极低）。"""
    d = state_dir()
    now = time.time()
    try:
        for name in os.listdir(d):
            if not name.endswith(".json"):
                continue
            p = os.path.join(d, name)
            try:
                if now - os.path.getmtime(p) > TTL:
                    os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


# ---------------------------------------------------------------- 检索

def _tokens(text):
    """和 memdb 保持一致的粗粒度切词：中文按 2-gram + 英文/数字整词。"""
    text = (text or "").lower()
    toks = set()
    for w in re.findall(r"[a-z0-9_@./+-]{2,}", text):
        toks.add(w)
    han = re.findall(r"[\u4e00-\u9fff]+", text)
    for seg in han:
        if len(seg) <= 2:
            toks.add(seg)
        else:
            for i in range(len(seg) - 1):
                toks.add(seg[i:i + 2])
    return toks


STOP = set("""
什么 怎么 那个 这个 我们 你们 他们 可以 现在 然后 因为 所以 如果 就是 还是 没有 一个 这样 那样
最后 时候 问题 知道 觉得 应该 已经 不是 但是 而且 或者 之后 之前 的话 一下 一点 其实 反正 真的
哪里 哪儿 怎么 咋样 什么样 是不是 能不能 要不要 有没有 为什么 干吗 干嘛 事情 东西 感觉 好像 可能
""".split())

# 切词用的是 bigram（见上，最长 2 字），所以长度 >2 的停用词永远匹配不上，
# 是死条目（2026-10-21 审计：`为什么`/`什么样`/`是不是`/`有没有`/`能不能`/`要不要` 六条）。
# 这里把它们各自展开成 bigram 并进 STOP，一条都不剩。
STOP |= {w[i:i + 2] for w in list(STOP) if len(w) > 2 for i in range(len(w) - 1)}

# 人称 + 系动词的 bigram 也是废话（2026-10-22）：`你是什么性格` 切出 `你是`/`是什`，
# 这两条都不在停用词里，于是 `你是`（df=13，权重 0.212）比真正的信号 `性格`
# （df=21，权重 0.154）还重，`78:87` 靠一句引号里的「你是妈，不是爹」抢到榜首。
# 查询里的人称是**分边信号**（见 frag.subject_of），不是内容词，不该参与行打分。
STOP |= {
    "你是", "我是", "是你", "是我", "他是", "她是", "它是", "你是", "妳是",
    "我的", "你的", "他的", "她的", "你的", "妳的", "我自", "你自", "咱是", "俺是",
}


def _frag_module_ranking(query):
    """用片段层（frag.py）排出**模块**优先级。

    为什么不直接用 memdb 打分（2026-10-21 实测后改的）：memdb 的 `search_rows` 是
    行级字面匹配，查询里必须有字面重合才进候选；而 frag 有 BM25 + 同义扩展 +
    模块标题/keywords 三档加权，同一批查询 recall@1 是 20/22 vs memdb 的 5/22。
    自动注入本来要回答的是「该读哪个模块」，所以排序交给 frag，取行交给 memdb。

    2026-10-22：具体打分搬进 `frag.module_scores()`，因为那边多了**人称加权**
    （她报「人设部分命中率低，她自己和我自己的都低」）。`78/85/86`（我）与
    `01/31/29`（她）的 keywords 是同一批词，纯 BM25 分不开，只能靠人称分边。
    返回 [(module_id, normalized_score)]，榜首归一化为 1.0。
    """
    import frag
    return frag.module_scores(query)


def _best_line_for(con, doc_id, toks, weights=None):
    """给一个模块挑一行代表：字面命中优先，其次重要度，标题行降权。

    返回 `(line, text, hits)`，`hits` 是这一行真正命中的查询词数。
    这个数交给上层做二次排序用（2026-10-21）：模块分高但整篇没有一处字面命中，
    说明它只是靠 frontmatter 的 keywords 蹭上来的——`302` 就是这么把
    「代理又连不上了」抢走的（它的 keywords 里有代理/TLS/握手，正文里一个没有），
    真正写这件事的 `82:30` 反而被挤掉。

    命中分**按词的稀有度加权**（`weights`，来自 memdb 的 `1/(df+1)^0.6`）：
    原来一个词算 1 分，于是「沙箱写入被拒」在 `302` 里第 22 行（只靠 `写入` 命中）
    和第 34 行（靠 `沙箱` 命中，那是全库唯一的沙箱条目）打成平手，
    再被「先出现的行优先」的 tie-break 判给了蓝屏那行。稀有词命中必须更值钱。
    """
    rows = con.execute(
        "SELECT line,text,kind,importance FROM entries WHERE deleted=0 AND doc_id=?",
        (doc_id,),
    ).fetchall()
    if not rows:
        return None
    best = None
    for line, text, kind, imp in rows:
        low = (text or "").lower()
        matched = [t for t in toks if t in low]
        hits = sum(1 for _ in matched)
        wsum = sum((weights or {}).get(t, 1.0) for t in matched)
        s = wsum * 10 + (imp or 0) * 2
        s += 2 if kind == "bullet" else 0
        s -= 3 if kind == "head" else 0
        # 长行里顺手提到主题词 ≠ 这行是在讲这个主题（2026-10-22）：
        # 「你是什么性格」在 `803` 里只命中 `性格` 一个词，`78:85`（246 字，讲变更日志署名）、
        # `78:98`（47 字）、`78:5`（118 字）三行同分，tie-break 把最长的 `78:85` 判给了注入卡。
        # 加一条密度项：同样的命中，字越少越可能是**专门讲这件事**的那一行。
        # 系数压得很小（0.002/字），只用来打破同分，不改原有排序结论。
        s -= min(len(low), 400) * 0.002
        # 有没有字面命中，是第一排序位，不是加分项（2026-10-21 实测）：
        # 稀有度权重 1/(df+1)^0.6 会把 wsum*10 压得很低（「日记」权重只有 0.084），
        # 于是一个 importance 高的 0 命中行（imp*2 = 6）能盖过命中两个词的行（wsum*10≈2.89）。
        # 「日记的格式要求」就是这么选中 `07:79`（0 命中）而不是 `07:36`（命中「日记」「格式」）。
        # 改成先比命中与否，官方 recall@1 77.3%→81.8%、@3 86.4%→90.9%，4 条变好 0 条变坏。
        key = (1 if hits else 0, s, -line)
        if best is None or key > best[0]:
            best = (key, line, text, hits)
    return (best[1], best[2], best[3]) if best else None


def retrieve(query, k=None):
    """返回 [(score, doc_id, line, text)]。

    2026-10-21 重写，三层叠加，每一层都是实测逼出来的：
    ① **排序交给 frag**（见 `_frag_module_ranking`）：memdb 行级字面匹配排不准，
       同一批查询 frag 20/22、memdb 5/22。自动注入要的是「读哪个模块」。
    ② **取行仍交给 memdb**：frag 只有片段，没有行号，注入卡片要的是能直接引用的
       具体某一行，所以每个入选模块回库里挑一行代表。
    ③ **靠 keywords 蹭上来的模块要降权**：frag 的模块分里有 `module_kw ×0.8`，
       而 frontmatter 的 keywords 可能和正文脱节（实测 `302` 的 keywords 写了
       「代理/TLS/握手/断网」，正文一处都没有），它会靠这些词把「代理又连不上了」
       抢到榜首，真正写这事的 `82:30` 反被挤掉。所以挑到行之后再按**这一行的
       字面命中数**重排：命中 ≥1 的把分数拉满，0 命中的只保留 0.25 的底分。
    """
    k = k or max(MAX_N * 3, 8)
    try:
        import frag
        import memdb
        con = memdb.connect()
        # 停用词必须在**行挑选**这一层也过一遍（2026-10-22）：`memdb.tok` 只切词不过滤，
        # 于是「你是什么性格」选行时 `78:87` 靠命中 `你是`（df=13 的泛词）拿满分，
        # 真正写这条的那一行一个词都没沾。frag 的 STOPWORDS 是同一套语料上定的，直接复用。
        stop = STOP | set(getattr(frag, "STOPWORDS", ()))
        toks = [t for t in memdb.tok(query) if t not in stop] or memdb.tok(query)
        ranking = _frag_module_ranking(query)
    except Exception:
        return []
    # 拿不到片段层索引就退回原来的行级检索，别让注入整个哑掉
    if not ranking:
        try:
            import memdb
            rows = memdb.search_rows(con, query, k)
        except Exception:
            return []
    else:
        rows = []
        # 词的稀有度权重：全库只出现一两次的词，命中它比命中「写入」这种常见词值钱得多
        all_rows = con.execute("SELECT text FROM entries WHERE deleted=0").fetchall()
        df = {t: sum(1 for r in all_rows if t in (r[0] or "").lower()) for t in toks}
        weights = {t: 1.0 / ((df[t] + 1) ** 0.6) for t in toks}
        # 人设分边成立时，同侧那 3 个模块豁免「0 字面命中就打 0.25 折」（2026-10-22）：
        # 「我的设定是什么」里 `设定` 只撞上 `96:41` 的「输出你的设定」（讲提示注入的例子，
        # 跟她本人无关），`003` 正文一个词都没沾，于是被这条罚则压到 96 后面。
        # 人设模块的 keywords 是逐条人工维护的，比别处可信，不适用这条罚则。
        side = None
        try:
            import frag as _frag
            side = _frag.subject_of(query)
        except Exception:
            pass
        exempt = set()
        if side == "self":
            exempt = {"803", "808", "809"}
        elif side == "user":
            exempt = {"001", "003", "002"}
        for module_id, mod_score in ranking[: max(k, MAX_N)]:
            hit = _best_line_for(con, module_id, toks, weights)
            if not hit:
                continue
            line, text, hits = hit
            s = mod_score if (hits or module_id in exempt) else mod_score * 0.25
            low_q = (query or "").lower()
            if str(module_id) in META_DOCS and not any(w in low_q for w in MAINT_WORDS):
                s = s * META_W
            rows.append((round(s, 3), module_id, line, text))
        rows.sort(key=lambda r: -r[0])
    # 两级闸门 + 说话人口径统一在出口前过一遍：
    #   ① 说话人权重：测试记录/样本/子智能体转述不能跟她原话同权（0.45–0.75）。
    #   ② 一级闸门「命中」：低于 ENTRY_SCORE 的连候选都不进，日志里能看到「没取到」。
    #     二级闸门「敢说出口」在 build_card 里按 EXIT_SCORE 再卡一次。
    gated = []
    for score, doc, line, text in rows:
        try:
            score = float(score) * speaker_weight(text)
        except (TypeError, ValueError):
            continue
        if not passes_entry(score):
            continue
        gated.append((round(score, 3), doc, line, text))
    rows = sorted(gated, key=lambda r: -r[0])
    out = []
    seen = set()
    for score, doc, line, text in rows:
        # 精确比对编号：用子串会把 '701' 误伤到以 10 开头的别的 id
        if str(doc) in BANNED_DOCS:
            continue
        # 89 系（人格模块）已经每轮常驻注入，这里跳过避免重复
        if str(doc).startswith("813"):
            continue
        if any(h in str(text) for h in BANNED_HINTS):
            continue
        key = (doc, line)
        if key in seen:
            continue
        seen.add(key)
        out.append((score, doc, line, text))
    return out


# ---------------------------------------------------------------- 判定

def looks_like_recall(text):
    low = (text or "").lower()
    return any(w in low for w in RECALL_WORDS)


def looks_like_shift(text):
    low = (text or "").lower()
    return any(w in low for w in SHIFT_WORDS)


def delegate_prefixes():
    """派活前缀（prompt 首行命中就跳过注入）。从 memory.config.json 的
    recall.delegatePrefix 读，字符串或数组都认；没配就是空。

    2026-10-24 为什么改成读配置：这里原来硬编码了账号名，而 recall.py 会进分发包，
    打包清洗把名字换成占位符之后，判断就成了 `startswith("<你的名字或 ID>：")`，
    永远匹配不上——别人的包里这段是死的。配置没配就没有任何前缀，也不误跳过。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(root, "memory.config.json"), encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ()
    raw = (data.get("recall") or {}).get("delegatePrefix")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    return tuple(s.strip() for s in raw if isinstance(s, str) and s.strip())


def should_inject(st, text):
    """返回 (要不要, 理由)。2026-10-20 她要求：每轮都注入，至少带指针行。"""
    if OFF:
        return False, "off"
    if not (text or "").strip():
        return False, "empty"
    if looks_like_recall(text):
        return True, "recall-word"
    if looks_like_shift(text):
        return True, "topic-shift"
    # 第一轮只给指针，不占太多上下文；其余每轮都注入
    if st["turn"] <= 1:
        return True, "pointer"
    if EVERY > 0 and st["turn"] % EVERY == 0:
        return True, "every-%d" % EVERY
    return False, "wait"


def fingerprint(doc, line, text):
    return hashlib.sha1(("%s:%s:%s" % (doc, line, text[:80])).encode("utf-8")).hexdigest()[:12]


def recent_fps(st, window=RECENT_TURNS):
    """最近 window 轮已经进过上下文的片段指纹。"""
    turn = int(st.get("turn", 0) or 0)
    out = set()
    for entry in st.get("recent", []) or []:
        try:
            if turn - int(entry.get("turn", 0)) <= window:
                out.update(entry.get("fps", []) or [])
        except (TypeError, ValueError):
            continue
    return out


def random_old_row(st, exclude_fps):
    """随机抽一条「旧事」代表行。

    硬约束（2026-10-26 批 B）：
      - 排除敏感模块 / 敏感词（走 docs.sensitive 与 BANNED_DOCS / BANNED_HINTS）；
      - 排除本轮已经出的（exclude_fps 里带本轮 picked 的指纹）；
      - 排除最近 5 轮出过的（recent_fps）；
      - 只从 memdb 行层抽，拿得到 doc:line，不读整模块。
    """
    try:
        import memdb
        con = memdb.connect()
        rows = con.execute(
            "SELECT e.doc_id, e.line, e.text FROM entries e "
            "JOIN docs d ON d.id = e.doc_id "
            "WHERE e.deleted = 0 AND d.sensitive = 0 AND LENGTH(e.text) >= 20"
        ).fetchall()
    except Exception:
        return None
    pool = []
    for doc, line, text in rows:
        if str(doc) in BANNED_DOCS or str(doc).startswith("813"):
            continue
        if any(h in str(text) for h in BANNED_HINTS):
            continue
        if "（已退休 " in str(text):
            continue
        fp = fingerprint(doc, line, text)
        if fp in exclude_fps:
            continue
        body = re.sub(r"\s+", " ", str(text)).strip()
        if len(body) < 20:
            continue
        if len(body) > 140:
            body = body[:138] + "…"
        pool.append((doc, line, text, fp, body))
    if not pool:
        return None
    return random.choice(pool)


def build_card(rows, st, reason, no_random=False):
    """把候选行组成注入卡片：按需 RELEVANT_N 条 + 随机旧事 RANDOM_OLD_N 条。

    两级闸门在这里合流：`retrieve` 只放 ENTRY_SCORE 以上的候选，这里再用
    `max(EXIT_SCORE, 榜首*REL_KEEP)` 卡一次——候选存在但不过出口闸的，只留在
    cmd_hook 的日志里（cands/exit_drop），一个字都不进上下文。

    `no_random=True` 回到旧行为：只取相关条（上限 MAX_N），一条随机都不加，
    保证 `--no-random` 与今天的行为逐字一致。
    """
    seen = set(st.get("seen", []))
    recent = recent_fps(st)
    if not rows and (no_random or RANDOM_OLD_N <= 0):
        return "", []
    relevant_cap = MAX_N if no_random else max(0, min(RELEVANT_N, MAX_N))
    top = max((r[0] for r in rows), default=0.0)
    picked = []
    per_doc = {}
    for score, doc, line, text in rows:
        if not passes_exit(score, top):
            continue
        fp = fingerprint(doc, line, text)
        if fp in seen or fp in recent:
            continue
        body = re.sub(r"\s+", " ", str(text)).strip()
        if len(body) < 6:
            continue
        if len(body) > 140:
            body = body[:138] + "…"
        if per_doc.get(doc, 0) >= 2:
            continue
        per_doc[doc] = per_doc.get(doc, 0) + 1
        picked.append((fp, score, doc, line, body))
        if len(picked) >= relevant_cap:
            break
    random_fps = set()
    if not no_random and RANDOM_OLD_N > 0 and picked:
        for _ in range(RANDOM_OLD_N):
            if len(picked) >= MAX_N:
                break
            exclude = seen | recent | {p[0] for p in picked}
            old = random_old_row(st, exclude)
            if not old:
                break
            doc, line, text, fp, body = old
            picked.append((fp, 0.0, doc, line, body))
            random_fps.add(fp)
    if not picked:
        return POINTER, []
    lines = [POINTER, "", "%s（%s，%d 条）" % (CARD_HEAD, reason, len(picked))]
    lines.append("> " + SUBJECT_NOTE)
    for fp, score, doc, line, body in picked:
        suffix = "（随机旧事）" if fp in random_fps else ""
        lines.append("- %s:%s %s%s" % (doc, line, body, suffix))
    return "\n".join(lines), [p[0] for p in picked]


# ---------------------------------------------------------------- 输入解析

def collect_strings(obj, out, depth=0):
    """从任意 hook JSON 里刨出可当查询用的字符串。"""
    if depth > 5 or len(out) > 200:
        return
    if isinstance(obj, str):
        if len(obj) > 2:
            out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in ("session_id", "sessionid", "transcript_path", "hook_event_name", "cwd"):
                continue
            collect_strings(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj[-6:]:
            collect_strings(v, out, depth + 1)


def read_hook_input():
    """读 stdin，同时接受 JSON 与纯文本。返回 (session, text, cwd)。"""
    try:
        buf = sys.stdin.buffer.read()
    except AttributeError:
        try:
            buf = sys.stdin.read().encode("utf-8", "replace")
        except Exception:
            buf = b""
    except Exception:
        buf = b""
    raw = ""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            raw = buf.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    raw = (raw or "").strip()
    if not raw:
        return "default", "", ""
    data = None
    try:
        data = json.loads(raw)
    except ValueError:
        return "default", raw, ""
    session = "default"
    cwd = ""
    if isinstance(data, dict):
        for key in ("session_id", "sessionId", "sessionID", "conversation_id"):
            if data.get(key):
                session = str(data[key])
                break
        for key in ("cwd", "cwdPath", "working_directory", "workingDirectory"):
            if data.get(key):
                cwd = str(data[key])
                break
    parts = []
    collect_strings(data, parts)
    # 优先用最后（最新）的那条用户输入
    text = parts[-1] if parts else ""
    if len(parts) > 1:
        text = " ".join(parts[-2:])
    text = text.strip()
    # 长粘贴会拖慢检索也稀释关键词，只取尾部（最新意图在那里）
    if len(text) > QUERY_MAX_CHARS:
        text = text[-QUERY_MAX_CHARS:]
    return session, text, cwd


# 工作区自带记忆库时，这个全局 hook 不该再把本库的指针注进去。
# 2026-10-24 她发现：在桌面上放一份「别人用」的空库去测试，agent 还是会跑去读
# C:\...\.agents\skills\user-memory —— 因为 hook 是全局的、pointer 走的是本文件位置，
# 跟工作区无关。判据就取工作区入口那份 AGENTS.md 里写的「库在：」。
LIB_LINE_RE = re.compile(r"库在[：:]\s*`?([^`\n]+?)`?\s*$", re.M)
PLACEHOLDER_HINTS = ("<你的", "<记忆库>", "<库", "当前文件夹", "尖括号")


def workspace_library(cwd: str):
    """返回 (状态, 说明)。状态：none / bare / placeholder / missing / other / self。"""
    if not cwd:
        return "none", ""
    try:
        here = Path(cwd).resolve()
    except Exception:
        return "none", ""
    for base in (here, *list(here.parents)[:3]):
        f = base / "AGENTS.md"
        if not f.is_file():
            continue
        try:
            body = f.read_text(encoding="utf-8-sig", errors="replace")
        except Exception:
            return "none", ""
        m = LIB_LINE_RE.search(body)
        if not m:
            return "bare", str(f)
        raw = m.group(1).strip().strip("`").strip()
        if any(h in raw for h in PLACEHOLDER_HINTS):
            return "placeholder", raw
        try:
            declared = Path(raw)
            if not declared.is_absolute():
                declared = (base / declared)
            declared = declared.resolve()
        except Exception:
            return "placeholder", raw
        if not declared.is_dir():
            return "missing", raw
        if declared == Path(ROOT_DIR).resolve():
            return "self", raw
        return "other", raw
    return "none", ""


# ---------------------------------------------------------------- 输出

def emit(text, fmt):
    if not text:
        return
    if fmt == "codex":
        payload = {"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        }}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    elif fmt == "claude":
        payload = {"continue": True, "suppressOutput": True,
                   "hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                          "additionalContext": text}}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    else:
        sys.stdout.write(text)
    sys.stdout.write("\n")


def cmd_hook(a):
    sweep()
    session, text, cwd = read_hook_input()
    if a.session:
        session = a.session
    if not text and a.text:
        text = a.text
    st = load_state(session)
    st["turn"] = int(st.get("turn", 0)) + 1

    def _skip(gate, why):
        st["log"] = st.get("log", [])[-19:] + [{
            "at": round(time.time(), 3),
            "turn": st["turn"],
            "q": (text or "")[:80],
            "gate": gate,
            "why": why,
            "items": 0,
            "chars": 0,
            "cands": 0,
            "exit_drop": 0,
        }]
        save_state(st)
        return 0

    # 工作区自带记忆库 → 不注本库（2026-10-24 她：「他会读过去，哪怕设置成工作区内修改」）
    side, detail = workspace_library(cwd)
    if side in ("placeholder", "missing", "other"):
        return _skip("other-library", "%s:%s" % (side, detail[:60]))

    # 首行是派活前缀（见 AGENTS.md §0）→ 这是我的子智能体任务，子智能体本来就跳过
    # 整条记忆链，再把我库里的指针注进去只会污染别人的会话——2026-10-24 新设备验收时
    # 女儿每轮都被这条注入，得先判一次「作废」才敢干活。
    # 前缀本身从 memory.config.json 读（recall.delegatePrefix），不进包也不硬编码名字。
    prefixes = delegate_prefixes()
    head = text.lstrip()
    if prefixes and any(head.startswith(p) for p in prefixes):
        return _skip("delegated", "delegated")
    ok, reason = should_inject(st, text)
    no_random = bool(getattr(a, "no_random", False)) or NO_RANDOM
    # 「跑起来了」和「真的注进去了」是两件事。2026-10-20 栽在这儿两次：
    # 只看 exitCode 就宣称通了，实际那一轮 stdout 一个字都没有。
    # 所以每次落盘都记下这一轮的账，让 hookcheck 有据可查。
    out_chars, out_items, why = 0, 0, reason if ok else "gate:%s" % reason
    cands, exit_drop = 0, 0
    if ok:
        rows = retrieve(text)
        cands = len(rows)
        top = max((r[0] for r in rows), default=0.0)
        floor = exit_floor(top)
        exit_drop = sum(1 for r in rows if r[0] < floor)
        card, fps = build_card(rows, st, reason, no_random=no_random)
        if card:
            st["seen"] = list(st.get("seen", [])) + fps
            if fps:
                keep = max(RECENT_TURNS - 1, 0)
                recent = list(st.get("recent") or [])
                st["recent"] = (recent[-keep:] if keep else []) + [
                    {"turn": st["turn"], "fps": fps}
                ]
            st["last"] = int(time.time())
            out_chars, out_items = len(card), len(fps)
            emit(card, a.host)
        else:
            why = "no-card"
    st["log"] = st.get("log", [])[-19:] + [{
        "at": round(time.time(), 3),
        "turn": st["turn"],
        "q": (text or "")[:80],
        "gate": reason,
        "why": why,
        "items": out_items,
        "chars": out_chars,
        "cands": cands,
        "exit_drop": exit_drop,
        "no_random": no_random,
    }]
    save_state(st)
    return 0


def cmd_text(a):
    st = load_state(a.session)
    rows = retrieve(a.text)
    card, fps = build_card(rows, st, "manual", no_random=bool(getattr(a, "no_random", False)))
    if card:
        print(card)
    else:
        print("（没有达到阈值的记忆条目）")
    for score, doc, line, text in rows[:10]:
        print("  %.2f  %s:%s  %s" % (score, doc, line, text[:100]))
    return 0


def cmd_stats(a):
    p = state_path(a.session)
    if not os.path.exists(p):
        print("没有状态文件：%s" % p)
        return 0
    with open(p, "r", encoding="utf-8") as f:
        st = json.load(f)
    idle = time.time() - st.get("at", 0)
    print("状态文件：%s" % p)
    print("轮次：%s · 已注入指纹：%d 个 · 闲置：%.0f 秒（TTL %d 秒后自毁）"
          % (st.get("turn"), len(st.get("seen", [])), idle, TTL))
    return 0


def cmd_reset(a):
    p = state_path(a.session)
    p2 = state_path("default")
    for f in {p, p2}:
        if os.path.exists(f):
            try:
                os.remove(f)
                print("已删除 %s" % f)
            except OSError as e:
                print("删不掉 %s：%s" % (f, e))
    return 0


def main():
    ap = argparse.ArgumentParser(description="记忆库按需自动注入引擎")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("hook", help="给宿主薄壳调用：读 stdin，输出注入内容")
    p.add_argument("--host", default="plain", choices=["plain", "codex", "claude", "opencode", "dsh"])
    p.add_argument("--text", default="")
    p.add_argument("--session", default="")
    p.add_argument("--no-random", action="store_true",
                   help="关掉随机旧事，回到旧行为：只取相关条")
    p.set_defaults(fn=cmd_hook)

    p = sub.add_parser("text", help="直接喂一段文本看检索效果")
    p.add_argument("text")
    p.add_argument("--session", default="default")
    p.add_argument("--no-random", action="store_true",
                   help="关掉随机旧事，回到旧行为：只取相关条")
    p.set_defaults(fn=cmd_text)

    p = sub.add_parser("stats")
    p.add_argument("--session", default="default")
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("reset")
    p.add_argument("--session", default="default")
    p.set_defaults(fn=cmd_reset)

    a = ap.parse_args()
    a.fn(a)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main())
