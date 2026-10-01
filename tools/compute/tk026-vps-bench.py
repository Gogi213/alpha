#!/usr/bin/env python3
# TK-026/TK-022 (В-163): замер скорости VPS на неделе суток: доставка с ящика на диск → 3 прохода (tk022-deck-day.sh) → удаление копии.
#   python3 tk026-vps-bench.py --days 2026-01-01:2026-01-07 --tool <tk022-deck-day.sh> --home <дом замера> --out <каталог замера>
# Исходы клеток не читаются: только коды возврата, секунды, пик RSS, счётчики каталогов/байт. Ящик — только чтение (/mnt/sb ro).
# Фаза A: первые сутки одной полосой (ничего не качается параллельно) → пик памяти → число полос L для фазы B (остальные сутки).
import argparse, csv, datetime as dt, json, os, re, shutil, subprocess, sys, threading, time

MON = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
ROOT_NAME = re.compile(r'^(?P<sym>.+?)-(?P<day>\d{4}-\d\d-\d\d)(?P<tail>(-p\d+)?\.binlog(\.zst)?)$')
HOME = '/home/deck'
A = HOME + '/alpha'
ST_DIR = A + '/tk022/status'
VIEW = A + '/tk022/view'


def now():
    return time.time()


def day_plus(day, n):
    return (dt.date.fromisoformat(day) + dt.timedelta(n)).isoformat()


class Bench:
    def __init__(self, a):
        self.a = a
        self.days = self.expand(a.days)
        self.carry = day_plus(self.days[-1], 1)
        self.stage = os.path.abspath(a.stage)
        self.out = os.path.abspath(a.out)
        for d in ('logs', 'time'):
            os.makedirs(f'{self.out}/{d}', exist_ok=True)
        os.makedirs(self.stage, exist_ok=True)
        self.lock = threading.Lock()
        self.done = set()
        self.rows = []
        self.halt = threading.Event()
        self.phase_a_done = threading.Event()
        self.stop_sampler = threading.Event()
        self.lanes = 1
        self.logf = open(f'{self.out}/bench.log', 'a', buffering=1)
        self.t_start = now()

    @staticmethod
    def expand(spec):
        if ':' in spec:
            x, y = (dt.date.fromisoformat(s) for s in spec.split(':'))
            return [(x + dt.timedelta(n)).isoformat() for n in range((y - x).days + 1)]
        return spec.split(',')

    def log(self, *m):
        print(time.strftime('%H:%M:%S'), *m, file=self.logf)

    # ---------- доставка ----------
    def paths(self, day):
        mon = MON[int(day[5:7]) - 1]
        return (f'{self.a.box}/alpha/epochs/e-{mon}/root', f'{self.a.box}/alpha/derived/tk015/e-{mon}/D20/{day}')

    def plan(self, day):
        rdir, ddir = self.paths(day)
        root = {n: os.path.getsize(f'{rdir}/{n}') for n in os.listdir(rdir)
                if (m := ROOT_NAME.match(n)) and m.group('day') == day}
        d20 = {n: os.path.getsize(f'{ddir}/{n}') for n in os.listdir(ddir) if os.path.isfile(f'{ddir}/{n}')}
        if not root or not d20:
            raise RuntimeError(f'на ящике нет файлов суток {day}')
        return rdir, ddir, root, d20

    def staged_bytes(self):
        n = 0
        for dp, _, fs in os.walk(self.stage):
            for f in fs:
                try:
                    n += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
        return n

    def deliver(self, day):
        rdir, ddir, root, d20 = self.plan(day)
        dst = f'{self.stage}/{day}'
        os.makedirs(f'{dst}/root', exist_ok=True)
        os.makedirs(f'{dst}/D20', exist_ok=True)
        total = sum(root.values()) + sum(d20.values())
        ns = self.a.streams
        lists = [[] for _ in range(ns)]
        sizes = [0] * ns
        for sub, src, files in (('root', rdir, root), ('D20', ddir, d20)):
            for n, s in sorted(files.items(), key=lambda x: -x[1]):
                i = sizes.index(min(sizes))
                lists[i].append((sub, src, n))
                sizes[i] += s
        errs = []

        def one(lst):
            for sub in ('root', 'D20'):
                fs = [f'{src}/{n}' for sb, src, n in lst if sb == sub]
                if fs:
                    r = subprocess.run(['cp', '-p', '--'] + fs + [f'{dst}/{sub}/'], capture_output=True, text=True)
                    if r.returncode:
                        errs.append(r.stderr.strip()[:200])

        t0 = now()
        ths = [threading.Thread(target=one, args=(l,)) for l in lists if l]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        el = now() - t0
        bad = [n for sub, files in (('root', root), ('D20', d20)) for n, s in files.items()
               if not os.path.isfile(f'{dst}/{sub}/{n}') or os.path.getsize(f'{dst}/{sub}/{n}') != s]
        if errs or bad:
            raise RuntimeError(f'доставка {day}: {errs[:1]} несовпадений {len(bad)}')
        open(f'{dst}/.ready', 'w').write(f'{total}\n')
        self.log(f'доставка {day}: {total / 1e9:.2f} ГБ за {el:.1f} с = {total / 1e6 / el:.1f} МБ/с (потоков {ns})')
        return total, el

    def producer(self):
        order = self.days + [self.carry]
        try:
            for idx, day in enumerate(order):
                if idx >= 2:
                    self.phase_a_done.wait()
                if os.path.exists(f'{self.stage}/{day}/.ready'):
                    continue
                est = sum(sum(x.values()) for x in self.plan(day)[2:])
                while not self.halt.is_set():
                    free = shutil.disk_usage(self.stage).free
                    if self.staged_bytes() + est <= self.a.budget_gb * 1e9 and free - est >= self.a.reserve_gb * 1e9:
                        break
                    time.sleep(5)
                if self.halt.is_set():
                    return
                total, el = self.deliver(day)
                with self.lock:
                    self.deliv = getattr(self, 'deliv', {})
                    self.deliv[day] = (total, el, now())
        except Exception as e:
            self.log('ОШИБКА доставки:', repr(e))
            self.halt.set()

    def try_release(self):
        with self.lock:
            for x in self.days + [self.carry]:
                if not os.path.isdir(f'{self.stage}/{x}'):
                    continue
                need = [y for y in (x, day_plus(x, -1)) if y in self.days]
                if need and all(y in self.done for y in need):
                    shutil.rmtree(f'{self.stage}/{x}')
                    self.log(f'release {x}: копия суток удалена')

    # ---------- проходы ----------
    def env(self, **kw):
        e = dict(os.environ, HOME=HOME, STAGE=self.stage)
        e.update(kw)
        return e

    def run_pass(self, day, n, res):
        tf = f'{self.out}/time/{day}.p{n}.time'
        lg = open(f'{self.out}/logs/{day}.p{n}.log', 'w')
        t0 = now()
        r = subprocess.run(['/usr/bin/time', '-f', '%e %U %S %M', '-o', tf, 'bash', self.a.tool, day, self.a.home],
                           env=self.env(PASS=str(n)), stdout=lg, stderr=subprocess.STDOUT)
        t1 = now()
        el = u = s = m = 0.0
        try:
            p = open(tf).read().split('\n')
            p = [x for x in p if x.strip()][-1].split()
            el, u, s, m = float(p[0]), float(p[1]), float(p[2]), float(p[3])
        except Exception:
            pass
        res[n] = dict(rc=r.returncode, t0=t0, t1=t1, wall=t1 - t0, cpu=u + s, rss_mb=m / 1024)
        self.log(f'{day} проход {n}: rc={r.returncode} {t1 - t0:.0f} с, ЦП {u + s:.0f} с, пик RSS {m / 1024:.0f} МБ')

    def count_cells(self, day, since):
        b5 = f'{self.a.home}/b5'
        n = nb = 0
        for e in os.scandir(b5):
            p = f'{e.path}/{day}'
            if e.name.startswith('.') or not os.path.isdir(p) or os.path.getmtime(p) < since:
                continue
            n += 1
            for f in os.scandir(p):
                try:
                    nb += f.stat().st_size
                except OSError:
                    pass
        return n, nb

    def run_day(self, day, lanes_cfg):
        nxt = day_plus(day, 1)
        while not self.halt.is_set() and not (os.path.exists(f'{self.stage}/{day}/.ready') and os.path.exists(f'{self.stage}/{nxt}/.ready')):
            time.sleep(3)
        if self.halt.is_set():
            return
        r = subprocess.run(['bash', self.a.tool, day, self.a.home], env=self.env(PREP='1'), capture_output=True, text=True)
        if r.returncode:
            self.log(f'{day}: PREP rc={r.returncode} {r.stderr.strip()[:200]}')
            self.halt.set()
            return
        grid_done = f'{ST_DIR}/{day}.p1.grid-done'
        t_day = now()
        res = {}
        th = {1: threading.Thread(target=self.run_pass, args=(day, 1, res))}
        th[1].start()
        while th[1].is_alive() and not os.path.exists(grid_done):
            time.sleep(3)
        if not th[1].is_alive() and res.get(1, {}).get('rc', 1) != 0:
            self.log(f'{day}: проход 1 упал — 2 и 3 не ставлю')
            self.halt.set()
            return
        for n in (2, 3):
            th[n] = threading.Thread(target=self.run_pass, args=(day, n, res))
            th[n].start()
        for n in (1, 2, 3):
            th[n].join()
        t_end = now()
        ncell, nbytes = self.count_cells(day, t_day)
        tmpb = 0
        for t in (f'.cellstmp-{day}', f'.t9tmp-touch-{day}', f'.g86tmp-{day}'):
            p = f'{self.a.home}/b5/{t}'
            if os.path.isdir(p):
                tmpb += sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(p) for f in fs)
                shutil.rmtree(p)
        shutil.rmtree(f'{VIEW}/{day}', ignore_errors=True)
        row = dict(day=day, lanes=lanes_cfg, t0=t_day, t1=t_end, ok=all(res.get(n, {}).get('rc', 1) == 0 for n in (1, 2, 3)),
                   cells=ncell, cell_bytes=nbytes, tmp_bytes=tmpb, **{f'p{n}': res.get(n) for n in (1, 2, 3)})
        with self.lock:
            self.rows.append(row)
            if row['ok']:
                self.done.add(day)
            else:
                self.halt.set()
        self.log(f'{day}: готов за {t_end - t_day:.0f} с, ok={row["ok"]}, каталогов клеток {ncell} ({nbytes / 1e6:.1f} МБ), temp {tmpb / 1e6:.0f} МБ')
        self.try_release()

    # ---------- семплер ----------
    def sampler(self):
        def cpu():
            f = open('/proc/stat').readline().split()[1:]
            v = [int(x) for x in f]
            return sum(v), v[3] + v[4], v[4]

        mi = lambda: {l.split(':')[0]: int(l.split()[1]) for l in open('/proc/meminfo') if ':' in l}
        w = csv.writer(open(f'{self.out}/samples.csv', 'w', newline=''), lineterminator='\n')
        w.writerow(['ts', 'cpu_busy_pct', 'iowait_pct', 'load1', 'mem_used_mb', 'swap_used_mb', 'n_grid', 'grid_rss_mb', 'disk_free_gb'])
        t0, i0, w0 = cpu()
        while not self.stop_sampler.wait(5):
            t1, i1, w1 = cpu()
            dt_ = max(t1 - t0, 1)
            busy, iow = 100 * (1 - (i1 - i0) / dt_), 100 * (w1 - w0) / dt_
            t0, i0, w0 = t1, i1, w1
            m = mi()
            n = rss = 0
            for p in os.listdir('/proc'):
                if not p.isdigit():
                    continue
                try:
                    if not open(f'/proc/{p}/comm').read().startswith('alpha'):
                        continue
                    for l in open(f'/proc/{p}/status'):
                        if l.startswith('VmRSS:'):
                            rss += int(l.split()[1])
                            n += 1
                except OSError:
                    pass
            free = shutil.disk_usage(self.stage).free
            w.writerow([f'{now():.0f}', f'{busy:.1f}', f'{iow:.1f}', open('/proc/loadavg').read().split()[0],
                        (m['MemTotal'] - m['MemAvailable']) // 1024, (m['SwapTotal'] - m['SwapFree']) // 1024, n, rss // 1024,
                        f'{free / 1e9:.1f}'])
            if free < self.a.panic_gb * 1e9:
                self.log(f'ТРЕВОГА: свободно {free / 1e9:.1f} ГБ — новые сутки не начинаю')
                self.halt.set()

    def window(self, t0, t1):
        rows = [r for r in csv.DictReader(open(f'{self.out}/samples.csv')) if t0 <= float(r['ts']) <= t1]
        if not rows:
            return {}
        f = lambda k: [float(r[k]) for r in rows]
        return dict(cpu_avg=sum(f('cpu_busy_pct')) / len(rows), iowait_avg=sum(f('iowait_pct')) / len(rows),
                    mem_used_peak_mb=max(f('mem_used_mb')), swap_peak_mb=max(f('swap_used_mb')),
                    grid_rss_peak_mb=max(f('grid_rss_mb')), grid_n_avg=sum(f('n_grid')) / len(rows), grid_n_max=max(f('n_grid')),
                    disk_free_min_gb=min(f('disk_free_gb')))

    # ---------- ход ----------
    def go(self):
        self.log(f'старт: сутки {self.days[0]}…{self.days[-1]}, довесок {self.carry}, дом {self.a.home}, stage {self.stage}')
        threading.Thread(target=self.sampler, daemon=True).start()
        prod = threading.Thread(target=self.producer, daemon=True)
        prod.start()
        self.run_day(self.days[0], 1)
        row = self.rows[0] if self.rows else None
        if not row or not row['ok']:
            self.log('фаза A не удалась — остановка')
        else:
            rs = [row[f'p{n}']['rss_mb'] for n in (1, 2, 3)]
            lane_mb = max(rs[0], rs[1] + rs[2]) + 300
            l_mem = int(self.a.mem_budget_mb // lane_mb)
            self.lanes = max(1, min(l_mem, self.a.max_lanes, len(self.days) - 1))
            self.log(f'фаза A: пик RSS проходов {rs[0]:.0f}/{rs[1]:.0f}/{rs[2]:.0f} МБ, полоса ≈ {lane_mb:.0f} МБ, бюджет {self.a.mem_budget_mb:.0f} МБ → полос {self.lanes}')
            self.phase_a_done.set()
            q = list(self.days[1:])
            qlock = threading.Lock()

            def lane():
                while not self.halt.is_set():
                    with qlock:
                        if not q:
                            return
                        d = q.pop(0)
                    self.run_day(d, self.lanes)

            ls = [threading.Thread(target=lane) for _ in range(self.lanes)]
            for t in ls:
                t.start()
                time.sleep(2)
            for t in ls:
                t.join()
        self.phase_a_done.set()
        t_end = now()
        self.stop_sampler.set()
        time.sleep(6)
        rows = sorted(self.rows, key=lambda r: r['day'])
        deliv = getattr(self, 'deliv', {})
        with open(f'{self.out}/days.csv', 'w', newline='') as fh:
            w = csv.writer(fh, lineterminator='\n')
            w.writerow(['day', 'lanes', 'gb', 'deliver_s', 'deliver_mbs', 'p1_s', 'p2_s', 'p3_s', 'day_wall_s', 'p1_cpu_s', 'p2_cpu_s', 'p3_cpu_s',
                        'p1_rss_mb', 'p2_rss_mb', 'p3_rss_mb', 'cpu_avg_pct', 'iowait_avg_pct', 'mem_used_peak_mb', 'swap_peak_mb',
                        'grid_n_avg', 'grid_n_max', 'cells_dirs', 'cells_mb', 'tmp_mb', 'ok'])
            for r in rows:
                tot, el, _ = deliv.get(r['day'], (0, 0, 0))
                wn = self.window(r['t0'], r['t1'])
                P = [r.get(f'p{n}') or {} for n in (1, 2, 3)]
                w.writerow([r['day'], r['lanes'], f'{tot / 1e9:.2f}', f'{el:.1f}', f'{tot / 1e6 / el:.1f}' if el else '',
                            *[f'{p.get("wall", 0):.0f}' for p in P], f'{r["t1"] - r["t0"]:.0f}', *[f'{p.get("cpu", 0):.0f}' for p in P],
                            *[f'{p.get("rss_mb", 0):.0f}' for p in P], f'{wn.get("cpu_avg", 0):.0f}', f'{wn.get("iowait_avg", 0):.0f}',
                            f'{wn.get("mem_used_peak_mb", 0):.0f}', f'{wn.get("swap_peak_mb", 0):.0f}', f'{wn.get("grid_n_avg", 0):.1f}',
                            f'{wn.get("grid_n_max", 0):.0f}', r['cells'], f'{r["cell_bytes"] / 1e6:.1f}', f'{r["tmp_bytes"] / 1e6:.0f}', int(r['ok'])])
        b = [r for r in rows if r['lanes'] > 1 or r['day'] != self.days[0]]
        summ = dict(lanes=self.lanes, wall_total_s=t_end - self.t_start, days_ok=len([r for r in rows if r['ok']]), of=len(self.days))
        if b:
            t0, t1 = min(r['t0'] for r in b), max(r['t1'] for r in b)
            gb = sum(deliv.get(r['day'], (0,))[0] for r in b) / 1e9
            summ.update(phaseB_wall_s=t1 - t0, phaseB_gb=gb, phaseB_days=len(b), phaseB_s_per_gb=(t1 - t0) / gb if gb else None, **self.window(t0, t1))
        json.dump(summ, open(f'{self.out}/summary.json', 'w'), indent=1)
        # единственная копия суток — на ящике; здесь остаётся только stage (пустой после release) и мелкие выходы b5
        for x in os.listdir(self.stage):
            p = f'{self.stage}/{x}'
            if re.fullmatch(r'\d{4}-\d\d-\d\d', x) and os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
        open(f'{self.out}/DONE', 'w').write(time.strftime('%FT%T') + '\n')
        self.log('DONE', json.dumps(summ))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', required=True)
    ap.add_argument('--tool', required=True)
    ap.add_argument('--home', required=True, help='дом замера (b5 — выходы); отличается от e-<мес>')
    ap.add_argument('--out', required=True)
    ap.add_argument('--stage', default=A + '/tk026/stage')
    ap.add_argument('--box', default='/mnt/sb')
    ap.add_argument('--streams', type=int, default=4)
    ap.add_argument('--budget-gb', type=float, default=9.0)
    ap.add_argument('--reserve-gb', type=float, default=5.0)
    ap.add_argument('--panic-gb', type=float, default=3.0)
    ap.add_argument('--mem-budget-mb', type=float, default=6000.0)
    ap.add_argument('--max-lanes', type=int, default=3)
    Bench(ap.parse_args()).go()


if __name__ == '__main__':
    main()
