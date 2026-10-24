# 一键初始化：把记忆模块接到一台新机器 / 新工作目录上
# 用法：
#   pwsh -File setup.ps1
#   pwsh -File setup.ps1 -Workspace "D:\notes-app\MyVault"
#   pwsh -File setup.ps1 -NoIndex          # 只检查，不重建索引
# 注意：保存为 UTF-8 with BOM

param(
    [string]$Workspace = '',
    [string]$Python = '',
    [switch]$NoIndex
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$Root = $PSScriptRoot
$Refs = Join-Path $Root 'references'
$Tools = Join-Path $Root 'tools'
$Tpl = Join-Path $Root 'templates'
$CfgFile = Join-Path $Root 'memory.config.json'
$CfgExample = Join-Path $Root 'config.example.json'

function Step($msg) { Write-Output ('  ' + $msg) }

Write-Output '=== 记忆模块初始化 ==='

# 1) 结构自检
foreach ($d in @($Refs, $Tools, $Tpl)) {
    if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}
$skill = Join-Path $Root 'SKILL.md'
if (-not (Test-Path -LiteralPath $skill)) {
    Write-Output '❌ SKILL.md 不在，这不是完整的记忆模块目录'
    exit 1
}
Write-Output '结构：'
Step ('根目录 ' + $Root)
Step ('模块 ' + (@(Get-ChildItem $Refs -File -Filter *.md).Count) + ' 个')

# 2) 配置
if (-not (Test-Path -LiteralPath $CfgFile)) {
    Copy-Item -LiteralPath $CfgExample -Destination $CfgFile -Force
    Write-Output '配置：'
    Step 'memory.config.json 从模板新建，接着按本机填路径'
}
$cfg = Get-Content -LiteralPath $CfgFile -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $cfg.paths.home) { $cfg.paths.home = $env:USERPROFILE }
if (-not $Python) { $Python = $cfg.paths.python }
if (-not $Python) {
    # 先看 PATH 上有没有真 python；再退回常见安装位置，不写死用户名
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $Python = $cmd.Source
    } else {
        $cand = Join-Path $env:LOCALAPPDATA 'Python\bin\python.exe'
        if (Test-Path -LiteralPath $cand) { $Python = $cand } else { $Python = 'python' }
    }
}
if (-not $Workspace) { $Workspace = $cfg.paths.workspace }
$cfg.paths.python = $Python
if ($Workspace) { $cfg.paths.workspace = $Workspace }
$cfg | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $CfgFile -Encoding UTF8
Write-Output '配置：'
Step ('python  ' + $Python)
Step ('工作区  ' + ($(if ($Workspace) { $Workspace } else { '(未设置，跳过 AGENTS.md)' })))

# 3) 工作区 AGENTS.md（缺了才建，不覆盖）
if ($Workspace) {
    $agents = Join-Path $Workspace 'AGENTS.md'
    if (Test-Path -LiteralPath $agents) {
        Write-Output '工作区：'
        Step 'AGENTS.md 已存在，保留不动'
    } elseif (Test-Path -LiteralPath (Join-Path $Tpl 'AGENTS.md')) {
        $body = Get-Content -LiteralPath (Join-Path $Tpl 'AGENTS.md') -Raw -Encoding UTF8
        $body = $body.Replace('<笔记库>', $Workspace).Replace('<代码仓库>', $cfg.paths.codeRepo)
        Set-Content -LiteralPath $agents -Value $body -Encoding UTF8
        Write-Output '工作区：'
        Step ('已生成 ' + $agents)
    }
}

# 4) 索引、片段层与总览
if (-not $NoIndex) {
    Write-Output '索引：'
    $pyFile = Join-Path $Tools 'memdb.py'
    & $Python $pyFile index
    if ($LASTEXITCODE -ne 0) { Step '⚠️ memdb 建索引失败，检索会退化到 grep，不影响其它功能' }
    & $Python (Join-Path $Tools 'frag.py') build
    if ($LASTEXITCODE -ne 0) { Step '⚠️ 片段层没建起来，读取退回整份模块' }
    & $Python (Join-Path $Tools 'catalog.py')
}

# 5) 下一步
Write-Output '=== 完成 ==='
Write-Output '接下来：'
Write-Output '  1. 填 memory.config.json 里的 workspace / codeRepo（体检脚本用它）'
Write-Output '  2. 把打开记忆的约定写进你自己的 AGENTS.md（第 1 节）'
Write-Output '  3. 空模块（distribute: blank）按自己的情况逐条填，格式见 references/_模板.md'
Write-Output '  4. 要发给别人：pwsh -File tools\pack.ps1'
