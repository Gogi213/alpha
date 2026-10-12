"""Стенд TK-217: разрыв ssh ПК->calc на 6 мин во время короткого задания (импортирует dispatch из ветки PR, без пробуждения ролей).
Разрыв — реальный отказ ssh-процесса (код 255): пока жив флаг-файл, к ssh добавляется ProxyCommand, который сразу падает;
флаг снимает задача Планировщика Windows (независимо от сессии). Журнал меток: tk217-stand.log (ISO время + метка)."""
import os, subprocess, sys, threading, time
from datetime import datetime
from pathlib import Path

DISP = sys.argv[1]  # каталог .claude/dispatcher из ветки PR
OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
FLAG = OUT / "cut.flag"; LOG = OUT / "tk217-stand.log"; JOB = "/tmp/tk217-stand.json"
sys.path.insert(0, DISP)
import dispatch as D  # noqa: E402


def mark(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {m}\n")


orig = D._ssh_cmd
def cut_cmd(alias, remote):
    c = orig(alias, remote)
    return c[:-2] + ["-o", f'ProxyCommand="{sys.executable}" -c "import sys;sys.exit(1)"'] + c[-2:] if FLAG.exists() else c
D._ssh_cmd = cut_cmd
D.WAIT_ERR_EVERY_S = 0
D.DISPATCHER_DIR = OUT; D.CEO_INBOX = OUT / "ceo-inbox.md"; D.CEO_WAKE_LOG = OUT / "ceo-wake.log"
real_beep = D._blind_sound
def beep():
    try:
        import winsound; winsound.Beep(1000, 300); mark("sound ok (winsound.Beep без исключения)")
    except Exception as e:
        mark(f"sound FAIL {type(e).__name__}: {e}")
D._blind_sound = beep

mark(f"stand start; session={os.environ.get('SESSIONNAME')}; user={os.environ.get('USERNAME')}")
ssh = lambda cmd: subprocess.run(orig("calc", cmd), capture_output=True, timeout=90)
ssh(f"rm -f {JOB}; nohup sh -c 'sleep 210; echo \"{{\\\"done\\\":1,\\\"total\\\":1}}\" > {JOB}' >/dev/null 2>&1 &")
t0 = time.time(); mark("job start (конец через 210 с)")
FLAG.write_text("cut"); mark("правило разрыва ВКЛ (флаг cut.flag); снятие — Планировщик +6 мин")
key = ("calc", "path", JOB)
D._WAIT_WATCH.add(key)
seen = set(); seen_n = -1
T = D.time.time
th = threading.Thread(target=D._wait_poller, daemon=True); th.start()
while time.time() - t0 < 600:
    D._blind_tick(None)
    if not FLAG.exists() and "off" not in seen:
        seen.add("off"); mark("правило разрыва СНЯТО")
    if (OUT / "ALERT-BLIND.txt").exists() and "alert" not in seen:
        seen.add("alert"); mark("alert: ALERT-BLIND.txt создан")
    if "alert" in seen and not (OUT / "ALERT-BLIND.txt").exists() and "rec" not in seen:
        seen.add("rec"); mark("recovered: ALERT-BLIND.txt снят")
    if time.time() - t0 > 215 and "jobend" not in seen:
        seen.add("jobend"); mark("конец задания (по плану +210 с; файл на calc создан)")
    if D._WAIT_CACHE.get(key, (0, False))[1] is True:
        mark(f"_WAIT_CACHE=True (роль проснулась бы); после снятия разрыва: {'да' if 'off' in seen else 'НЕТ'}"); break
    if int(time.time() - t0) % 30 < 2 and int(time.time() - t0) // 30 != seen_n:
        seen_n = int(time.time() - t0) // 30; mark(f"state: cut={FLAG.exists()} down={ {k: ('back' in v) for k, v in D.BLIND.down.items()} }")
    time.sleep(2)
else:
    mark("TIMEOUT: _WAIT_CACHE не стал True")
mark("stand end"); (OUT / "done").write_text("1")
