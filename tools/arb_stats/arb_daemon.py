"""Живой счёт вкладки «Арбитраж»: служба, один проход раз в 5 минут (юнит arb-live.service; прежний почасовой arb_live.py — запасной).

Что делает проход (≈ 10–15 с процессора вместо 13 мин):
  1. читает только новые байты открытых файлов book<N> — распаковщик каждого файла живёт между проходами (файл — один кадр zstd, окно
     512 КиБ: с середины его не распаковать, поэтому состояние распаковщика держится в памяти; после перезапуска служба прочитывает файл с начала,
     выбрасывая уже учтённое — тот же `off`, что в parse_inc);
  2. дописывает новые минуты к состоянию каждой пары (arb_inc.advance_pair): граница прохода t_b = минута до «сейчас − 60 с»;
  3. пишет arb.json (`written`, `period` — по ним страница опрашивает раз в период) и chart-<gen>.jsonl;
  4. сохраняет состояние (state.pkl: пары, символы, позиции чтения) — перезапуск продолжает с того же места.
Первый запуск без state.pkl: bootstrap из кусков прежнего почасового разбора (<live>/parsed и <live>/state) — полный счёт одним проходом.
Запуск: python3 arb_daemon.py run <live> | bootstrap <live> [PAR] | once <live> | rebuild <пустой каталог> [PAR]. Файл /data/perf/HOLD — проход пропускается (запись в skips.log).
rebuild — состояние с нуля по сырым файлам (этап A по всем биржам, затем bootstrap): так подключается новая биржа (Gate — пятая), чьих символов в прежних кусках нет;
в боевой каталог не пишется (должен быть пустым) — готовый state.pkl кладётся на место при остановленной службе, служба догоняет с него.
Нужен пакет zstandard (на srv — /opt/arb-tab/pylib, PYTHONPATH в юните).
"""
import glob
import io
import json
import os
import pickle
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import arb_inc  # noqa: E402
import arb_live  # noqa: E402
import parse_inc  # noqa: E402
from common import EXCHANGES, load_pool, norm, unit_of  # noqa: E402,F401

PERIOD_S = 300               # период проходов
TICK_OFFSET_S = 15           # проход через 15 с после границы периода: сборщики успевают дописать минуту
MARGIN_MS = 60_000           # запас: записи с лагом больше 60 с отбрасываются (в данных лаг ≤ 28 с)
MIN = 60_000
CHUNK = 8 << 20              # читаем сжатый файл кусками по 8 МБ: память не растёт при долгом простое
START_MS = arb_live.START_MS
HOLD = arb_live.HOLD


def log(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)


# --- чтение файлов -------------------------------------------------------------------------------------------------

class Reader:
    """Один открытый файл book<N>: распаковщик и позиция живут между проходами. Состояние st — как в parse_inc (off, cum, last, done)."""

    consume = staticmethod(parse_inc.consume)     # разбор записей: книги; TradeReader (arb_turnover.py) подставляет разбор сделок

    def __init__(self, path, st):
        self.path, self.st = path, st
        self.f = self.dobj = None
        self.skip = st["off"]       # сколько распакованных байт выбросить после (пере)открытия
        self.buf = b""              # распакованное, ещё не разобранное (хвост без перевода строки)

    def _open(self):
        import zstandard
        self.f = open(self.path, "rb")
        self.dobj = zstandard.ZstdDecompressor().decompressobj(read_across_frames=True)
        self.skip, self.buf = self.st["off"], b""

    def prime(self):
        """После перезапуска: читает файл с начала, выбрасывая учтённое (до off). Распаковка не держит GIL — файлы греются в потоках."""
        if self.dobj is None:
            self._open()
        while self.skip:
            data = self.f.read(CHUNK)
            if not data:
                return
            out = self.dobj.decompress(data)
            if len(out) <= self.skip:
                self.skip -= len(out)
                continue
            self.buf, self.skip = out[self.skip:], 0

    def _feed(self, out, want, keep_from, arrs):
        blob = self.buf + out
        end = blob.rfind(b"\n") + 1
        self.buf = blob[end:]
        if not end:
            return 0
        return self.consume(io.BytesIO(blob[:end]), self.st, want, keep_from, arrs)[0]

    def read(self, want, keep_from, arrs):
        """Дочитывает файл: записи — в arrs {символ (bytes): массивы}. -> число записей."""
        self.prime()
        closed = os.path.exists(self.path + ".ok")     # файл закрыт сборщиком до чтения — после дочитывания он закончен
        kept = self._feed(b"", want, keep_from, arrs)
        while True:
            data = self.f.read(CHUNK)
            if not data:
                break
            kept += self._feed(self.dobj.decompress(data), want, keep_from, arrs)
        self.st["done"] = closed
        if closed:
            self.close()
        return kept

    def close(self):
        if self.f:
            self.f.close()
        self.f = self.dobj = None
        self.buf = b""
        self.st["last"] = {}


# --- карта символов ------------------------------------------------------------------------------------------------

def coin_map(day):
    """-> ({(биржа, символ): (монета, множитель названия, единица размера)}, {монета: {биржа: (символ, множитель, точное_имя)}}) — как analyze.compute."""
    symmap, d = arb_live.symmap_for(day)
    meta = {ex: load_pool(ex, d)[1] for ex in EXCHANGES}
    coins = {}
    for ex, m in symmap.items():
        for sym, (coin, mult) in m.items():
            cur = coins.setdefault(coin, {}).get(ex)
            exact = norm(ex, sym)[0] == coin     # две записи одной монеты на бирже (MASK / NULLMASK): берётся та, чьё имя совпадает
            if cur is None or (exact and not cur[2]):
                coins[coin][ex] = (sym, mult, exact)
    cmap = {}
    for coin, per in coins.items():
        for ex, (sym, mult, _) in per.items():
            unit = unit_of(ex, meta[ex].get(sym))
            cmap[(ex, sym)] = (coin, mult, unit)
    return cmap, coins


def to_sym_arrays(a, mult, unit):
    """Записи этапа A (dict массивов e, rx, bid, ask, bsz, asz) -> аргументы Sym.ingest: глубина считается до деления цены, как в analyze.load."""
    bid, ask = np.asarray(a["bid"]), np.asarray(a["ask"])
    dep = np.minimum(np.asarray(a["bsz"]) * bid, np.asarray(a["asz"]) * ask) * unit
    return np.array(a["e"], np.int64), np.array(a["rx"], np.int64), bid / mult, ask / mult, dep


# --- мир: всё состояние службы ---------------------------------------------------------------------------------------

VERSION = 2                  # формат состояния: 2 — плотные минутные ряды пар и живые минуты бирж (график «как было»)


def new_world():
    """t_a — граница последнего прохода (мс), None — прохода ещё не было; syms — (биржа, символ) -> Sym; pairs — (монета, A, B) -> Pair;
    parse — ключ файла -> {off, cum, last, done}; alive — живые минуты бирж; passes — число проходов."""
    return SimpleNamespace(version=VERSION, t_a=None, syms={}, pairs={}, parse={}, alive=arb_inc.Alive((START_MS - arb_live.WARM_MS) // MIN - 5), passes=0)


def save_world(live, W):
    p = Path(live, "state.pkl")
    tmp = p.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(W, f, protocol=4)
    os.replace(tmp, p)


def load_world(live):
    p = Path(live, "state.pkl")
    if not p.exists():
        return None
    with open(p, "rb") as f:
        W = pickle.load(f)
    return W if getattr(W, "version", 0) == VERSION else None       # состояние старого формата — не продолжать (bootstrap заново)


def file_key(ex, day, path):
    return f"{ex}-{parse_inc.tag_of(day, path)}"


def ready(W, ex, sym):
    s = W.syms.get((ex, sym))
    return s is not None and s.n >= arb_inc.NEED_EVENTS and s.mid is not None


def pair_ok(sa, sb):
    return 0.9 < sa.mid / sb.mid < 1.1      # цены одной монеты на двух биржах — одного порядка (иначе разный множитель названия)


def step_coin(W, coin, per, carry0, batch, k_to):
    """Дописывает проход (минуты до k_to) ко всем парам монеты (по биржам из per, порядок EXCHANGES). Существующие пары дописываются
    каждый проход, даже без новых записей: минуты без записей получают перенесённое состояние."""
    exs = [ex for ex in EXCHANGES if ex in per and ready(W, ex, per[ex][0])]
    for i in range(len(exs)):
        for j in range(i + 1, len(exs)):
            ea, eb = exs[i], exs[j]
            ka, kb = (ea, per[ea][0]), (eb, per[eb][0])
            key = (coin, ea, eb)
            if key not in W.pairs and batch.get(ka) is None and batch.get(kb) is None:
                continue
            if not pair_ok(W.syms[ka], W.syms[kb]):
                continue
            A, B = arb_inc.with_carry(carry0.get(ka), batch.get(ka)), arb_inc.with_carry(carry0.get(kb), batch.get(kb))
            ps = W.pairs.get(key)
            if ps is None:
                ps = W.pairs[key] = arb_inc.Pair()
            arb_inc.advance_pair(ps, A, B, START_MS, k_to)


def advance(W, coins, t_b):
    """Один проход: все символы отдают записи до t_b, пары дописываются. -> число символов с новыми записями."""
    carry0, batch, nb, alive = {}, {}, 0, {}
    for key, s in W.syms.items():
        carry0[key] = s.carry
        b = s.take(W.t_a if W.t_a is not None else t_b - 10 ** 12, t_b)
        batch[key] = b
        if b is not None:
            nb += 1
            alive.setdefault(key[0], []).append(np.unique(b["e"] // MIN))
            if s.mid is None:
                s.mid = float(np.median((b["bid"] + b["ask"]) / 2))
    for ex, ms in alive.items():
        W.alive.mark(ex, np.concatenate(ms))
    W.alive.extend(t_b // MIN)
    for coin, per in coins.items():
        step_coin(W, coin, per, carry0, batch, t_b // MIN)
    W.t_a = t_b
    return nb


def results(W, t_b):
    t0 = max(START_MS, t_b - arb_live.WINDOW_MS)
    det = {}
    for key, ps in W.pairs.items():
        arb_inc.prune(ps, t_b)
        if ps.m0 is None:
            continue
        r = arb_inc.result(ps, t0, t_b, arb_live.PERIODS_H, W.alive.ok(key[1], key[2], ps.m0, len(ps.mid)))
        if r:
            det[key] = r
    W.alive.prune((t_b - arb_live.WINDOW_MS) // MIN - 2)
    return t0, det


# --- bootstrap из кусков прежнего почасового разбора ---------------------------------------------------------------

def boot_coin(arg):
    coin, per, cmap, parsed, t_boot = arg
    syms, batch, alive = {}, {}, {}
    for ex, (sym, mult, _) in per.items():
        fs = sorted(glob.glob(str(Path(parsed, ex, sym + ".*.npz"))))
        if not fs:
            continue
        parts = [dict(np.load(f)) for f in fs]
        a = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
        s = arb_inc.Sym()
        s.ingest(*to_sym_arrays(a, mult, cmap[(ex, sym)][2]))
        syms[(ex, sym)] = s
    W = new_world()
    W.syms = syms
    carry0 = {k: None for k in syms}
    for k, s in syms.items():
        b = s.take(t_boot - 10 ** 12, t_boot)
        batch[k] = b
        if b is not None:
            s.mid = float(np.median((b["bid"] + b["ask"]) / 2))
            alive.setdefault(k[0], []).append(np.unique(b["e"] // MIN))
    step_coin(W, coin, per, carry0, batch, t_boot // MIN)
    return syms, W.pairs, {ex: np.unique(np.concatenate(ms)) for ex, ms in alive.items()}


def bootstrap(live, par=3, now_ms=None):
    """Полный счёт одним проходом по кускам <live>/parsed до границы t_boot; позиции чтения — из <live>/state (прежние файлы состояния)."""
    t_start = time.time()
    now_ms = now_ms or int(t_start * 1000)
    W = new_world()
    rx_end = []
    for sp in sorted(Path(live, "state").glob("*.pkl")):
        with open(sp, "rb") as f:
            st = pickle.load(f)
        W.parse[sp.stem] = {"off": st["off"], "cum": st["cum"], "last": {} if st["done"] else st["last"], "done": st["done"]}
        if not st["done"]:
            rx_end.append(st["cum"] // 1_000_000)
    t_boot = (min(rx_end or [now_ms]) - MARGIN_MS) // MIN * MIN
    day = datetime.fromtimestamp(now_ms / 1000, timezone.utc).date().isoformat()
    cmap, coins = coin_map(day)
    args = [(c, per, cmap, str(Path(live, "parsed")), t_boot) for c, per in sorted(coins.items())]
    if par <= 1:
        res = list(map(boot_coin, args))
    else:
        with Pool(par) as p:
            res = p.map(boot_coin, args, chunksize=4)
    for syms, pairs, alive in res:
        W.syms.update(syms)
        W.pairs.update({k: v for k, v in pairs.items() if v.m0 is not None})
        for ex, ms in alive.items():
            W.alive.mark(ex, ms)
    W.alive.extend(t_boot // MIN)
    W.t_a = t_boot
    save_world(live, W)
    log(event="bootstrap", t_boot=t_boot, files=len(W.parse), symbols=len(W.syms), pairs=len(W.pairs), seconds=round(time.time() - t_start, 1))
    return W


# --- проход и служба --------------------------------------------------------------------------------------------------

def rss_mb():
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


class Service:
    def __init__(self, live, now_ms=None):
        self.live = live
        self.W = load_world(live) or bootstrap(live, now_ms=now_ms)
        self.day = None
        self.cmap, self.coins = {}, {}
        self.readers = {}
        self.prime_started = False

    def refresh_maps(self, now_ms):
        day = datetime.fromtimestamp(now_ms / 1000, timezone.utc).date().isoformat()
        if day != self.day:
            self.cmap, self.coins = coin_map(day)
            self.day = day
            log(event="coins", day=day, symbols=len(self.cmap), coins=len(self.coins))

    def open_readers(self, now_ms):
        """Читатели открытых файлов: новые файлы — с нуля, известные — с сохранённой позиции."""
        days = arb_live.days_between(now_ms - 36 * 3600_000, now_ms)
        new = []
        for job in arb_live.discover(days):
            ex, day, path, _ = job
            key = file_key(ex, day, path)
            st = self.W.parse.setdefault(key, {"off": 0, "cum": 0, "last": {}, "done": False})
            if st["done"] or key in self.readers:
                continue
            self.readers[key] = Reader(path, st)
            new.append(self.readers[key])
        for key in [k for k, r in self.readers.items() if r.st["done"]]:
            del self.readers[key]
        return new

    def read_all(self, now_ms):
        new = self.open_readers(now_ms)
        with ThreadPoolExecutor(3) as ex:                         # после перезапуска: распаковка с начала файлов параллельно
            list(ex.map(lambda r: r.prime(), [r for r in new if r.st["off"]]))
        want = {e: {s.encode(): s for (x, s) in self.cmap if x == e} for e in EXCHANGES}
        keep_from = START_MS - arb_live.WARM_MS
        kept = 0
        for key, r in list(self.readers.items()):
            ex = key.split("-", 1)[0]
            arrs = {}
            kept += r.read(want[ex], keep_from, arrs)
            for sb, a in arrs.items():
                name = want[ex][sb]
                coin, mult, unit = self.cmap[(ex, name)]
                s = self.W.syms.setdefault((ex, name), arb_inc.Sym())
                s.ingest(*to_sym_arrays({"e": np.frombuffer(a[0], "i8"), "rx": np.frombuffer(a[1], "i8"), "bid": np.frombuffer(a[3], "f8"),
                                         "ask": np.frombuffer(a[4], "f8"), "bsz": np.frombuffer(a[5], "f8"), "asz": np.frombuffer(a[6], "f8")}, mult, unit))
        return kept

    def tick(self, now=None):
        t0 = time.time()
        cpu0 = time.process_time()
        now = now or int(t0 * 1000)
        if os.path.exists(HOLD):
            msg = {"skipped": f"есть {HOLD} (замер производительности)", "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            log(**msg)
            with open(Path(self.live, "skips.log"), "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            return
        self.refresh_maps(now)
        kept = self.read_all(now)
        t_read = time.time()
        t_b = (now - MARGIN_MS) // MIN * MIN
        if self.W.t_a is not None and t_b <= self.W.t_a:
            return
        nb = advance(self.W, self.coins, t_b)
        t_adv = time.time()
        t0w, det = results(self.W, t_b)
        n = arb_live.write_outputs(self.live, now, t0w, t_b, det, PERIOD_S)
        t_out = time.time()
        self.W.passes += 1
        save_world(self.live, self.W)
        late = sum(s.late for s in self.W.syms.values())
        log(event="pass", t1=t_b, kept=kept, symbols_new=nb, pairs=n, late_total=late, read_s=round(t_read - t0, 1), advance_s=round(t_adv - t_read, 1),
            out_s=round(t_out - t_adv, 1), save_s=round(time.time() - t_out, 1), total_s=round(time.time() - t0, 1),
            cpu_s=round(time.process_time() - cpu0, 1), rss_mb=rss_mb(), open_files=len(self.readers))

    def run(self):
        while True:
            self.tick()
            now = time.time()
            nxt = (now // PERIOD_S + 1) * PERIOD_S + TICK_OFFSET_S
            time.sleep(max(1.0, nxt - now))


def main(cmd, live, par=3):
    Path(live).mkdir(parents=True, exist_ok=True)
    if cmd == "bootstrap":
        bootstrap(live, par)
        return
    if cmd == "rebuild":
        if any(Path(live, n).exists() for n in ("state.pkl", "state", "parsed")):
            log(error="rebuild: каталог не пустой (есть state.pkl / state / parsed) — боевое состояние не трогаем")
            sys.exit(2)
        files, kept = arb_live.parse_all(live, int(time.time() * 1000), par)
        log(event="parse", files=files, kept=kept)
        bootstrap(live, par)
        return
    import fcntl   # только Linux: замок от двух служб
    lock = open(Path(live, "daemon.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log(error="служба уже запущена")
        return
    svc = Service(live)
    if cmd == "once":
        svc.tick()
    else:
        svc.run()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 3)
