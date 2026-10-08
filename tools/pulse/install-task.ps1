# Д-1 (TK-090): сборщик табло — заданием Планировщика, а не ручным WMI.
# Задание alpha-pulse: каждые 5 минут (покрывает и вход; AtLogOn без прав администратора недоступен); живой экземпляр не дублируется (collect.pid).
# Веб табло здесь не поднимается: живёт юнитом alpha-board на сервере счёта (адрес — .claude/pulse/board-url.txt, выкладка — блокнот инженера, «Табло»).
# Запуск: pwsh -File tools/pulse/install-task.ps1   (идемпотентно: пересоздаёт задание)
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
# pythonw напрямую, не cmd.exe /c start: cmd каждые 5 минут мигал видимым окном (TK-105); консольного вывода у pythonw нет
$py = (Get-Command python).Source
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
$act = New-ScheduledTaskAction -Execute $pyw -Argument 'tools\pulse\collect.py' -WorkingDirectory $repo
$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
$pr = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'alpha-pulse' -Action $act -Trigger $t2 -Settings $set -Principal $pr -Force | Out-Null
Get-ScheduledTask -TaskName 'alpha-pulse' | Select-Object TaskName, State
