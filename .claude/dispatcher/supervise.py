"""Присмотр за диспетчером и сторожем (TK-072 п.3): запускается Планировщиком заданий Windows каждые 5 мин, сам ничего
не держит. Сердцебиение старше STALE_S (или процесса нет) -> убить зависший pid и поднять заново через WMI (вне job),
строка в supervise.log. Установка/снятие задания: `python supervise.py --install|--uninstall`."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DIR = Path(__file__).resolve().parent
ROOT = DIR.parents[1]
LOG = DIR / "supervise.log"
STALE_S = float(os.environ.get("ALPHA_SUPERVISE_STALE_S", "600"))
TASK = "alpha-supervise"
DISPATCH_ENV = {"ALPHA_DISPATCH_ROLE_PARALLEL": "engineer:6", "ALPHA_DISPATCH_MAX_PARALLEL": "8",
                "ALPHA_DISPATCH_MAX_RUNS_PER_TICKET_HOUR": "20", "ALPHA_DISPATCH_MAX_SAME_STATUS_RUNS": "60",
                "PYTHONUNBUFFERED": "1"}
# имя -> (файл сердцебиения, поле времени, pid-файл, скрипт, файл лога, окружение)
TARGETS = {
    "dispatch": (DIR / "state.json", "last_tick", DIR / "dispatch.pid", DIR / "dispatch.py", DIR / "dispatch.out.log", DISPATCH_ENV),
    "watch": (DIR / "watch-heartbeat.json", "ts", DIR / "watch.pid", DIR / "watch.py", DIR / "watch.out.log", {"PYTHONUNBUFFERED": "1"}),
}


def heartbeat_age(path: Path, field: str, now: float) -> float | None:
    """Возраст сердцебиения в секундах; None — файла/поля нет или он нечитаем."""
    try:
        ts = json.loads(path.read_text(encoding="utf-8"))[field]
        return now - datetime.fromisoformat(ts).timestamp()
    except (OSError, ValueError, KeyError, TypeError):
        return None


def pid_alive(pid_file: Path) -> int:
    """pid живого python-процесса из pid-файла или 0."""
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True)
    return pid if "python" in r.stdout.lower() else 0


def decide(age: float | None, alive: int, stale_s: float = STALE_S) -> str:
    """ok | start (процесса нет) | restart (процесс есть, сердцебиение старое — завис)."""
    if not alive:
        return "start"
    if age is None or age > stale_s:
        return "restart"
    return "ok"


def log(msg: str) -> None:
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}\n")


def wmi_command(script: Path, out: Path, env: dict) -> str:
    """Командная строка cmd: каждая переменная — `set "K=V"&` (в cmd `;` не разделитель)."""
    envs = "".join(f'set "{k}={v}"& ' for k, v in env.items())
    py = Path(sys.executable).with_name("python.exe")  # не pythonw: у него нет консоли, дети получили бы окна
    return f'cmd /c {envs}"{py}" "{script}" >> "{out}" 2>&1'


def wmi_start(script: Path, out: Path, env: dict) -> int:
    """Запуск вне job: Win32_Process.Create, CreateFlags 512 (новая группа процессов), cwd = корень репо."""
    cmd = wmi_command(script, out, env)
    ps = ("$s=New-CimInstance -ClientOnly -CimClass (Get-CimClass Win32_ProcessStartup) -Property @{CreateFlags=[uint32]512};"
          f"$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{CommandLine='{cmd}';"
          f"CurrentDirectory='{ROOT}';ProcessStartupInformation=$s}};$r.ReturnValue")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    return int((r.stdout.strip().splitlines() or ["-1"])[-1])


def run_once(now: float | None = None, start=wmi_start, alive=pid_alive, kill=None) -> dict:
    now = time.time() if now is None else now
    kill = kill or (lambda pid: subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True))
    out = {}
    for name, (hb, field, pidf, script, outlog, env) in TARGETS.items():
        age, pid = heartbeat_age(hb, field, now), alive(pidf)
        act = decide(age, pid)
        out[name] = act
        if act == "ok":
            continue
        if act == "restart":
            kill(pid)
            time.sleep(2)
        rc = start(script, outlog, env)
        log(f"{name}: {act} (сердцебиение {'нет' if age is None else f'{age:.0f} с'}, pid {pid or '-'}) -> WMI rc={rc}")
    return out


def install() -> None:
    pyw = Path(sys.executable).with_name("pythonw.exe")
    tr = f'"{pyw}" "{DIR / "supervise.py"}"'
    subprocess.run(["schtasks", "/Create", "/F", "/TN", TASK, "/SC", "MINUTE", "/MO", "5", "/TR", tr], check=True)


if __name__ == "__main__":
    if "--install" in sys.argv:
        install()
    elif "--uninstall" in sys.argv:
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK])
    else:
        print(run_once())
