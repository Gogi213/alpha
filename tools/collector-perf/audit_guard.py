#!/usr/bin/env python3
"""Сторож подключений для аудитов и теневых прогонов (collector-perf, решение координатора 14.11): любой запуск коллектора, который ходит к бирже с IP srv, проходит через него.
Лимиты биржи на IP — BingX 60 сокетов (код 100419), Gate 300 соединений, OKX 3 подключения в секунду, Binance 300 подключений за 5 мин, Bybit 500 за 5 мин на домен; вместе с боевым процессом и всеми уже идущими копиями —
не больше 80 % лимита, запуски вразнобой. Сторож считает по живым процессам `collect run --exchange <биржа>` (боевой тоже): сокеты :443 (`ss -p`) и события `connected` в их `--root` за 5 мин.
usage: audit_guard.py <биржа> <линий нового процесса> [--dry]
Выход 0: допущено — печатает одно число, `--connect-budget` для нового процесса (подключений за 1 с у OKX/BingX, за 5 мин у Binance/Bybit); сторож уже выждал раскладку по времени.
Выход 3: отказ — причина в stderr; запускать нельзя. Остаточный риск OKX/BingX: у каждого процесса свой бюджет (2/с у боевого, 1/с у копии), общего замка с боевым процессом нет — поэтому
копий на такую биржу не больше двух и боевой процесс не должен подключаться в последние 30 с (общий замок со всеми процессами — отдельная задача с накатом)."""
import glob
import json
import os
import re
import subprocess
import sys
import time

EX = sys.argv[1] if len(sys.argv) > 1 else ""
LANES = int(sys.argv[2]) if len(sys.argv) > 2 else 0
DRY = "--dry" in sys.argv
CAP = 0.8
# (лимит сокетов на IP | None, лимит подключений за окно (число, секунды) | None)
LIMITS = {"bingx": (60, (3, 1)), "okx": (None, (3, 1)), "binance": (None, (300, 300)), "bybit": (None, (500, 300)),
          "gate": (300, (5, 1))}  # gate: 300 соединений с IP (документация); числа подключений в секунду в документации нет — назначенные 5/с (бюджет коллектора); сторож — 80 % = 240 сокетов
MAX_COPIES_PER_SEC_EXCHANGE = 2
LEDGER = "/data/perf/guard"


def refuse(msg):
    print("ОТКАЗ сторожа подключений: " + msg, file=sys.stderr)
    sys.exit(3)


if EX not in LIMITS or LANES <= 0:
    refuse("использование: audit_guard.py <binance|bybit|okx|bingx|gate> <линий> [--dry]")
sock_lim, (win_n, win_s) = LIMITS[EX]
if EX == "okx" and os.environ.get("ALPHA_ALLOW_OKX_COPY") != "1":   # решение координатора 14.11: копий на OKX нет, пока нет общего замка с боевым процессом, кроме случаев, когда этого требует задача
    refuse("okx: копии запрещены до общего замка подключений (ALPHA_ALLOW_OKX_COPY=1 — только если задача требует и координатор разрешил)")

# живые процессы коллектора этой биржи: pid, корень данных, боевой ли
procs = []
for ln in subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout.splitlines():
    if " collect run " not in ln or f"--exchange {EX}" not in ln:
        continue
    pid = int(ln.split(None, 1)[0])
    m = re.search(r"--root (\S+)", ln)
    root = m.group(1) if m else ""
    procs.append((pid, root, not re.search(r"/(audit|a8|shadow|st|ab|fw|perf)/", root)))
pids = {p[0] for p in procs}
copies = [p for p in procs if not p[2]]

# сокеты :443 этих процессов
sockets = 0
for ln in subprocess.run(["ss", "-tnHp", "state", "established"], capture_output=True, text=True).stdout.splitlines():
    f = ln.split()
    if len(f) >= 4 and f[3].endswith(":443"):
        m = re.search(r"pid=(\d+)", ln)
        if m and int(m.group(1)) in pids:
            sockets += 1

# подключения (`connected`) за окно по событиям всех этих процессов и когда подключался боевой
now = time.time()
recent, last_prod, last_any = 0, 0.0, 0.0
for pid, root, prod in procs:
    for f in glob.glob(root + "/events-*.jsonl"):
        try:
            size = os.path.getsize(f)
            with open(f, "rb") as fh:
                fh.seek(max(0, size - 400_000))
                data = fh.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        for ln in data.splitlines():
            if '"connected"' not in ln:
                continue
            try:
                j = json.loads(ln)
            except ValueError:
                continue
            if j.get("ev") != "connected":
                continue
            t = j["ts_ms"] / 1000
            if now - t <= 300:
                recent += 1
            last_any = max(last_any, t)
            if prod:
                last_prod = max(last_prod, t)

# ледгер: когда сторож допустил прошлый запуск
os.makedirs(LEDGER, exist_ok=True)
led_path = f"{LEDGER}/{EX}.json"
try:
    led = json.load(open(led_path))
except (OSError, ValueError):
    led = {"t": 0, "lanes": 0, "budget": 1}

info = f"{EX}: сокетов {sockets}, подключений за 5 мин {recent}, копий {len(copies)}, новый процесс +{LANES} линий"
if sock_lim is not None and sockets + LANES > CAP * sock_lim:
    refuse(f"{info}: сокетов было бы {sockets + LANES} > {CAP:.0%} лимита {sock_lim}")
if win_s == 300:
    cap = int(CAP * win_n)
    if recent + LANES > cap:
        refuse(f"{info}: подключений за 5 мин было бы {recent + LANES} > {CAP:.0%} лимита {win_n} ({cap})")
    budget = max(LANES, min(40, cap - recent))
    wait = 0.0
else:
    if len(copies) >= MAX_COPIES_PER_SEC_EXCHANGE:
        refuse(f"{info}: уже {len(copies)} копии на бирже с лимитом {win_n} в секунду (допустимо {MAX_COPIES_PER_SEC_EXCHANGE})")
    if now - last_prod < 30:
        refuse(f"{info}: боевой процесс подключался {now - last_prod:.0f} с назад (нужна тишина 30 с)")
    budget = 1                        # подключений за 1 с у копии; у боевого — 2
    # раскладка: новый процесс начинает после того, как прошлый успеет подключить свои линии на своём бюджете, плюс 3 с
    wait = max(0.0, led["t"] + led["lanes"] / max(led.get("budget", 1), 1) + 3 - now)
if wait > 120:
    refuse(f"{info}: слишком близко к прошлому запуску ({wait:.0f} с)")
print(f"{info}: допущено, бюджет {budget}, ждать {wait:.0f} с", file=sys.stderr)
if not DRY:
    time.sleep(wait)
    json.dump({"t": time.time(), "lanes": LANES, "budget": budget}, open(led_path, "w"))
print(budget)
