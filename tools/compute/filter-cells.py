#!/usr/bin/env python3
"""TK-216: раннер «фильтр-клетки» (вердикт Судьи TK-214: путь годен при `--driver setups` + `--busy-skip off`).

Клетка = форма пула + фильтр сигналов. Круг сигнала от других сигналов набора не зависит, поэтому строки пула формы
(`signals.csv` прогона `bounce-grid --busy-skip off --signal approach` по набору без режимного/R1-фильтра) режутся
по колонкам признаков кэша подходов `approaches-<SYM>.csv` -> `busy-replay.py --keep` -> `portfolio-sim.py` — без движка.
Ключ строки: (symbol, form, t0_ns = arm_ms × 1e6, price_tick) — на одном t0 бывает два подхода.

    filter-cells.py --pool-tree <месяц> --pool-set pool --pool-base age=2700,side=bid --approaches <кэш подходов; {day} в пути — подстановка суток>
        --cells <клетки.tsv: имя<TAB>форма[,форма…]<TAB>фильтр> --out <каталог> [--regime-from <dir>] [--tick-lot <csv>]
        [--psim-deposit 2500 --psim-position 500 --psim-max-pos 0] [--tools <каталог busy-replay/portfolio-sim>]

`--pool-tree` — каталог месяца `<сутки>/<набор>/{rounds,signals}.csv` (как читает busy-replay). Фильтр — синтаксис
`--set` bounce-grid. Годны только ключи фильтра сигнала: side, age, agemax, flow, usd_min, behind_min, stack_min,
frontrun_min, r1_<колонка>_min|max, оси pool*/btc*. Всё, что меняет план (вход/выход/стоп/тейк/дедлайн/размер/σ,
очередь/RTT), и ключи, не определённые у подхода (eaten*, frontrun, ret*), — отказ. Клетка не строже базы пула
(age/side/…) — отказ: пул её не покрывает.
"""
import argparse
import csv
import hashlib
import os
import re
import subprocess
import sys

PLAN_KEYS = {"entry", "entry_form", "exit", "exit_form", "stop", "take", "deadline", "ttl", "entry_ttl", "qty", "usd",
             "order_usd", "sigma", "queue", "rtt", "band", "band_exit", "post_only", "tape", "early"}
NOT_AT_APPROACH = {"eaten", "eaten_min", "frontrun"}
PLAIN = {"age", "agemax", "flow", "side", "usd_min", "behind_min", "stack_min", "frontrun_min"}
REGIME = {"pool1h": "pool_ret_1h_bps", "pool4h": "pool_ret_4h_bps", "btc1h": "btc_ret_1h_bps",
          "btc2h": "btc_ret_2h_bps", "btc3h": "btc_ret_3h_bps", "btc4h": "btc_ret_4h_bps",
          "btc6h": "btc_ret_6h_bps", "btc8h": "btc_ret_8h_bps", "btc12h": "btc_ret_12h_bps",
          "btc24h": "btc_ret_24h_bps"}
RET_AXES = {"ret10m", "ret1h", "ret4h"}


def parse_filter(spec):
    """Фильтр -> словарь {ключ: значение}; отказ (ValueError) на планоменяющем/неопределённом у подхода/неизвестном."""
    f = {}
    for kv in (s for s in spec.split(",") if s):
        k, _, v = kv.partition("=")
        if k in f:
            raise ValueError(f"ключ {k} повторяется")
        base = k.rsplit("_", 1)[0] if k.endswith(("_min", "_max")) else k
        if k in PLAN_KEYS or base in PLAN_KEYS:
            raise ValueError(f"{k}: меняет план — своя форма, свой пул (вердикт Судьи TK-214), раннер отказывает")
        if k in NOT_AT_APPROACH:
            raise ValueError(f"{k}: у подхода не определён (bounce-grid --signal approach отказывает так же)")
        if k in PLAIN:
            f[k] = v if k == "side" else float(v)
        elif k.startswith("r1_") and k.endswith(("_min", "_max")):
            f[k] = int(v)
        elif k.endswith(("_min", "_max")) and k.rsplit("_", 1)[0] in REGIME:
            f[k] = float(v)
        elif k.endswith(("_min", "_max")) and k.rsplit("_", 1)[0] in RET_AXES:
            raise ValueError(f"{k}: ход монеты несёт только кэш касаний, у подхода колонок ret нет")
        else:
            raise ValueError(f"{k}: неизвестный ключ фильтра")
    if f.get("side") not in (None, "bid", "ask"):
        raise ValueError("side: bid|ask")
    return f


def covers(base, cell):
    """Пул (база) покрывает клетку: клетка не слабее базы по каждому ключу базы."""
    for k, bv in base.items():
        if k not in cell:
            return f"база пула задаёт {k}={bv}, клетка слабее — пул её не покрывает"
        cv = cell[k]
        if k == "side":
            ok = cv == bv
        elif k.endswith("_max") or k == "agemax":
            ok = cv <= bv
        else:  # age, *_min, flow, usd_min, behind_min, stack_min, frontrun_min
            ok = cv >= bv
        if not ok:
            return f"{k}={cv} слабее базы пула {k}={bv} — пул её не покрывает"
    return None


def pool_cfg(path):
    """Конфиг пула = первая строка `# lob bounce-grid: …` signals.csv (RTT, очередь, order-usd, σ, формы, фильтр) без root/days."""
    with open(path, encoding="utf-8") as fh:
        head = fh.readline()
    if not head.startswith("#"):
        sys.exit(f"{path}: нет строки конфига `# lob bounce-grid` — конфиг-хеш пула не вычислить")
    return re.sub(r"(root|days)=\S+", "", head).strip()


def rd(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(l for l in fh if not l.startswith("#")))


def maybe_int(s):
    return None if s in ("", None) else int(s)


APPROACH_COLS = ("arm_ms", "price_tick", "age_ms", "size_at_arm", "side", "depth_behind_lots", "stack_levels_at_arm",
                 "flow_1h_lots", "frontrun_lots_at_arm")


class Cache:
    """Кэш подходов: (symbol, arm_ms, price_tick) -> нужные колонки строки; режим суток по минутам.
    Память — O(строк пула суток): файл подходов читается потоком, остаются только ключи пула этих суток (`need`),
    `drop_day` выгружает сутки."""

    def __init__(self, root, regime_root, tick_lot, cols):
        self.root, self.regime_root, self.tick_lot, self.cols = root, regime_root, tick_lot, cols
        self.apr, self.reg, self.need = {}, {}, {}

    def approach(self, sym, day, arm_ms, tick):
        if (sym, day) not in self.apr:
            d = {}
            want = self.need.get((sym, day), ())
            p = os.path.join(self.root.replace("{day}", day), day, f"approaches-{sym}.csv")
            if not os.path.exists(p):
                sys.exit(f"нет кэша подходов {p}")
            with open(p, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(l for l in fh if not l.startswith("#")):
                    k = (int(r["arm_ms"]), int(r["price_tick"]))
                    if k in want:
                        d[k] = {c: r[c] for c in self.cols if c in r}
            self.apr[sym, day] = d
        return self.apr[sym, day].get((arm_ms, tick))

    def drop_day(self):
        self.apr.clear()
        self.reg.clear()

    def regime(self, day, minute, col):
        if day not in self.reg:
            if not self.regime_root:
                sys.exit("ключ режима без --regime-from")
            self.reg[day] = {int(r["minute_ms"]): r for r in rd(os.path.join(self.regime_root, f"{day}.csv"))}
        r = self.reg[day].get(minute)
        if r is None:
            return None
        if col not in r:
            sys.exit(f"режим {day}: нет колонки {col}")
        return None if r[col] == "" else float(r[col])


def admits(f, a, day, arm_ms, cache, sym):
    """Те же условия и порядок, что `TouchFilter::admits_row` (src/commands/lob/bounce_grid/sets.rs) для подхода."""
    age_ms = int(a["age_ms"])
    size = int(a["size_at_arm"])
    if "frontrun_min" in f and int(a["frontrun_lots_at_arm"]) < f["frontrun_min"]:
        return False
    if "age" in f and age_ms < int(f["age"]) * 1000:
        return False
    if "agemax" in f and age_ms >= int(f["agemax"]) * 1000:
        return False
    if "flow" in f:
        flow = int(a["flow_1h_lots"])
        if not (flow > 0 and size / flow * 100.0 >= f["flow"]):
            return False
    if "side" in f and a["side"].lower() != f["side"]:
        return False
    if "usd_min" in f:
        tick, lot = cache.tick_lot[sym]
        if int(a["price_tick"]) * tick * size * lot < f["usd_min"]:
            return False
    if "behind_min" in f and int(a["depth_behind_lots"]) * 100 < int(f["behind_min"]) * size:
        return False
    if "stack_min" in f and int(a["stack_levels_at_arm"]) < f["stack_min"]:
        return False
    for k, bound in f.items():
        if k.startswith("r1_"):
            name, which = k[3:].rsplit("_", 1)
            raw = a.get(name)
            if raw in (None, ""):  # нет записи R1 или R1_UNDEF — порогу не удовлетворяет
                return False
            v = int(raw)
            if (which == "min" and v < bound) or (which == "max" and v > bound):
                return False
    minute = arm_ms - arm_ms % 60000
    for k, bound in f.items():
        if k.endswith(("_min", "_max")) and k.rsplit("_", 1)[0] in REGIME:
            v = cache.regime(day, minute, REGIME[k.rsplit("_", 1)[0]])
            if v is None or (k.endswith("_min") and v < bound) or (k.endswith("_max") and v > bound):
                return False
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool-tree", required=True)
    ap.add_argument("--pool-set", default="pool")
    ap.add_argument("--pool-base", default="", help="фильтр, с которым считан пул (age/side как у базы)")
    ap.add_argument("--approaches", required=True)
    ap.add_argument("--regime-from")
    ap.add_argument("--pool-cfg-hash", help="ожидаемый конфиг-хеш пула (см. pool-cfg.txt); не сошёлся — отказ")
    ap.add_argument("--tick-lot", help="csv symbol,tick,lot — только для usd_min")
    ap.add_argument("--cells", required=True, help="TSV: имя<TAB>форма<TAB>фильтр (форма — из пула)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--tools", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--psim-deposit", default="2500")
    ap.add_argument("--psim-position", default="500")
    ap.add_argument("--psim-max-pos", default="0")
    ap.add_argument("--no-psim", action="store_true")
    a = ap.parse_args()

    base = parse_filter(a.pool_base)
    tick_lot = {}
    if a.tick_lot:
        tick_lot = {r["symbol"]: (float(r["tick"]), float(r["lot"])) for r in rd(a.tick_lot)}

    days = sorted(d for d in os.listdir(a.pool_tree) if os.path.isdir(os.path.join(a.pool_tree, d, a.pool_set)))
    pool = {}  # день -> строки signals.csv пула
    for d in days:
        pool[d] = rd(os.path.join(a.pool_tree, d, a.pool_set, "signals.csv"))
    forms = {r["form"] for rows in pool.values() for r in rows}
    cfgs = {d: pool_cfg(os.path.join(a.pool_tree, d, a.pool_set, "signals.csv")) for d in days}
    if len(set(cfgs.values())) != 1:
        sys.exit("ОТКАЗ: сутки пула считаны с разными конфигами: " + ", ".join(sorted(f"{d}={hashlib.sha256(c.encode()).hexdigest()[:8]}" for d, c in cfgs.items())))
    cfg_hash = hashlib.sha256(next(iter(cfgs.values())).encode()).hexdigest()[:16]
    if a.pool_cfg_hash and a.pool_cfg_hash != cfg_hash:
        sys.exit(f"ОТКАЗ: конфиг-хеш пула {cfg_hash} ≠ ожидаемого {a.pool_cfg_hash}")
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "pool-cfg.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"{cfg_hash}\n{next(iter(cfgs.values()))}\n")
    print(f"конфиг-хеш пула {cfg_hash}", flush=True)

    cells = []
    for line in open(a.cells, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        name, form, spec = (line.split("\t") + ["", ""])[:3]
        try:
            f = parse_filter(spec)
            for fm in form.split(","):
                if fm not in forms:
                    raise ValueError(f"форма {fm!r} не из пула (формы пула: {sorted(forms)}) — своя форма, свой пул")
            if (why := covers(base, f)):
                raise ValueError(why)
            if "usd_min" in f and not tick_lot:
                raise ValueError("usd_min: нужен --tick-lot (шаг цены и лот символа)")
        except ValueError as e:
            sys.exit(f"клетка {name}: ОТКАЗ — {e}")
        cells.append((name, form, f))

    r1cols = {k[3:].rsplit("_", 1)[0] for _, _, f in cells for k in f if k.startswith("r1_")}
    cache = Cache(a.approaches, a.regime_from, tick_lot, APPROACH_COLS + tuple(sorted(r1cols)))
    for d in days:  # ключи пула суток: подход читается потоком, держим только их
        for r in pool[d]:
            cache.need.setdefault((r["symbol"], r.get("day_utc") or d), set()).add((int(r["t0_ns"]) // 1_000_000, int(r["price_tick"])))

    mo = os.path.basename(os.path.normpath(a.pool_tree))
    odirs, fhs, stat = {}, {}, {}
    for name, form, f in cells:
        odirs[name] = os.path.join(a.out, name)
        os.makedirs(odirs[name], exist_ok=True)
        fhs[name] = open(os.path.join(odirs[name], "keep.csv"), "w", newline="")
        csv.writer(fhs[name]).writerow(["symbol", "t0_ns", "price_tick", "form"])
        stat[name] = [0, 0, 0]  # пул, остаётся, без подхода
    for d in days:  # сутки снаружи, клетки внутри: кэш одних суток в памяти
        for name, form, f in cells:
            w = csv.writer(fhs[name])
            st = stat[name]
            for r in pool[d]:
                if r["form"] not in form.split(","):
                    continue
                st[0] += 1
                arm_ms, tick = int(r["t0_ns"]) // 1_000_000, int(r["price_tick"])
                ap_row = cache.approach(r["symbol"], r.get("day_utc") or d, arm_ms, tick)
                if ap_row is None:
                    st[2] += 1
                    continue
                if admits(f, ap_row, r.get("day_utc") or d, arm_ms, cache, r["symbol"]):
                    st[1] += 1
                    w.writerow([r["symbol"], r["t0_ns"], r["price_tick"], r["form"]])
        cache.drop_day()
    for fh in fhs.values():
        fh.close()
    summary = []
    for name, form, f in cells:
        odir = odirs[name]
        keep = os.path.join(odir, "keep.csv")
        n_pool, n_keep, n_miss = stat[name]
        if n_miss:
            sys.exit(f"клетка {name}: {n_miss} строк пула без подхода в кэше (symbol,t0,price_tick) — ключ не сходится")
        if n_keep == 0:  # пустая клетка: прямой счёт тоже пуст (0 сигналов) — closes нет
            summary.append((name, form, n_pool, 0, ""))
            print(f"{name}\t{form}\tпул {n_pool}\tостаётся 0\t(пусто)", flush=True)
            continue
        out_mo = os.path.join(odir, mo)
        subprocess.run([sys.executable, os.path.join(a.tools, "busy-replay.py"), a.pool_tree, out_mo, "--keep", keep,
                        "--sets", a.pool_set], check=True, capture_output=True, text=True)
        closes = ""
        if not a.no_psim:
            closes = os.path.join(odir, "closes.json")
            subprocess.run([sys.executable, os.path.join(a.tools, "portfolio-sim.py"), "--epoch", f"{mo}={odir}:{mo}",
                            *[x for fm in form.split(",") for x in ("--variant", f"{a.pool_set}@{fm}={a.pool_set}/{fm}")],
                            "--deposit-usd", a.psim_deposit,
                            "--position-usd", a.psim_position, "--max-pos", a.psim_max_pos, "--closes-out", closes],
                           check=True, capture_output=True, text=True)
        summary.append((name, form, n_pool, n_keep, closes))
        print(f"{name}\t{form}\tпул {n_pool}\tостаётся {n_keep}\t{closes}", flush=True)
    with open(os.path.join(a.out, "summary.tsv"), "w") as fh:
        fh.write("cell\tform\tpool_rows\tkept\tcloses\n")
        for row in summary:
            fh.write("\t".join(map(str, row)) + "\n")


if __name__ == "__main__":
    main()
