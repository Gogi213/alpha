# TK-077: откат окна переключения. Возврат к alpha-копии механики (тег alpha-dispatcher-pre-plugin).
# -DryRun - печать без действий. -Project можно направить на копию дерева (сухая проверка отката на копии).
param(
    [switch]$DryRun,
    [string]$Project = 'C:\visual projects\alpha',
    [string]$PluginDir = ''
)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$Disp = Join-Path $Project '.claude\dispatcher'
$Py = (Get-Command python).Source
function Say($m) { Write-Host ('[' + (Get-Date -Format HH:mm:ss) + '] ' + $m) }
function Do-It($desc, [scriptblock]$sb) { if ($DryRun) { Say "DRY: $desc" } else { Say $desc; & $sb } }

if (-not $PluginDir) {
    $ip = Get-Content "$HOME\.claude\plugins\installed_plugins.json" -Raw | ConvertFrom-Json
    $rec = $ip.plugins.'role-play-vibing@role-play-vibing'
    if ($rec) { $PluginDir = Join-Path $rec[0].installPath '.claude\dispatcher' }
}

# 1. Присмотр плагина снять, службы плагина остановить
if ($PluginDir -and (Test-Path (Join-Path $PluginDir 'supervise.py'))) {
    Do-It 'supervise.py --uninstall (присмотр плагина)' { & $Py (Join-Path $PluginDir 'supervise.py') --uninstall --project $Project }
}
foreach ($n in 'dispatch.pid', 'watch.pid', 'board_push.pid') {
    $procId = [int](Get-Content (Join-Path $Disp $n) -ErrorAction SilentlyContinue)
    if ($procId -gt 0 -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
        $par = (Get-CimInstance Win32_Process -Filter "ProcessId=$procId").ParentProcessId
        Do-It "Stop-Process $n pid $procId (+ cmd $par)" {
            Stop-Process -Id $procId -Force
            if ($par -and (Get-Process -Id $par -ErrorAction SilentlyContinue | Where-Object ProcessName -eq 'cmd')) { Stop-Process -Id $par -Force }
        }
    } else { Say "$n : процесса нет" }
}

# 2. Вернуть alpha-копию механики и настройки (тег; settings.json.pre-plugin точнее - там незакоммиченное)
Do-It 'git checkout alpha-dispatcher-pre-plugin: .claude/hooks, .claude/settings.json, .claude/dispatcher/*.py' {
    Push-Location $Project
    try {
        git checkout alpha-dispatcher-pre-plugin -- .claude/hooks .claude/settings.json
        $tracked = git ls-tree --name-only alpha-dispatcher-pre-plugin .claude/dispatcher/ | Where-Object { $_ -like '*.py' }
        foreach ($f in $tracked) { git checkout alpha-dispatcher-pre-plugin -- $f }
    } finally { Pop-Location }
}
$pre = Join-Path $Project '.claude\settings.json.pre-plugin'
if (Test-Path $pre) { Do-It 'settings.json.pre-plugin -> settings.json' { Copy-Item $pre (Join-Path $Project '.claude\settings.json') -Force } }
elseif ($DryRun) { Say 'DRY: settings.json.pre-plugin нет (окно не запускалось) - останется версия из тега' }

# 3. User-переменные RPV_* убрать (ALPHA_* остаются как были)
Do-It 'снять User-переменные RPV_*' {
    foreach ($e in @([Environment]::GetEnvironmentVariables('User').Keys)) {
        if ($e -like 'RPV_*') { [Environment]::SetEnvironmentVariable($e, $null, 'User') }
    }
}

# 4. Старый присмотр и старая служба (как до окна: WMI, cmd-обёртка с set ALPHA_*, CreateFlags 512)
Do-It 'schtasks /change alpha-supervise /enable' { schtasks /change /tn alpha-supervise /enable | Out-Null }
$env1 = [ordered]@{ ALPHA_DISPATCH_ROLE_PARALLEL = 'engineer:6'; ALPHA_DISPATCH_MAX_PARALLEL = '8'; ALPHA_DISPATCH_MAX_RUNS_PER_TICKET_HOUR = '20'; ALPHA_DISPATCH_MAX_SAME_STATUS_RUNS = '60' }
$sets = ($env1.GetEnumerator() | ForEach-Object { 'set "' + $_.Key + '=' + $_.Value + '"&' }) -join ' '
foreach ($svc in 'dispatch', 'watch') {
    $pre1 = ''
    if ($svc -eq 'dispatch') { $pre1 = $sets + ' ' }
    $cmd = 'cmd /c ' + $pre1 + 'set "PYTHONUNBUFFERED=1"& "' + $Py + '" "' + $Disp + '\' + $svc + '.py" >> "' + $Disp + '\' + $svc + '.out.log" 2>&1'
    if ($DryRun) { Say "DRY: WMI Create (CreateFlags 512, cwd $Project): $cmd" }
    else {
        Say "старт $svc через WMI"
        $si = New-CimInstance -Namespace root/cimv2 -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0; CreateFlags = [uint32]512 }
        $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $Project; ProcessStartupInformation = $si }
        if ($r.ReturnValue -ne 0) { throw "WMI Create $svc : $($r.ReturnValue)" }
    }
}
Say 'откат готов; проверить: dispatch.pid/watch.pid живы, heartbeat свежий, schtasks /query /tn alpha-supervise'
