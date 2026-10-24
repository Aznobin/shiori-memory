# 校验「记忆里的 DSH 模块清单」和实际安装的 package.json 是否一致
# 用法：pwsh -File tools/check-modules.ps1     （一致返回 0，有差异返回 1）
$ErrorActionPreference = 'Stop'

$profileJson = Join-Path $env:USERPROFILE '.dsh\profiles\desktop\package.json'
$memFile = Join-Path $PSScriptRoot '..\references\21-DSH模块清单.md'

if (-not (Test-Path -LiteralPath $profileJson)) {
    Write-Output "找不到 $profileJson"
    exit 2
}
if (-not (Test-Path -LiteralPath $memFile)) {
    Write-Output "找不到 $memFile"
    exit 2
}

$actual = (Get-Content -LiteralPath $profileJson -Raw | ConvertFrom-Json).dependencies.PSObject.Properties.Name |
    Sort-Object -Unique

$text = Get-Content -LiteralPath $memFile -Raw
$documented = [regex]::Matches($text, '\|\s*`([^`]+)`\s*\|\s*\^?[\d.]+\s*\|') |
    ForEach-Object { $_.Groups[1].Value } |
    Sort-Object -Unique

$missing = @($actual | Where-Object { $_ -notin $documented })
$extra = @($documented | Where-Object { $_ -notin $actual })

if ($missing.Count -eq 0 -and $extra.Count -eq 0) {
    Write-Output "OK  $($actual.Count) 个模块，清单与 package.json 一致"
    exit 0
}

Write-Output "不一致，需要更新 references/21-DSH模块清单.md："
if ($missing.Count -gt 0) {
    Write-Output "  清单缺（已安装未记录）："
    $missing | ForEach-Object { Write-Output "    - $_" }
}
if ($extra.Count -gt 0) {
    Write-Output "  清单多（已记录未安装）："
    $extra | ForEach-Object { Write-Output "    - $_" }
}
exit 1
