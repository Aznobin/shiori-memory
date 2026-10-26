#!/usr/bin/env python3
"""dsh-meme 表情面板「只画前 240 张」补丁。

问题:输入框那张笑脸弹出的选择器(client.js / MemeBoard)只渲染
PICKER_MAX_CELLS = 240 个格子,底部写一句「只画了前 240 张,还有 N 张——用搜索或
筛选取一部分」,剩下的图不搜就看不到。

改法:首屏照样只铺 240 格(上千格一次性铺出来 = 上千 DOM + 上千图片请求),
但网格滚到底部附近就再铺 PICKER_BATCH 格,另外给一个「全部铺开」按钮。
不动其它行为,不改颜色,不加依赖。

幂等:已经打过补丁(marker PICKER_BATCH)直接报 already patched 退出 0。
写盘走临时文件 + os.replace,不原地改 pnpm store 的硬链接副本。

用法:
    python dsh-meme-picker-render.py [client.js 路径]
"""

import os
import re
import shutil
import sys
import tempfile

DEFAULT_TARGET = os.path.join(
    os.path.expanduser("~"),
    ".dsh", "profiles", "desktop", "node_modules", "dsh-meme", "client.js",
)
MARKER = "PICKER_BATCH"


def replace_once(src, old, new, label):
    if src.count(old) != 1:
        raise SystemExit("[fail] anchor %r hit %d times (plugin version changed?)"
                         % (label, src.count(old)))
    return src.replace(old, new, 1)


def sub_once(src, pattern, repl, label):
    new, n = re.subn(pattern, repl, src, count=1, flags=re.M)
    if n != 1:
        raise SystemExit("[fail] anchor %r hit %d times (plugin version changed?)"
                         % (label, n))
    return new


def patch(src):
    # 原文件是 CRLF,补进去的行也得是 CRLF,免得以后 anchor 混着两种换行
    eol = "\r\n" if "\r\n" in src else "\n"
    nl = lambda s: s.replace("\n", eol)

    # 1. 批次大小常量
    src = sub_once(
        src,
        r"^(    const PICKER_MAX_CELLS = 240)(?=\r?\n)",
        lambda m: m.group(1) + nl(
            "\n"
            "    /** 滚到底再铺一批的步长:首屏依旧是 240 格,后面的图滚一滚就到,不用靠搜索找。 */\n"
            "    const PICKER_BATCH = 120"
        ),
        "PICKER_MAX_CELLS",
    )

    # 2. 底部提示条改成可点(横排居中 + 两个小按钮的样式)
    src = replace_once(
        src,
        "'.meme-picker .mp-hint{flex:none;font-size:11px;color:var(--dsw-alias-label-secondary);text-align:center}',",
        nl("'.meme-picker .mp-hint{flex:none;font-size:11px;color:var(--dsw-alias-label-secondary);"
           "text-align:center;display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:8px}',\n"
           "      '.meme-picker .mp-more{padding:2px 10px;border-radius:999px;border:1px solid "
           "var(--dsw-alias-border-l1);background:transparent;color:var(--dsw-alias-label-secondary);"
           "cursor:pointer;font-size:11px}',\n"
           "      '.meme-picker .mp-more:hover{border-color:var(--dsw-alias-brand-primary);"
           "color:var(--dsw-alias-label-primary)}',"),
        "mp-hint css",
    )

    # 3. 渲染窗口状态
    src = sub_once(
        src,
        r"^( *const \[showFilters, setShowFilters\] = React\.useState\(\(\) => pickerFiltersOpen\).*?)(?=\r?\n)",
        lambda m: m.group(1) + nl(
            "\n"
            "      // 网格当前铺多少格:滚到底或点按钮就加一批(见 PICKER_BATCH),不再只画 240 张\n"
            "      const [renderCap, setRenderCap] = React.useState(PICKER_MAX_CELLS)"
        ),
        "showFilters state",
    )

    # 4. 换搜索词/筛选时窗口回到第一批(必须在 if (!open) return null 之前,保持 hook 顺序)
    src = replace_once(
        src,
        "      const viewport = (what) => {",
        nl("      // 结果集变了就回到第一批:从 780 张筛到 12 张时不至于留一堆空位\n"
           "      React.useEffect(() => { setRenderCap(PICKER_MAX_CELLS) }, [q, selPacks, selTags])\n"
           "\n"
           "      const viewport = (what) => {"),
        "viewport helper",
    )

    # 5. 用状态代替常量
    src = replace_once(
        src,
        "      const cells = rows.slice(0, PICKER_MAX_CELLS)",
        "      const cells = rows.slice(0, renderCap)",
        "cells slice",
    )

    # 6. 网格滚动加载:离底不足 400px 就再铺一批
    src = replace_once(
        src,
        "            : h('div', { className: 'mp-grid' }, gridCells)),",
        nl("            : h('div', {\n"
           "              className: 'mp-grid',\n"
           "              // 滚到接近底部再铺下一批:一次全铺上千格会把面板卡住,分批滚着铺不卡\n"
           "              onScroll: (e) => {\n"
           "                const el = e.currentTarget\n"
           "                if (!el || el.scrollHeight - el.scrollTop - el.clientHeight > 400) return\n"
           "                setRenderCap((n) => (n >= rows.length ? n : n + PICKER_BATCH))\n"
           "              },\n"
           "            }, gridCells)),"),
        "mp-grid element",
    )

    # 7. 底部提示:报进度 + 全部铺开
    src = sub_once(
        src,
        r"        rows\.length > cells\.length\r?\n"
        r"          \? h\('div', \{ className: 'mp-hint' \},\r?\n"
        r"            '[^\r\n]*\r?\n"
        r"          : null,",
        lambda m: nl("        rows.length > cells.length\n"
                     "          ? h('div', { className: 'mp-hint' },\n"
                     "            '已铺 ' + cells.length + ' / ' + rows.length + ' 张,往下滚会接着铺',\n"
                     "            h('button', {\n"
                     "              type: 'button', className: 'mp-more',\n"
                     "              onClick: () => setRenderCap(rows.length),\n"
                     "            }, '全部铺开'))\n"
                     "          : null,"),
        "mp-hint block",
    )

    return src


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET
    target = os.path.abspath(target)
    if not os.path.isfile(target):
        raise SystemExit("[fail] not found: " + target)

    with open(target, "r", encoding="utf-8", newline="") as fh:
        src = fh.read()

    if MARKER in src:
        print("[ok] already patched: " + target)
        return

    out = patch(src)

    backup = target + ".orig"
    if not os.path.exists(backup):
        shutil.copy2(target, backup)
        print("[ok] backup: " + backup)

    # 走临时文件 + os.replace:不原地改硬链接共享的 pnpm store 副本
    d = os.path.dirname(target)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".client.js.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(out)
        # 故意不 copystat:mtime 必须是现在,宿主的客户端缓存 / ETag 才不会拿着旧时间戳
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    print("[ok] patched: %s (+%d bytes)" % (target, len(out) - len(src)))


if __name__ == "__main__":
    main()
