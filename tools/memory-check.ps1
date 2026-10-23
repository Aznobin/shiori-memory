# 记忆库体检（只读，不修改任何文件）
# 宿主二选一，输出完全一致：
#   pwsh -File "<技能根>\tools\memory-check.ps1"
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<技能根>\tools\memory-check.ps1"
# 可选参数: -Root <技能根目录>  -StaleDays 30  -CheckupDays 7
# 注意: 本文件必须保存为 UTF-8 with BOM，否则 Windows PowerShell 5.1 会按 GBK 解析成乱码

param(
    [string]$Root = (Join-Path $env:USERPROFILE '.agents\skills\user-memory'),
    [int]$StaleDays = 30,
    [int]$CheckupDays = 7
)

# 5.1 在 CP936 控制台下会把中文输出成乱码，先钉死输出编码
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$refsDir   = Join-Path $Root 'references'
$skillFile = Join-Path $Root 'SKILL.md'
$issues = @()
$warns  = @()

# 行数按「真实内容行」算：CRLF 归一 + 去掉结尾换行造成的空行
function Get-LineCount {
    param([string]$Path)
    $t = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ($null -eq $t) { return 0 }
    $t = $t -replace "`r`n", "`n"
    $t = $t.TrimEnd("`n")
    if ($t.Length -eq 0) { return 0 }
    return @($t -split "`n").Count
}

function Get-Dates {
    param([string]$Text)
    return @([regex]::Matches($Text, '(?m)^\|\s*(\d{4}-\d{2}-\d{2})\s*\|') | ForEach-Object { $_.Groups[1].Value })
}

$today    = Get-Date
$todayStr = $today.ToString('yyyy-MM-dd')

Write-Output ('=== 记忆库体检 ' + $today.ToString('yyyy-MM-dd HH:mm') + ' ===')
Write-Output ('根目录: ' + $Root)
Write-Output ('宿主: ' + $PSVersionTable.PSVersion.ToString())

$skillText = Get-Content $skillFile -Raw -Encoding UTF8

# 1) 路由表登记 <-> references/ 文件 一一对应
$routeFiles = @()
foreach ($m in [regex]::Matches($skillText, '(?m)^\|\s*([^|]*?)\s*\|')) {
    # 一行里可以合并登记多个结构件，所以把首列的反引号名字全部收下来
    foreach ($b in [regex]::Matches($m.Groups[1].Value, '`([^`]+)`')) {
        $name = $b.Groups[1].Value
        if ($name -match '^\d{2}[a-z]?-' -or $name -in @('_待确认', '变更日志', '_复核清单', '_巡检记录')) {
            $routeFiles += ($name + '.md')
        }
    }
}
$moduleFiles = @(
    Get-ChildItem $refsDir -File -Filter *.md |
    Where-Object { $_.Name -notin @('_模板.md', 'CATALOG.md', '_复核清单.md', '_巡检记录.md') } |
    Select-Object -ExpandProperty Name
)

$missingFile  = @($routeFiles  | Where-Object { $moduleFiles -notcontains $_ })
$missingRoute = @($moduleFiles | Where-Object { $routeFiles  -notcontains $_ })
if ($missingFile.Count)  { $issues += ('路由表登记但文件不存在: ' + ($missingFile -join ', ')) }
if ($missingRoute.Count) { $issues += ('文件未登记进路由表: '     + ($missingRoute -join ', ')) }

# 2) frontmatter 必备字段
$required = @('id', 'title', 'keywords', 'sensitive', 'updated')
foreach ($name in $moduleFiles) {
    $raw = Get-Content (Join-Path $refsDir $name) -Raw -Encoding UTF8
    $fm = [regex]::Match($raw, '(?s)^\ufeff?---\s*\r?\n(.*?)\r?\n---')
    if (-not $fm.Success) { $issues += ($name + ' 没有 frontmatter 区块'); continue }
    $head = $fm.Groups[1].Value
    foreach ($f in $required) {
        if ($head -notmatch ('(?m)^' + $f + '\s*:')) { $issues += ($name + ' frontmatter 缺 ' + $f) }
    }
}

# 3) 行数上限
$skillLines = Get-LineCount $skillFile
if ($skillLines -gt 150) { $warns += ('SKILL.md ' + $skillLines + ' 行（上限 150）：把内容下放到模块') }
foreach ($name in $moduleFiles) {
    $c = Get-LineCount (Join-Path $refsDir $name)
    if ($c -gt 120) { $warns += ($name + ' ' + $c + ' 行（建议 ≤120）：按 15 §6 拆分') }
}

# 4) updated 新鲜度
foreach ($name in $moduleFiles) {
    $text = Get-Content (Join-Path $refsDir $name) -Raw -Encoding UTF8
    $m = [regex]::Match($text, '(?m)^updated:\s*(\d{4}-\d{2}-\d{2})')
    if (-not $m.Success) { $issues += ($name + ' 没有可解析的 updated 日期'); continue }
    $d = [datetime]::ParseExact($m.Groups[1].Value, 'yyyy-MM-dd', $null)
    $age = [int]($today - $d).TotalDays
    if ($age -gt $StaleDays) { $warns += ($name + ' updated 距今 ' + $age + ' 天：话题涉及现状时先复核') }
}

# 5) inbox 待确认
$inboxText = Get-Content (Join-Path $refsDir '_待确认.md') -Raw -Encoding UTF8
$pending = [regex]::Matches($inboxText, '(?m)^\|\s*\d{4}-\d{2}-\d{2}\s*\|')
if ($pending.Count -gt 0) { $warns += ('inbox 有 ' + $pending.Count + ' 条待确认：收尾时清一遍') }

# 6) 重复问题台账
$ledgerText = Get-Content (Join-Path $refsDir '82-重复问题台账.md') -Raw -Encoding UTF8
$watching = [regex]::Matches($ledgerText, '观察中')
if ($watching.Count -gt 0) { $warns += ('台账还有 ' + $watching.Count + ' 行「观察中」') }
foreach ($m in [regex]::Matches($ledgerText, '(?m)^\|([^|\n]+)\|\s*(\d+)\s*\|([^\n]*)$')) {
    $n   = [int]$m.Groups[2].Value
    $row = $m.Value
    if ($n -ge 2 -and $row -notmatch '已升级|已解决|已排除|不升级') {
        $warns += ('台账 2 次以上却没升级: ' + $m.Groups[1].Value.Trim())
    }
}

# 7) 变更日志
$logText  = Get-Content (Join-Path $refsDir '变更日志.md') -Raw -Encoding UTF8
$logDates = @(Get-Dates $logText)
if ($logDates.Count -gt 0) {
    $latest = ($logDates | Sort-Object -Descending | Select-Object -First 1)
    Write-Output ('变更日志: ' + $logDates.Count + ' 行，最新 ' + $latest)
}

# 8) 节律：上次体检距今多久
$checkText  = Get-Content (Join-Path $refsDir '_巡检记录.md') -Raw -Encoding UTF8
$checkDates = @(Get-Dates $checkText | Sort-Object -Descending)
if ($checkDates.Count -eq 0) {
    $warns += ('_巡检记录 里没有任何体检日期：跑完这轮补一行')
} else {
    $last = [datetime]::ParseExact($checkDates[0], 'yyyy-MM-dd', $null)
    $gap  = [int]($today - $last).TotalDays
    if ($gap -gt $CheckupDays) { $warns += ('上次体检距今 ' + $gap + ' 天（节律 ' + $CheckupDays + ' 天）：该跑一轮了') }
}

# 9) 日志纪律：今天动过库却没有今天的日志行
$touchedToday = @(Get-ChildItem $refsDir -File -Filter *.md | Where-Object { $_.LastWriteTime.Date -eq $today.Date })
if ($touchedToday.Count -gt 0 -and ($logDates -notcontains $todayStr)) {
    $warns += ('今天改过 ' + $touchedToday.Count + ' 个文件，但变更日志没有 ' + $todayStr + ' 的行')
}

# 10) 记录膨胀：巡检记录只留最近 5 次
if ($checkDates.Count -gt 5) {
    $warns += ('_巡检记录 有 ' + $checkDates.Count + ' 行：只留最近 5 次，把旧行删掉')
}

# 报告
Write-Output ''
Write-Output ('模块 ' + $moduleFiles.Count + ' 个 · SKILL.md ' + $skillLines + ' 行')
if ($issues.Count -eq 0 -and $warns.Count -eq 0) {
    Write-Output '✅ 全部通过：登记一致、frontmatter 齐、行数达标、无超期、无待办'
} else {
    if ($issues.Count) {
        Write-Output ('❌ 必须修 (' + $issues.Count + ')')
        $issues | ForEach-Object { Write-Output ('  - ' + $_) }
    }
    if ($warns.Count) {
        Write-Output ('⚠️ 建议看 (' + $warns.Count + ')')
        $warns | ForEach-Object { Write-Output ('  - ' + $_) }
    }
}
