# TK-077 п.3: окно переключения alpha на плагин role-play-vibing (шаги 1-5). Откат — rollback.ps1.
# Запускать из сессии вне диспетчера (CEO). -DryRun печатает всё и ничего не меняет.
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
function Do-It($desc, [scriptblock]$sb) {
    if ($DryRun) { Say "DRY: $desc" } else { Say $desc; & $sb }
}

# Шаг 0. Каталог плагина (установленный; для сухой проверки — -PluginDir)
if (-not $PluginDir) {
    $ip = Get-Content "$HOME\.claude\plugins\installed_plugins.json" -Raw | ConvertFrom-Json
    $rec = $ip.plugins.'role-play-vibing@role-play-vibing'
    if (-not $rec) { Say 'плагин role-play-vibing не установлен (claude plugin install role-play-vibing@role-play-vibing)'; exit 3 }
    $PluginDir = Join-Path $rec[0].installPath '.claude\dispatcher'
}
if (-not (Test-Path (Join-Path $PluginDir 'start.py'))) { Say "нет start.py в $PluginDir"; exit 3 }
Say "плагин: $PluginDir"

# Шаг 1. Нет бегущих ролей
$state = Get-Content (Join-Path $Disp 'state.json') -Raw | ConvertFrom-Json
$alive = @()
foreach ($p in $state.active_runs.PSObject.Properties) {
    $procId = [int]$p.Value.pid
    if ($procId -gt 0 -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) { $alive += ('{0} {1} pid {2}' -f $p.Name, $p.Value.role, $procId) }
}
if ($alive.Count) {
    Say ('бегущие роли (' + $alive.Count + '): ' + ($alive -join '; '))
    if (-not $DryRun) { Say 'отказ: сначала tickets.py stop / дождаться конца ролей'; exit 2 }
    Say 'DRY: в бою здесь был бы выход с кодом 2'
} else { Say 'бегущих ролей нет' }

# Шаг 2. Окружение: живое ALPHA_* диспетчера -> RPV_*, плюс настройки плагина
$expect = [ordered]@{
    RPV_DISPATCH_ROLE_PARALLEL            = 'engineer:6'
    RPV_DISPATCH_MAX_PARALLEL             = '8'
    RPV_DISPATCH_MAX_RUNS_PER_TICKET_HOUR = '20'
    RPV_DISPATCH_MAX_SAME_STATUS_RUNS     = '60'
    RPV_BUS_URL                           = 'http://89.163.242.211:8788'
    RPV_BUS_TOKEN_FILE                    = "$HOME\.alpha-bus-token"
    RPV_CALC_HOST                         = 'root@89.163.242.211'
    RPV_VPS_HOST                          = 'root@13.140.29.171'
    RPV_DECK_KEY                          = "$HOME\.ssh\id_rsa"
    RPV_DECK_KNOWN_HOSTS                  = "$HOME\.ssh\known_hosts"
    RPV_WATCHED_ALIASES                   = 'calc'
    RPV_PROGRESS_DIR                      = '/data/progress'
    RPV_GUARD_HEAVY_HOST                  = '89.163.242.211'
    RPV_GUARD_HEAVY_HINT                  = 'Обёртка: /data/benchrun.sh stand <команда> (диски — /data/tk052/benchrun2.sh; волна — wave)'
    RPV_GUARD_REMOTE_ROOTS                = '~/alpha/,$home/alpha/,${home}/alpha/,/home/deck/alpha/,/opt/alpha-compute/'
    RPV_GUARD_HOST_ROOTS                  = '89.163.242.211=/home/deck/alpha/,/root/tk0,/data/tk0,/data/registry/,/tmp/,/opt/alpha-board/;13.140.29.171=/opt/alpha-archive/stage/,/opt/alpha-archive-tk021/dup-reimport/'
    RPV_GUARD_STAGE                       = '/dev/shm/alpha-stage'
    RPV_GUARD_FORBIDDEN_HOSTS             = '139.99.91.22'
}
$livePid = [int](Get-Content (Join-Path $Disp 'dispatch.pid') -ErrorAction SilentlyContinue)
$parent = 0
if ($livePid) { $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$livePid" -ErrorAction SilentlyContinue).ParentProcessId }
$liveCmd = ''
if ($parent) { $liveCmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$parent" -ErrorAction SilentlyContinue).CommandLine }
foreach ($m in [regex]::Matches([string]$liveCmd, 'set "ALPHA_([A-Z_]+)=([^"]*)"')) {
    $k = 'RPV_' + $m.Groups[1].Value; $v = $m.Groups[2].Value
    if ($expect.Contains($k)) {
        if ($expect[$k] -ne $v) { Say "РАЗНИЦА $k : план '$($expect[$k])', у живого '$v' - беру живое"; $expect[$k] = $v }
    } else { $expect[$k] = $v }
}
foreach ($k in $expect.Keys) { Say ("env $k=" + $expect[$k]) }

# Шаг 3. Стоп старого: присмотр отключить (не удалять), диспетчер и сторож остановить
Do-It 'schtasks /change alpha-supervise /disable' { schtasks /change /tn alpha-supervise /disable | Out-Null }
foreach ($n in 'dispatch.pid', 'watch.pid') {
    $f = Join-Path $Disp $n
    $procId = [int](Get-Content $f -ErrorAction SilentlyContinue)
    if ($procId -gt 0 -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
        $par = (Get-CimInstance Win32_Process -Filter "ProcessId=$procId").ParentProcessId
        Do-It "Stop-Process $n pid $procId (+ cmd-обёртка $par)" {
            Stop-Process -Id $procId -Force
            if ($par -and (Get-Process -Id $par -ErrorAction SilentlyContinue | Where-Object ProcessName -eq 'cmd')) { Stop-Process -Id $par -Force }
        }
    } else { Say "$n : процесса нет" }
}

# Шаг 4. env (процесс + User, чтобы хуки интерактивных сессий видели замок замеров), присмотр и служба плагина
Do-It 'env RPV_* -> процесс и User' {
    foreach ($k in $expect.Keys) {
        [Environment]::SetEnvironmentVariable($k, $expect[$k], 'Process')
        [Environment]::SetEnvironmentVariable($k, $expect[$k], 'User')
    }
}
Do-It "supervise.py --install --project $Project" {
    & $Py (Join-Path $PluginDir 'supervise.py') --install --project $Project
    if ($LASTEXITCODE) { throw 'supervise --install' }
}
Do-It "start.py --project $Project (WMI Win32_Process.Create внутри start.py)" {
    & $Py (Join-Path $PluginDir 'start.py') --project $Project
    if ($LASTEXITCODE) { throw 'start.py' }
}

# Шаг 5. settings.json: убрать хуки alpha-копии (их даёт плагин); копия - settings.json.pre-plugin
$settings = Join-Path $Project '.claude\settings.json'
$strip = Join-Path $PSScriptRoot 'strip_hooks.py'
Do-It 'settings.json: хуки на .claude/hooks/ убрать (копия .pre-plugin)' {
    Copy-Item $settings "$settings.pre-plugin" -Force
    & $Py $strip $settings
    if ($LASTEXITCODE) { throw 'strip_hooks' }
}
if ($DryRun) { & $Py $strip $settings --dry-run }

Say 'готово. Дальше (6): /rpv-doctor, тестовый тикет next/wait_for, проба замка; затем git rm alpha-копии механики (тег alpha-dispatcher-pre-plugin - откат)'
