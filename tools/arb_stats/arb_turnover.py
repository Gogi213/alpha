"""Оборот за сутки по биржам и монетам для вкладки «Арбитраж»: служба раз в 5 минут (юнит arb-turnover.service), отдельно от arb-live.

Берёт записанные сделки сборщиков (trade<N>-<метка>.tsv.zst; запись t — parse_trades.py), складывает $ = цена × количество × размер контракта (OKX — ctVal) по минутам
времени сделки T = приём − lag − (E−T) для каждого (биржа, символ) и каждый проход пишет <live>/turnover.json: {"t0", "t1", "written", "coin": {монета: {биржа: $}}} за окно
[t1 − 24 ч, t1), t1 — минута до «сейчас − 60 с», как у arb.json. Монеты и символы — как в arb_daemon.coin_map (одна запись монеты на бирже — та же, что в паре). Читает все символы
файлов (символ, появившийся в пуле позже, не теряет прошлые часы). Страница получает оборот через arb_api.py: строка таблицы v = [$ первой биржи, $ второй].
Состояние — <live>/turnover-state.pkl (минутные ряды и позиции чтения файлов); без него — bootstrap пулом процессов по файлам окна, дальше дочитывание с позиции.
Запуск: python3 arb_turnover.py run <live> [PAR] | once <live> [PAR] | boot <live> [PAR]. Файл /data/perf/HOLD — проход пропускается. Нужен пакет zstandard.
"""
import glob
import json
import os
import pickle
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import arb_daemon  # noqa: E402
import arb_live  # noqa: E402
import parse_inc  # noqa: E402
import parse_trades  # noqa: E402
from common import EXCHANGES, ROOTS  # noqa: E402

MIN = 60_000
PERIOD_S = arb_daemon.PERIOD_S
TICK_OFFSET_S = 30            # проход через 30 с после границы периода (arb-live — через 15 с)
MARGIN_MS = arb_daemon.MARGIN_MS
WINDOW_MS = arb_live.WINDOW_MS
KEEP_MIN = 30                 # минутные ряды держим на 30 минут глубже окна
VERSION = 1
TRADE = re.compile(r"trade(\d+)-(\d+)\.tsv\.zst$")


def log(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)


class AllSyms:
    """«want» для consume: берём все символы файла (какие из них нужны — решает карта монет при записи)."""

    def __contains__(self, s):
        return True


def units():
    """{биржа: {символ (bytes): размер контракта}} — ctVal из всех пулов (только OKX; у остальных количество в монете)."""
    out = {ex: {} for ex in EXCHANGES}
    for f in glob.glob(f"{ROOTS['okx']}/pool-*.json"):
        for s, m in (json.loads(Path(f).read_text(encoding="utf-8")).get("meta") or {}).items():
            out["okx"][s.encode()] = float(m.get("ctVal") or 1.0)
    return out


# --- минутные ряды (состояние — обычный dict, чтобы pickle не зависел от имени модуля) -------------------------------------

def new_state(m0):
    return {"version": VERSION, "m0": m0, "bins": {}, "parse": {}, "t_a": None}


def add_bins(bins, m0, key, minute, usd):
    """bins[key][m − m0] += usd для минут minute (m ≥ m0)."""
    ok = minute >= m0
    if not ok.all():
        minute, usd = minute[ok], usd[ok]
    if not len(minute):
        return
    lo, hi = int(minute.min()) - m0, int(minute.max()) - m0 + 1
    a = bins.get(key)
    if a is None:
        a = bins[key] = np.zeros(hi)
    elif len(a) < hi:
        a = bins[key] = np.concatenate([a, np.zeros(hi - len(a))])
    a[lo:hi] += np.bincount(minute - m0 - lo, weights=usd, minlength=hi - lo)


def ingest(bins, m0, ex, arrs, unit):
    """Записи одного куска файла (parse_trades.consume: e, rx, p, q, m, et по символам) -> минутные ряды."""
    for sb, a in arrs.items():
        e, et = np.frombuffer(a[0], "i8"), np.frombuffer(a[5], "i4")
        usd = np.frombuffer(a[2], "f8") * np.frombuffer(a[3], "f8") * unit.get(sb, 1.0)
        add_bins(bins, m0, (ex, sb.decode()), (e - et) // MIN, usd)


def window_sum(bins, m0, key, lo_min, hi_min):
    a = bins.get(key)
    if a is None:
        return 0.0
    return float(a[max(0, lo_min - m0):max(0, hi_min - m0)].sum())


def cut(st, m_new):
    if m_new <= st["m0"]:
        return
    d = m_new - st["m0"]
    st["bins"] = {k: a[d:] for k, a in st["bins"].items() if len(a) > d}
    st["m0"] = m_new


# --- чтение файлов --------------------------------------------------------------------------------------------------------

class TradeReader(arb_daemon.Reader):
    """Один открытый файл trade<N>: тот же распаковщик с памятью, что у книг, но разбор записей t."""
    consume = staticmethod(parse_trades.consume)

    def read_stream(self, keep_from, sink):
        """Дочитывает файл, отдавая sink(arrs) после каждого куска (память не растёт с размером файла). -> число записей."""
        self.prime()
        closed = os.path.exists(self.path + ".ok")
        kept = 0

        def chunk(out):
            arrs = {}
            n = self._feed(out, AllSyms(), keep_from, arrs)
            if n:
                sink(arrs)
            return n
        kept += chunk(b"")
        while True:
            data = self.f.read(arb_daemon.CHUNK)
            if not data:
                break
            kept += chunk(self.dobj.decompress(data))
        self.st["done"] = closed
        if closed:
            self.close()
        return kept


def file_key(ex, day, path):
    return f"{ex}-{parse_inc.tag_of(day, path)}"


def discover(days, t_lo_ms, t_hi_ms):
    """Файлы сделок запусков с метки MINRUN, которые могут держать записи окна: запуск начался до t_hi, следующий запуск той же полосы — не раньше t_lo."""
    jobs = []
    for ex in EXCHANGES:
        for day in days:
            lanes = {}
            for f in glob.glob(f"{ROOTS[ex]}/{day}/trade*.tsv.zst"):
                m = TRADE.search(f)
                if m and int(m.group(2)) >= arb_live.MINRUN:
                    lanes.setdefault(m.group(1), []).append((int(m.group(2)), f))
            for fs in lanes.values():
                fs.sort()
                for i, (stamp, f) in enumerate(fs):
                    nxt = fs[i + 1][0] if i + 1 < len(fs) else 1 << 60
                    if stamp * 1000 < t_hi_ms and nxt * 1000 >= t_lo_ms:
                        jobs.append((ex, day, f))
    return jobs


def boot_file(job):
    """Bootstrap одного файла с начала: минутные ряды по символам и позиция чтения. -> (ключ, состояние файла, {символ: (минута_0, ряд)})."""
    ex, day, path, keep_from, unit, m0 = job
    st = {"off": 0, "cum": 0, "last": {}, "done": False}
    bins = {}
    r = TradeReader(path, st)
    r.read_stream(keep_from, lambda arrs: ingest(bins, m0, ex, arrs, unit))
    r.close()
    return file_key(ex, day, path), st, bins


def bootstrap(live, t_b, par=3):
    """Полный разбор файлов окна пулом процессов; позиции чтения сохраняются — служба дочитывает дальше с них."""
    t0 = time.time()
    lo = max(arb_live.START_MS, t_b - WINDOW_MS)
    m0 = lo // MIN - KEEP_MIN
    days = arb_live.days_between(lo - 90 * MIN, t_b + 2 * MIN)
    un = units()
    jobs = [(ex, day, f, lo - KEEP_MIN * MIN, un[ex], m0) for ex, day, f in discover(days, lo - KEEP_MIN * MIN, t_b + MARGIN_MS)]
    jobs.sort(key=lambda j: -os.path.getsize(j[2]))
    S = new_state(m0)
    with Pool(par) as p:
        res = p.imap_unordered(boot_file, jobs, chunksize=1) if par > 1 else map(boot_file, jobs)
        for key, st, bins in res:
            S["parse"][key] = {"off": st["off"], "cum": st["cum"], "last": {}, "done": st["done"]}
            for k, a in bins.items():
                t = S["bins"].get(k)
                if t is None:
                    S["bins"][k] = a
                else:
                    if len(t) < len(a):
                        t = S["bins"][k] = np.concatenate([t, np.zeros(len(a) - len(t))])
                    t[:len(a)] += a
    save_state(live, S)
    log(event="bootstrap", files=len(jobs), keys=len(S["bins"]), seconds=round(time.time() - t0, 1))
    return S


def save_state(live, S):
    p = Path(live, "turnover-state.pkl")
    tmp = p.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(S, f, protocol=4)
    os.replace(tmp, p)


def load_state(live):
    p = Path(live, "turnover-state.pkl")
    if not p.exists():
        return None
    with open(p, "rb") as f:
        S = pickle.load(f)
    return S if isinstance(S, dict) and S.get("version") == VERSION else None


# --- служба --------------------------------------------------------------------------------------------------------------

class Service:
    def __init__(self, live, now_ms=None, par=3):
        self.live, self.par = live, par
        now_ms = now_ms or int(time.time() * 1000)
        self.S = load_state(live) or bootstrap(live, (now_ms - MARGIN_MS) // MIN * MIN, par)
        self.units = units()
        self.day, self.coins = None, {}
        self.readers = {}

    def refresh_maps(self, now_ms):
        day = datetime.fromtimestamp(now_ms / 1000, timezone.utc).date().isoformat()
        if day != self.day:
            _, self.coins = arb_daemon.coin_map(day)
            self.day = day
            log(event="coins", day=day, coins=len(self.coins))

    def open_readers(self, lo, hi, now_ms):
        days = arb_live.days_between(lo - 90 * MIN, now_ms)
        new, seen = [], set()
        for ex, day, path in discover(days, lo - KEEP_MIN * MIN, hi + MARGIN_MS):
            key = file_key(ex, day, path)
            seen.add(key)
            st = self.S["parse"].setdefault(key, {"off": 0, "cum": 0, "last": {}, "done": False})
            if st["done"] or key in self.readers:
                continue
            self.readers[key] = TradeReader(path, st)
            new.append(self.readers[key])
        for key in [k for k, r in self.readers.items() if r.st["done"]]:
            del self.readers[key]
        for key in [k for k, st in self.S["parse"].items() if st["done"] and k not in seen]:
            del self.S["parse"][key]
        return new

    def read_all(self, lo, hi, now_ms):
        new = self.open_readers(lo, hi, now_ms)
        with ThreadPoolExecutor(3) as pool:                       # после перезапуска: распаковка с начала файлов параллельно
            list(pool.map(lambda r: r.prime(), [r for r in new if r.st["off"]]))
        kept = 0
        keep_from = lo - KEEP_MIN * MIN
        for key, r in list(self.readers.items()):
            ex = key.split("-", 1)[0]
            kept += r.read_stream(keep_from, lambda arrs, ex=ex: ingest(self.S["bins"], self.S["m0"], ex, arrs, self.units[ex]))
        return kept

    def result(self, t0, t1):
        lo, hi = t0 // MIN, t1 // MIN
        out = {}
        for coin, per in self.coins.items():
            row = {ex: round(window_sum(self.S["bins"], self.S["m0"], (ex, sym), lo, hi)) for ex, (sym, _, _) in per.items()}
            out[coin] = row
        return out

    def tick(self, now=None):
        t_start = time.time()
        now = now or int(t_start * 1000)
        if os.path.exists(arb_live.HOLD):
            log(skipped=f"есть {arb_live.HOLD} (замер производительности)")
            return
        t1 = (now - MARGIN_MS) // MIN * MIN
        if self.S["t_a"] is not None and t1 <= self.S["t_a"]:
            return
        t0 = max(arb_live.START_MS, t1 - WINDOW_MS)
        self.refresh_maps(now)
        kept = self.read_all(t0, t1, now)
        coin = self.result(t0, t1)
        doc = {"t0": t0, "t1": t1, "written": int(time.time() * 1000), "coin": coin}
        tmp = Path(self.live, "turnover.json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, Path(self.live, "turnover.json"))
        cut(self.S, t0 // MIN - KEEP_MIN)
        self.S["t_a"] = t1
        save_state(self.live, self.S)
        log(event="pass", t1=t1, kept=kept, coins=len(coin), open_files=len(self.readers), total_s=round(time.time() - t_start, 1), rss_mb=arb_daemon.rss_mb())

    def run(self):
        while True:
            self.tick()
            now = time.time()
            time.sleep(max(1.0, (now // PERIOD_S + 1) * PERIOD_S + TICK_OFFSET_S - now))


def main(cmd, live, par=3):
    Path(live).mkdir(parents=True, exist_ok=True)
    if cmd == "boot":
        bootstrap(live, (int(time.time() * 1000) - MARGIN_MS) // MIN * MIN, par)
        return
    import fcntl   # только Linux: замок от двух служб
    lock = open(Path(live, "turnover.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log(error="служба уже запущена")
        return
    svc = Service(live, par=par)
    if cmd == "once":
        svc.tick()
    else:
        svc.run()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 3)
