#!/usr/bin/env python3
# TK-026 (только чтение): доля байт суток, которую счёт TK-022 реально читает — по монетам и по времени.
#   python3 tk026-thin-share.py /dev/shm/alpha-stage/2026-01-01 [...]   (каталоги суток stage: root/ + D20/)
# Монета без подходов в D20 счётом пропускается до чтения событий (bounce_grid.rs: touches_total == 0).
# Время: объединение окон [arm_ms, arm_ms + H] по подходам монеты; H = дедлайн 14400 с + ttl 1800 с (максимум в cells-скрипте).
# Доля времени монеты умножается на байты её бинлога (байты равномерны по времени — приближение).
import csv, os, sys

H_ALL = (14400 + 1800) * 1000
H_SHORT = (3600 + 1800) * 1000
AGE_MIN = 900 * 1000
REG = '/home/deck/alpha/epochs/e-jan/study/regime'  # минутные ряды BTC; пороги — самые мягкие из 167 --set cells-скрипта
THR = {'btc_ret_1h_bps': -21.17, 'btc_ret_2h_bps': -30.56, 'btc_ret_3h_bps': -38.75, 'btc_ret_4h_bps': -44.55}


def union_ms(starts, h, lo, hi):
    tot = 0
    cur_s = cur_e = None
    for s in sorted(starts):
        e = s + h
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                tot += min(cur_e, hi) - max(cur_s, lo)
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        tot += min(cur_e, hi) - max(cur_s, lo)
    return max(tot, 0)


rows = []


def regime_on(day):
    on = set()
    for r in csv.DictReader(open(os.path.join(REG, day + '.csv'))):
        if any(r[k] != '' and float(r[k]) <= v for k, v in THR.items()):
            on.add(int(r['minute_ms']))
    return on

for day_dir in sys.argv[1:]:
    day = os.path.basename(day_dir.rstrip('/'))
    import datetime as dt
    lo = int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    hi = lo + 86400_000
    tb = zb = 0
    cov_all = cov_bid = cov_short = cov_reg = 0.0
    reg = regime_on(day)
    reg_min = len(reg) / 1440
    nsym = nzero = 0
    for fn in sorted(os.listdir(os.path.join(day_dir, 'root'))):
        if not fn.endswith('.binlog'):
            continue
        sym = fn.rsplit('-' + day, 1)[0]
        sz = os.path.getsize(os.path.join(day_dir, 'root', fn))
        tb += sz
        nsym += 1
        ap = os.path.join(day_dir, 'D20', f'approaches-{sym}.csv')
        arm_all, arm_bid, arm_reg = [], [], []
        if os.path.isfile(ap):
            for r in csv.DictReader(open(ap)):
                t = int(r['arm_ms'])
                arm_all.append(t)
                if r['side'] == 'bid' and int(r['age_ms']) >= AGE_MIN:
                    arm_bid.append(t)
                    if t // 60000 * 60000 in reg:
                        arm_reg.append(t)
        if not arm_all:
            nzero += 1
            zb += sz
            continue
        cov_all += sz * union_ms(arm_all, H_ALL, lo, hi) / 86400_000
        cov_short += sz * union_ms(arm_all, H_SHORT, lo, hi) / 86400_000
        cov_bid += sz * union_ms(arm_bid, H_ALL, lo, hi) / 86400_000
        cov_reg += sz * union_ms(arm_reg, H_ALL, lo, hi) / 86400_000
    rows.append((day, nsym, nzero, tb, zb, cov_all, cov_short, cov_bid, cov_reg))
    print(f'{day}: монет {nsym}, без подходов {nzero} ({zb/tb*100:.1f} % байт), день {tb/1e9:.3f} ГБ; '
          f'окна 4,5 ч по всем подходам {cov_all/tb*100:.1f} % байт; по подходам bid&возраст≥15мин {cov_bid/tb*100:.1f} %; '
          f'окна 1,5 ч по всем {cov_short/tb*100:.1f} %; + режим BTC (мягчайший порог) {cov_reg/tb*100:.1f} % (минут режима {reg_min*100:.0f} %)')
T = sum(r[3] for r in rows)
if T:
    print(f'ИТОГО {len(rows)} сут, {T/1e9:.3f} ГБ: нулевые монеты {sum(r[4] for r in rows)/T*100:.1f} %; '
          f'время (4,5 ч, все) {sum(r[5] for r in rows)/T*100:.1f} %; (bid&возраст≥15мин) {sum(r[7] for r in rows)/T*100:.1f} %; + режим BTC {sum(r[8] for r in rows)/T*100:.1f} %')
