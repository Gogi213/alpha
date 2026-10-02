#!/usr/bin/env python3
# TK-026: конвейер подкачки суток с Storage Box в stage деки (по В-162 — RAM, /dev/shm/alpha-stage; только чтение ящика, счёт не запускает).
#   python3 tk026-pipeline.py --days 2026-01-01:2026-01-08 --stage /dev/shm/alpha-stage --meta-dir ~/alpha/stage --budget-gb 6 --reserve-gb 1
#   (_done/ и pipeline.csv — в --meta-dir, на диске: мелкие, переживают перезагрузку; сами сутки — только в --stage)
# Формат стыка с TK-022 (Исследователь):
#   <stage>/<D>/root/  бинлоги суток D (<SYM>-<D>.binlog, mtime с ящика, .events не качаются)
#   <stage>/<D>/D20/   кэш подходов суток D (ящик alpha/derived/tk015/e-<мес>/D20/<D>/)
#   <stage>/<D>/.ready пишется ПОСЛЕДНИМ: все файлы есть, размеры == размерам на ящике
#   <stage>/<D>/.release пишет потребитель; тогда каталог суток удаляется (только внутри --stage), остаётся _done/<D>
# Сутки качаются строго в порядке --days; новые сутки не начинаются, пока занято > --budget-gb или свободно < --reserve-gb.
# На ящике ничего не меняется и не удаляется. Остановка — файл <stage>/STOP или SIGTERM (докачка не теряется: .part/).
import argparse, csv, datetime as dt, os, re, shutil, signal, subprocess, sys, threading, time

MON = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
BOX = 'u677479@u677479.your-storagebox.de'
KEY = os.path.expanduser('~/.ssh/id_storagebox')
ROOT_NAME = re.compile(r'^(?P<sym>.+?)-(?P<day>\d{4}-\d\d-\d\d)(?P<tail>(-p\d+)?\.binlog(\.zst)?)$')


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def ssh_ls(path):
    r = subprocess.run(['ssh', '-n', '-p', '23', '-i', KEY, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', BOX, f'ls -l {path}'],
                       capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise RuntimeError(f'ls {path}: {r.stderr.strip()}')
    out = {}
    for ln in r.stdout.splitlines():
        p = ln.split()
        if len(p) >= 9 and p[4].isdigit():
            out[p[8]] = int(p[4])
    return out


def safe_rmtree(stage, d):
    rs, rd = os.path.realpath(stage), os.path.realpath(d)
    if os.path.islink(d) or not rd.startswith(rs + os.sep) or rd == rs or os.path.basename(rd).startswith('_'):
        raise RuntimeError(f'отказ удалять вне stage: {d}')
    shutil.rmtree(rd)


def day_dirs(stage):
    return [d for d in sorted(os.listdir(stage)) if re.fullmatch(r'\d{4}-\d\d-\d\d', d) and os.path.isdir(os.path.join(stage, d))]


def mem_note():
    mi = {l.split(':')[0]: int(l.split()[1]) for l in open('/proc/meminfo') if ':' in l}
    return f"MemAvailable {mi['MemAvailable']/1e6:.1f} ГБ, своп занят {(mi['SwapTotal']-mi['SwapFree'])/1e6:.2f} ГБ"


def staged_bytes(stage):
    n = 0
    for d in day_dirs(stage):
        for dp, _, fs in os.walk(os.path.join(stage, d)):
            for f in fs:
                try:
                    n += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
    return n


def process_releases(stage, meta):
    freed = []
    for d in day_dirs(stage):
        if os.path.exists(os.path.join(stage, d, '.release')):
            safe_rmtree(stage, os.path.join(stage, d))
            open(os.path.join(meta, '_done', d), 'w').write(time.strftime('%FT%T'))
            freed.append(d)
    return freed


def plan_day(day, args, lscache):
    d = dt.date.fromisoformat(day)
    mon = MON[d.month - 1]
    out = {}
    for sub, path in (('root', args.root_fmt.format(mon=mon, year=d.year, day=day)),
                      ('D20', args.d20_fmt.format(mon=mon, year=d.year, day=day))):
        if path not in lscache:
            lscache[path] = ssh_ls(path)
        lst = lscache[path]
        if sub == 'root':
            lst = {n: s for n, s in lst.items() if (m := ROOT_NAME.match(n)) and m.group('day') == day}
        out[sub] = (path, lst)
    for sub in ('root', 'D20'):
        if not out[sub][1]:
            raise RuntimeError(f'на ящике нет файлов {sub} суток {day}: {out[sub][0]}')
    return out


def have(dst, sub, n, s):
    p = os.path.join(dst, sub, n)
    return os.path.isfile(p) and os.path.getsize(p) == s


def fetch_day(day, plan, stage, streams, bwlimit):
    dst = os.path.join(stage, day)
    for sub in ('root', 'D20'):
        os.makedirs(os.path.join(dst, sub), exist_ok=True)
    total = sum(s for _, lst in plan.values() for s in lst.values())
    todo = [(sub, n, s) for sub, (_, lst) in plan.items() for n, s in lst.items() if not have(dst, sub, n, s)]
    t0 = time.time()
    lists = [[] for _ in range(streams)]
    sizes = [0] * streams
    for sub, n, s in sorted(todo, key=lambda x: -x[2]):
        i = sizes.index(min(sizes))
        lists[i].append((sub, n))
        sizes[i] += s
    errs = []

    def one(i):
        for sub in ('root', 'D20'):
            names = [n for sb, n in lists[i] if sb == sub]
            if not names:
                continue
            lf = os.path.join(dst, f'.list{i}-{sub}')
            open(lf, 'w').write('\n'.join(names) + '\n')
            cmd = ['rsync', '-t', '--partial-dir=.part', '-e', f'ssh -p 23 -i {KEY} -o BatchMode=yes']
            if bwlimit:
                cmd.append(f'--bwlimit={bwlimit}')
            cmd += ['--files-from', lf, f'{BOX}:{plan[sub][0]}/', os.path.join(dst, sub) + '/']
            r = subprocess.run(cmd, capture_output=True, text=True)
            os.remove(lf)
            if r.returncode:
                errs.append(f'{sub} rsync rc={r.returncode} {r.stderr.strip()[:200]}')

    ths = [threading.Thread(target=one, args=(i,)) for i in range(streams) if lists[i]]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    for sub in ('root', 'D20'):
        shutil.rmtree(os.path.join(dst, sub, '.part'), ignore_errors=True)
    bad = [f'{sub}/{n}' for sub, (_, lst) in plan.items() for n, s in lst.items() if not have(dst, sub, n, s)]
    if errs or bad:
        raise RuntimeError(f'доставка {day}: ошибки={errs} несовпадений={len(bad)} {bad[:3]}')
    open(os.path.join(dst, '.ready'), 'w').write(f'{total}\n')
    return total, time.time() - t0, sum(s for _, _, s in todo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', required=True, help='A:B (включительно) или список через запятую — порядок подкачки')
    ap.add_argument('--stage', default=os.path.expanduser('~/alpha/stage'))
    ap.add_argument('--meta-dir', default=None, help='где _done/ и pipeline.csv (по умолчанию = --stage)')
    ap.add_argument('--root-fmt', default='alpha/epochs/e-{mon}/root', help='{mon}=jan..dec {year} {day}')
    ap.add_argument('--d20-fmt', default='alpha/derived/tk015/e-{mon}/D20/{day}')
    ap.add_argument('--budget-gb', type=float, default=14.0, help='потолок занятого stage (новые сутки не начинаются выше)')
    ap.add_argument('--reserve-gb', type=float, default=8.0, help='свободное место в --stage (tmpfs: место RAM-диска; MemAvailable не проверяется, только печатается), которое не трогаем')
    ap.add_argument('--streams', type=int, default=2)
    ap.add_argument('--bwlimit', type=int, default=0, help='rsync --bwlimit КБ/с на поток; 0 — без лимита')
    ap.add_argument('--once', action='store_true', help='качать, пока все сутки не .ready, и выйти (без уборки .release)')
    args = ap.parse_args()
    if ':' in args.days and ',' not in args.days:
        a, b = (dt.date.fromisoformat(x) for x in args.days.split(':'))
        days = [(a + dt.timedelta(n)).isoformat() for n in range((b - a).days + 1)]
    else:
        days = [x for x in args.days.split(',') if x]
    stage = os.path.abspath(args.stage)
    meta = os.path.abspath(args.meta_dir or args.stage)
    os.makedirs(stage, exist_ok=True)
    os.makedirs(os.path.join(meta, '_done'), exist_ok=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    days = [d for d in days if not os.path.exists(os.path.join(meta, '_done', d))]
    stat = open(os.path.join(meta, 'pipeline.csv'), 'a', newline='')
    cw = csv.writer(stat, lineterminator='\n')
    state = {'fetching_done': False, 'err': None}

    def stopped():
        return stop.is_set() or os.path.exists(os.path.join(stage, 'STOP'))

    def producer():
        lscache = {}
        try:
            for day in days:
                if os.path.exists(os.path.join(stage, day, '.ready')):
                    continue
                plan = plan_day(day, args, lscache)
                est = sum(s for _, lst in plan.values() for s in lst.values())
                log(f'план {day}: {est/1e9:.2f} ГБ, в stage {staged_bytes(stage)/1e9:.2f} ГБ; {mem_note()}')
                while not stopped():
                    used = staged_bytes(stage)
                    free = shutil.disk_usage(stage).free
                    if used + est <= args.budget_gb * 1e9 and free - est >= args.reserve_gb * 1e9:
                        break
                    if used == 0 and free - est < args.reserve_gb * 1e9:
                        raise RuntimeError(f'диск: свободно {free/1e9:.1f} ГБ, сутки {est/1e9:.2f} ГБ, резерв {args.reserve_gb} ГБ, stage пуст')
                    time.sleep(5)
                if stopped():
                    return
                tot, el, got = fetch_day(day, plan, stage, args.streams, args.bwlimit)
                log(f'готовы {day}: {tot/1e9:.2f} ГБ (скачано {got/1e9:.2f}) за {el:.0f} с = {got/1e6/max(el,1e-9):.2f} МБ/с; {mem_note()}')
                cw.writerow(['fetch', day, tot, got, f'{el:.1f}', time.strftime('%FT%T')])
                stat.flush()
        except Exception as e:
            state['err'] = repr(e)
            log('ОШИБКА доставки:', e)
        finally:
            state['fetching_done'] = True

    th = threading.Thread(target=producer, daemon=True)
    th.start()
    while th.is_alive():
        if not args.once:
            for d in process_releases(stage, meta):
                log(f'release {d}: каталог удалён, свободно {shutil.disk_usage(stage).free/1e9:.1f} ГБ')
        time.sleep(3)
    if not args.once:
        while not stopped():
            left = [d for d in days if not os.path.exists(os.path.join(meta, '_done', d))]
            if not left:
                break
            for d in process_releases(stage, meta):
                log(f'release {d}: каталог удалён, свободно {shutil.disk_usage(stage).free/1e9:.1f} ГБ')
            time.sleep(5)
    log('готово' if not state['err'] else f'выход с ошибкой: {state["err"]}')
    sys.exit(1 if state['err'] else 0)


if __name__ == '__main__':
    main()
