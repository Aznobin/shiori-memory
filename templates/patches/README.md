# DSH 本地补丁

## dsh-meme-picker-render.py

修 dsh-meme 表情选择器「只画前 240 张」的问题：改成滚到底再铺 120 张，另加「全部铺开」按钮。

用法（每台机器各跑一次）：

    python <记忆库根>\templates\patches\dsh-meme-picker-render.py

默认目标是 `~/.dsh/profiles/desktop/node_modules/dsh-meme/client.js`，要换路径就把 client.js 路径当参数传进去。

- 幂等：已打过补丁会报 `already patched` 并退出 0
- dsh-meme 升级覆盖 client.js 之后必须重跑
- 锚点对不上会报 `[fail] anchor ... hit 0 times`，说明插件版本变了，补丁要重新对锚点，别硬跑
