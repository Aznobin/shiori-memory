#!/usr/bin/env python3
r"""人设类探针：量「关于她」与「关于我自己」两类问题能不能读到对的模块。

用法：python tools\persona_probe.py [--frag]
  默认走 recall.retrieve()（线上自动注入那条路）；--frag 走片段层纯排序。

2026-10-22 第 2 版：第 1 版判据是 `set(got[:5]) & want`，太松——六个人的设模块
总有两三个会糊进前五，关掉人称加权它也报 18/18，量不出任何差别。现在分三个口径：
**@1（榜首对不对）/ @3 / 分边错了没（问她的却榜首是我）**。
"""
import sys, os

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"%USERPROFILE%\.agents\skills\user-memory\tools")
os.chdir(r"%USERPROFILE%\.agents\skills\user-memory")

import recall
import frag

SELF = {"78", "85", "86"}          # 关于我：性格 / 账 / 立场
USER = {"31", "01", "29", "22"}    # 关于她：侧写 / 背景 / 名字 / 情绪

PROBES = [
    # —— 问她（应该命中 31/01/29）——
    ("我是个什么样的人", {"31", "01"}),
    ("你怎么看我的性格", {"31"}),
    ("我的性格有什么毛病", {"31"}),
    ("分析一下我这个人", {"31", "01"}),
    ("我叫什么名字好", {"29"}),
    ("我的名字有哪些候选", {"29"}),
    ("我平时喜欢什么东西", {"01"}),
    ("我为什么老是自我否定", {"31"}),
    ("我以前的性格", {"31"}),
    ("我的设定是什么", {"01", "31"}),
    # —— 问我（应该命中 78/85/86）——
    ("你是什么性格", {"78"}),
    ("你自己有没有变过", {"85", "78"}),
    ("你有什么毛病", {"78"}),
    ("你会不会顺着我说话", {"78", "86"}),
    ("你的原则是什么", {"86"}),
    ("你有什么雷点", {"85"}),
    ("你讨厌我说什么话", {"78"}),
    ("你自己的口味", {"85"}),
    ("你怎么看自己做过的事", {"78", "85"}),
    ("你有没有独立人格", {"86"}),
    ("用户的性格是什么样", {"78"}),
    ("你的立场", {"86"}),
]


def docs_live(q):
    out = []
    for r in recall.retrieve(q, k=6):
        d = str(r[1])
        if d not in out:
            out.append(d)
    return out


def docs_frag(q):
    return [m for m, _s in frag.module_scores(q)][:6]


mode = "frag" if "--frag" in sys.argv else "live"
fn = docs_frag if mode == "frag" else docs_live

r1 = r3 = side_wrong = 0
for q, want in PROBES:
    got = fn(q)
    is_self_q = bool(want & SELF)
    other = USER if is_self_q else SELF
    a1 = bool(set(got[:1]) & want)
    a3 = bool(set(got[:3]) & want)
    bad = bool(set(got[:1]) & other) and not a1
    r1 += a1
    r3 += a3
    side_wrong += bad
    flag = "OK  " if a1 else ("SIDE" if bad else "MISS")
    print(f"{flag} {q:<16} @1={got[0]:<3} got={','.join(got[:4])}")

n = len(PROBES)
print(f"\n{mode}: @1 {r1}/{n} = {r1/n:.1%} · @3 {r3}/{n} = {r3/n:.1%} · 分边反了 {side_wrong}/{n}")
