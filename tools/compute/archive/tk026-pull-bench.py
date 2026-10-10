#!/usr/bin/env python3
# TK-026: сторож на этой машине — ждёт DONE замера на VPS и забирает мелкие итоги (days.csv, summary.json, bench.log, time/*.time),
# затем кладёт data/tk026-vps-bench/<метка>/DONE — на этот файл смотрит wait_for тикета (диспетчер умеет только file:/deck:).
#   python tk026-pull-bench.py <метка> <user@host> <удалённый каталог замера> [--key ключ] [--known-hosts файл]
import os, subprocess, sys, time

label, host, rdir = sys.argv[1:4]
key = 'C:/Users/Георгий/.ssh/id_rsa'
kh = 'C:/Users/Георгий/.ssh/known_hosts'
dst = os.path.join(r'C:\visual projects\alpha\data\tk026-vps-bench', label)
os.makedirs(dst, exist_ok=True)
ssh = ['ssh', '-i', key, '-o', f'UserKnownHostsFile={kh}', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', host]
t_end = time.time() + 8 * 3600
while time.time() < t_end:
    try:
        r = subprocess.run(ssh + [f'test -e {rdir}/DONE'], capture_output=True, timeout=40)
        if r.returncode == 0:
            for f in ('days.csv', 'summary.json', 'bench.log'):
                out = subprocess.run(ssh + [f'cat {rdir}/{f}'], capture_output=True, timeout=60).stdout
                open(os.path.join(dst, f), 'wb').write(out)
            open(os.path.join(dst, 'DONE'), 'w').write(time.strftime('%FT%T') + '\n')
            break
    except Exception:
        pass
    time.sleep(180)
