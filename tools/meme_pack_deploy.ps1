# 表情包图库部署：把记忆库里的图库接到 dsh-meme 的扫描目录
# 幂等：链接已存在就只校验。用法：powershell -ExecutionPolicy Bypass -File tools\meme_pack_deploy.ps1
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$target = Join-Path $repo 'meme-packs\shiori-learned'
$packsDir = Join-Path $env:USERPROFILE '.dsh\meme-packs'
$link = Join-Path $packsDir 'shiori-learned'
$settings = Join-Path $env:USERPROFILE '.dsh\dsh-expression.json'

if (-not (Test-Path -LiteralPath $target)) { throw "记忆库里没有图库：$target（先同步记忆库）" }
if (-not (Test-Path -LiteralPath (Join-Path $target 'index.db'))) { throw "图库缺 index.db：$target" }

New-Item -ItemType Directory -Force -Path $packsDir | Out-Null

if (Test-Path -LiteralPath $link) {
  $item = Get-Item -LiteralPath $link -Force
  if ($item.LinkType -ne 'Junction' -and $item.LinkType -ne 'SymbolicLink') {
    throw "$link 已存在且不是链接，先手动处理"
  }
  "链接已存在：$link -> $($item.Target)"
} else {
  cmd /c mklink /J "$link" "$target" | Out-Null
  "已建链接：$link -> $target"
}

$db = Join-Path $target 'index.db'
$files = (Get-ChildItem -LiteralPath (Join-Path $target 'memes') -Recurse -File).Count
$env:MEME_PACK_DB = $db
$indexRows = [int](& node -e "const {DatabaseSync}=require('node:sqlite');const db=new DatabaseSync(process.env.MEME_PACK_DB,{readOnly:true});console.log(db.prepare('select count(*) c from memes').get().c)" | Select-Object -Last 1)
"index 行数=$indexRows  图片文件数=$files"
if ($indexRows -ne $files) { "⚠ 不一致：改过图之后要重建 index（tools\meme_pack_index.mjs）" }

if (Test-Path -LiteralPath $settings) {
  $cfg = Get-Content -LiteralPath $settings -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($cfg.packId -ne 'shiori-learned') {
    "⚠ 当前激活图库是 $($cfg.packId)；到 dsh-meme 设置页切到「Shiori 表情包（学习图库）」，或把 dsh-expression.json 的 memeRoot/packId 指过来后重启 GUI"
  } else { "当前已激活：shiori-learned" }
} else { "未找到 dsh-expression.json，首次启动插件后会生成" }
