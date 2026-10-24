# -*- coding: utf-8 -*-
r"""看人设类问题的模块分怎么排的（带人称判定）。"""
import sys, os

sys.stdout.reconfigure(encoding="utf-8")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
os.chdir(_ROOT)
import frag

QS = [
    "我是个什么样的人",
    "你怎么看我的性格",
    "我的性格有什么毛病",
    "分析一下我这个人",
    "我平时喜欢什么东西",
    "我为什么老是自我否定",
    "你是什么性格",
    "你自己有没有变过",
    "你有什么毛病",
    "你会不会顺着我说话",
    "你的原则是什么",
    "你的立场",
    "你以前是什么样子的",
    "我以前的性格",
    "你讨厌我说什么话",
    "你自己的口味",
    "你有没有独立人格",
    "我的设定是什么",
    "用户的性格是什么样",
]
for q in QS:
    side = frag.subject_of(q)
    top = frag.module_scores(q)[:4]
    print(f"{q:<22} side={str(side):<5} " + "  ".join(f"{m}:{s:.2f}" for m, s in top))
