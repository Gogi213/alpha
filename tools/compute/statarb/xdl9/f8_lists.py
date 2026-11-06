#!/usr/bin/env python3
"""TK-493 S3 (П-37 r3.1 §11, §15): списки заданий и команды F8 для контролей и чувствительности (счёт НЕ запускается; торговое окно не читается).

Берёт группы запусков волн S1 (`waves_manifest.csv`, файлы `waves/w*/`) и строит единицы F8 (`f8_units.tsv`: id, stage, команда, glob выходов):
  main   - группы волн 1-3 (команда та же, что в манифесте S1; у D2 исправлен `--pairs`: в S1 стоял файл клеток, которого у D2 нет - pairs-файл группы)
  ap     - контроль §11.8 «все пары каталога без отбора» (волна 4 S1, задания пишет sel_allpairs_jobs.py на Deck)
  mkplac - сдвинутые панели и фандинг партнёра (f8_shift.py mkplac), один раз
  pl     - плацебо-2 (§11.2): те же пары и параметры клетки, партнёр сдвинут на +1 сутки (календарные - крипта, торговые дни - остальные) по всему окну
  rp     - случайные пары (§11.4): тот же класс и биржа, число равно числу заданий группы, seed 37; A1/A2 - обе ноги случайны из каталога, B1/C1/D2 - нога Y
           сохранена, партнёр X заменён случайным другим активом той же биржи и класса
  sm     - чувствительность min_obs = ceil(0.9 W) (§6.8 в), вне семьи (A1, A2, B1, C1; у D2 min_obs не используется)
  n17    - чувствительность «без 17 контрактов Binance TradFi» (§11.9): группы, где такие контракты есть, без заданий с ними, вне семьи
Режим `--mode form` строит те же команды на панелях формирования (~/xdl9/form, сутки 2468-2478) для сухого прогона; `--mode trade` - боевой F8.

  python f8_lists.py gen --mode trade --out <каталог списков> --units <f8_units.tsv>
  python f8_lists.py selftest
"""
import csv, math, os, random, re, sys
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
SELDOC = os.path.join(ROOT, "docs", "findings", "xdl9-selection-2026-11-06")
WAVES = os.path.join(SELDOC, "waves")
CONTRACTS = os.path.join(ROOT, "data", "xdl9", "sel", "contracts.csv")

BIN = "$HOME/xdl9/bin/statarb-xdl9-4ef45cc8"
SEED = 37
NL = chr(10)
HDR_CELLS = "cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh"
D2_WINDOW = 60
TAG = "@p1"
NO17 = ["ACN", "ASML", "BX", "CAT", "COST", "CSCO", "DELL", "EQT", "GEV", "JPM", "KODEX200", "MRVL", "MU", "NOK", "ORCL", "STRC", "TXN"]
NO17_SYMS = {b + "USDT" for b in NO17}

MODES = {
    "trade": dict(P="$HOME/xdl9/trade", F="$HOME/xdl9/funding", PL="$HOME/xdl9/plac", D0=2479, D1=2500, OUT="out", LST="f8", LO=2465, HI=2500),
    "form": dict(P="$HOME/xdl9/form", F="$HOME/xdl9/funding_form", PL="$HOME/xdl9/plac_form", D0=2468, D1=2479, OUT="$HOME/xdl9/sel/f8_dry/out", LST="f8form", LO=2465, HI=2479),
}


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_lines(path, lines):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=NL) as fh:
        fh.write(NL.join(lines) + NL)


def slug_of_cell(cell):
    return cell.split("|")[2]


def stem_of(cid, exy, exx):
    return "%s__%s__%s" % (cid.replace("|", "_").replace("~", "-"), exy, exx)


def load_groups():
    """Группы S1 -> список словарей; D2 дополняется k и hold_payments из команды манифеста."""
    out = []
    for r in read_csv(os.path.join(SELDOC, "waves_manifest.csv")):
        g = dict(wave=int(r["wave"]), kind=r["kind"], cell=r["cell"], exy=r["ex_y"], exx=r["ex_x"], hands=r["hands"], nj=int(r["jobs"]),
                 cells_file=r["cells_file"], jobs_file=r["jobs_file"], cmd=r["cmd"], slug=slug_of_cell(r["cell"]))
        d2 = g["kind"] == "D2"
        g["stem"] = os.path.basename(g["jobs_file"])[:-len(".pairs.csv" if d2 else ".jobs.csv")]
        assert g["stem"] == stem_of(g["cell"], g["exy"], g["exx"]) or g["kind"] == "C1", g["stem"]
        if d2:
            m = re.search(r"--funding-k (\d+) --hold-payments (\d+)", g["cmd"])
            g["k"], g["hold"] = m.group(1), m.group(2)
        out.append(g)
    return out


def read_jobs(path, header_cols):
    with open(path, encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        hdr = next(rd)
        assert hdr == header_cols, (path, hdr)
        return [row for row in rd]


# ------------------------------------------------------------------ команда запуска
def outs_main(O, st, jf):
    return ("--daily-out {O}/{st}.daily.csv --trades-out {O}/{st}.trades.csv --trades-ext --trade-jobs \"$(tj {jf})\" "
            "--open-out {O}/{st}.open.csv --mtm-open --cells-out {O}/{st}.cells_out.csv --ext-out {O}/{st}.ext.csv "
            "--job-day-out {O}/{st}.jobday.csv").format(O=O, st=st, jf=jf)


def outs_light(O, st):
    return ("--daily-out {O}/{st}.daily.csv --cells-out {O}/{st}.cells_out.csv --ext-out {O}/{st}.ext.csv "
            "--job-day-out {O}/{st}.jobday.csv").format(O=O, st=st)


def build_run(g, M, variant, st, cf, jf, O, P, F, PX=None, FX=None, d2dir=None, d2_gen=None):
    """Одна группа запусков. variant main - команда S1 (боевые выходы), иначе лёгкие выходы. P/F - каталоги панелей и фандинга ноги Y (и X в одной бирже);
    PX/FX - каталоги ноги X для cross/D2 (по умолчанию как у Y-биржи группы)."""
    exy, exx = g["exy"], g["exx"]
    flags = "--min-span 1440 --threads ${THREADS:-4}"
    outs = outs_main(O, st, jf) if variant == "main" else outs_light(O, st)
    kind = g["kind"]
    if kind in ("A1", "A2"):
        core = "--kind pair --panels %s --funding-y %s --rearm --cells %s" % (P, F, cf)
    elif kind == "B1":
        core = "--kind basket --panels %s --funding-y %s --rearm --cells %s" % (P, F, cf)
    elif kind == "C1":
        core = ("--kind cross --panels %s --panels-b %s --funding-y %s --funding-x %s --delay-x 1 --rearm --cells %s" % (P, PX, F, FX, cf))
    else:
        body = ("%s statarb run --kind funding-pp --panels %s --panels-b %s --funding-y %s/$S --funding-x %s/empty_x --rule funding "
                "--funding-k %s --hold-payments %s --cell \"%s|a0\" --window %d --min-obs %d --entry-z 1.00 --exit-z 0 --stop-z inf --max-hold 0 "
                "--refresh %d --jobs $J %s %s"
                % (BIN, P, PX, d2dir, d2dir, g["k"], g["hold"], g["cell"], D2_WINDOW, D2_WINDOW, D2_WINDOW, flags,
                   outs.replace("\"$(tj %s)\"" % jf, "\"$(tj $J)\"").replace(st + ".", st + "__$S.")))
        return "%sfor J in %s/shard*.jobs.csv; do S=$(basename $J .jobs.csv); [ $(wc -l < $J) -gt 1 ] || continue; %s; done" % (d2_gen, d2dir, body)
    return "%s statarb run %s --jobs %s %s %s" % (BIN, core, jf, outs, flags)


def d2_gen_cmd(pairs, fy, fx, d2dir, M, logf):
    return ("python3 $HOME/xdl9/tools/statarb/xdl9/d2_diff_funding.py --pairs %s --fund-y %s --fund-x %s --out %s --from-day %d --to-day %d >> %s && "
            % (pairs, fy, fx, d2dir, M["D0"], M["D1"], logf))


def out_dir(M, tag):
    return "%s/%s" % (M["OUT"], tag)


def unit_main(g, M):
    """Команда S1 (боевая) - строится тем же кодом, что sel_waves.build_cmd, для сверки с манифестом (тест)."""
    st, O = g["stem"], out_dir(M, "w%d" % g["wave"])
    P, F = "%s/%s" % (M["P"], g["exy"]), "%s/%s" % (M["F"], g["exy"])
    PX, FX = "%s/%s" % (M["P"], g["exx"]), "%s/%s" % (M["F"], g["exx"])
    if g["kind"] == "D2":
        d2dir = ("d2/" + st) if M is MODES["trade"] else ("$HOME/xdl9/sel/f8_dry/d2/" + st)
        gen = d2_gen_cmd(g["jobs_file"], F, FX, d2dir, M, O + "/d2_diff.log")
        return build_run(g, M, "main", st, g["cells_file"], g["jobs_file"], O, P, F, PX, FX, d2dir, gen), O + "/" + st + "* " + d2dir
    return build_run(g, M, "main", st, g["cells_file"], g["jobs_file"], O, P, F, PX, FX), O + "/" + st + ".*"


# ------------------------------------------------------------------ контракты, каталоги
def load_contracts():
    import sel_lib as S
    rows = read_csv(CONTRACTS)
    for r in rows:
        r["stem"] = S.safe(r["symbol"])
    return rows


class Catalog:
    def __init__(self):
        self.rows = load_contracts()
        self.by_ex_slug = {}
        self.asset = {}
        self.by_ex_slug_any = {}
        for r in self.rows:
            self.asset[(r["exchange"], r["stem"])] = r["asset_id"]
            if r["in_catalog_a"] == "1":
                self.by_ex_slug.setdefault((r["exchange"], r["slug"]), []).append(r["stem"])
            if r["in_catalog_a"] == "1" or r["leg_c1d2"] == "1":
                self.by_ex_slug_any.setdefault((r["exchange"], r["slug"]), []).append(r["stem"])
        for d in (self.by_ex_slug, self.by_ex_slug_any):
            for v in d.values():
                v.sort()

    def syms(self, ex, slug):
        return self.by_ex_slug.get((ex, slug), [])

    def syms_any(self, ex, slug):
        """Контракты биржи и полосы, годные как нога C1/D2: в каталоге A или нога таблицы «один актив»."""
        return self.by_ex_slug_any.get((ex, slug), [])


def rng_for(stem):
    return random.Random("%d|%s" % (SEED, stem))


def random_partners(g, cat, jobs):
    """jobs: [(y, x_str)]. -> список заданий той же длины, того же класса и биржи (seed 37 + имя группы), пары без повторов."""
    rng = rng_for(g["stem"])
    kind, ex, exx, slug = g["kind"], g["exy"], g["exx"], g["slug"]
    n = len(jobs)
    if kind in ("A1", "A2"):
        base = [s for s in cat.syms(ex, slug) if kind == "A1" or cat.asset.get((ex, s), "").split("|")[0] != "BTC"]
        bad = {frozenset((y, x)) for y, x in jobs}
        if len(base) < 2 or n > len(base) * (len(base) - 1) // 2 - len(bad):
            raise ValueError("пар недостаточно: " + g["stem"])
        out, seen = [], set()
        while len(out) < n:
            a, b = rng.sample(base, 2)
            k = frozenset((a, b))
            if k in seen or k in bad:
                continue
            seen.add(k)
            out.append((a, b))
        return out
    cand = cat.syms_any(exx, slug)
    out = []
    for y, x in jobs:
        ya = cat.asset.get((ex, y), "")
        if kind == "B1":
            pool = [s for s in cat.syms(ex, slug) if cat.asset.get((ex, s), "") != ya and s != y]
            if len(pool) < 3:
                return None
            out.append((y, "+".join(rng.sample(pool, 3))))
        else:
            pool = [s for s in cand if cat.asset.get((exx, s), "") != ya and s != y]
            if not pool:
                return None
            out.append((y, rng.choice(pool)))
    return out


# ------------------------------------------------------------------ генерация
def gen(mode, out_dir_lists, units_path, groups=None, cat=None, only=None):
    M = MODES[mode]
    groups = groups or load_groups()
    if only:
        groups = [g for g in groups if re.search(only, g["stem"])]
    cat = cat or Catalog()
    L = M["LST"]
    units, needs, ctrl_rows, skipped = [], set(), [], []
    day_re = lambda row: row[:2] + [str(M["D0"]), str(M["D1"])]

    def add(uid, stage, cmd, outs):
        units.append((uid, stage, cmd, outs))

    def jobs_of(g):
        rows = read_jobs(os.path.join(WAVES, g["jobs_file"]), ["y", "x"] if g["kind"] == "D2" else ["y", "x", "from_day", "to_day"])
        return [(r[0], r[1]) for r in rows]

    def write_jobs(rel, jobs, d2):
        path = os.path.join(out_dir_lists, rel)
        if d2:
            write_lines(path, ["y,x"] + ["%s,%s" % j for j in jobs])
        else:
            write_lines(path, ["y,x,from_day,to_day"] + ["%s,%s,%d,%d" % (j[0], j[1], M["D0"], M["D1"]) for j in jobs])
        return "%s/%s" % (L, rel)

    # --- main (боевые группы) и ap
    for g in groups:
        cmd, outs = unit_main(g, M)
        if mode == "form":
            # на формировании списки заданий - с формационными сутками (S1 пишет торговые 2479-2500)
            jobs = jobs_of(g)
            jf = write_jobs("main/%s.%s.csv" % (g["stem"], "pairs" if g["kind"] == "D2" else "jobs"), jobs, g["kind"] == "D2")
            cmd = cmd.replace(g["jobs_file"], jf)
            if g["cells_file"] and g["kind"] != "D2":
                src = os.path.join(WAVES, g["cells_file"])
                cf = "%s/main/%s" % (L, os.path.basename(g["cells_file"]))
                write_lines(os.path.join(out_dir_lists, "main", os.path.basename(g["cells_file"])), open(src, encoding="utf-8").read().splitlines())
                cmd = cmd.replace(g["cells_file"], cf)
        add("main:" + g["stem"], "w%d" % g["wave"], cmd, outs)
    if not only:
        for cid in ("binance-crypto", "bingx-crypto", "bingx-eq_us", "okx-crypto"):
            ex, slug = cid.split("-", 1)
            cellname = "A1|%s|%s" % (ex, slug)
            gg = [g for g in groups if g["cell"] == cellname][0]
            st = "ALLPAIRS_" + gg["stem"]
            cmd = ("python3 $HOME/xdl9/tools/statarb/xdl9/sel_allpairs_jobs.py %s w4/%s.jobs.csv %d %d && %s statarb run --kind pair --panels %s/%s --funding-y %s/%s --rearm "
                   "--cells %s --jobs w4/%s.jobs.csv --daily-out %s/w4/%s.daily.csv --cells-out %s/w4/%s.cells_out.csv --ext-out %s/w4/%s.ext.csv --mtm-open "
                   "--min-span 1440 --threads ${THREADS:-4}"
                   % (cid, st, M["D0"], M["D1"], BIN, M["P"], ex, M["F"], ex, gg["cells_file"], st, M["OUT"], st, M["OUT"], st, M["OUT"], st))
            add("ap:" + gg["stem"], "w4", cmd, "%s/w4/%s.*" % (M["OUT"], st))

    # --- плацебо, случайные пары, чувствительность
    for g in groups:
        jobs = jobs_of(g)
        d2 = g["kind"] == "D2"
        pair_like = g["kind"] in ("A1", "A2", "B1")
        st, kind = g["stem"], g["kind"]
        exy, exx, slug = g["exy"], g["exx"], g["slug"]
        mode_shift = "cal" if slug == "crypto" else "trade"
        base_cf = "%s/main/%s" % (L, os.path.basename(g["cells_file"])) if (mode == "form" and not d2) else g["cells_file"]
        # --- pl
        if pair_like:
            pj = [(y, "+".join(x + TAG for x in xs.split("+"))) for y, xs in jobs]
            for ex_, xs in {(exy, x) for _, x_ in jobs for x in x_.split("+")}:
                needs.add(("p", ex_, xs, mode_shift))
            P, F = "%s/p/%s" % (M["PL"], exy), "%s/f/%s" % (M["PL"], exy)
            PX = FX = None
        else:
            pj = jobs
            for _, x_ in jobs:
                needs.add(("x", exx, x_, mode_shift))
            P, F = "%s/%s" % (M["P"], exy), "%s/%s" % (M["F"], exy)
            PX, FX = "%s/x/%s" % (M["PL"], exx), "%s/fx/%s" % (M["PL"], exx)
        jf = write_jobs("pl/%s.%s.csv" % (st, "pairs" if d2 else "jobs"), pj, d2)
        O = out_dir(M, "pl")
        if d2:
            d2dir = ("d2_pl/" + st) if mode == "trade" else ("$HOME/xdl9/sel/f8_dry/d2_pl/" + st)
            gen_ = d2_gen_cmd(jf, "%s/%s" % (M["F"], exy), "%s/fx/%s" % (M["PL"], exx), d2dir, M, O + "/d2_diff.log")
            cmd = build_run(g, M, "light", st, None, jf, O, P, F, PX, FX, d2dir, gen_)
            add("pl:" + st, "pl", cmd, "%s/%s* %s" % (O, st, d2dir))
        else:
            add("pl:" + st, "pl", build_run(g, M, "light", st, base_cf, jf, O, P, F, PX, FX), "%s/%s.*" % (O, st))
        # --- rp
        rj = random_partners(g, cat, jobs)
        P, F = "%s/%s" % (M["P"], exy), "%s/%s" % (M["F"], exy)
        PX, FX = "%s/%s" % (M["P"], exx), "%s/%s" % (M["F"], exx)
        if rj is None:
            skipped.append((st, g["cell"], len(jobs), "нет кандидатов другого актива в каталоге биржи и полосы"))
        elif d2:
            jf = write_jobs("rp/%s.pairs.csv" % st, rj, True)
            O = out_dir(M, "rp")
            d2dir = ("d2_rp/" + st) if mode == "trade" else ("$HOME/xdl9/sel/f8_dry/d2_rp/" + st)
            gen_ = d2_gen_cmd(jf, F, FX, d2dir, M, O + "/d2_diff.log")
            add("rp:" + st, "rp", build_run(g, M, "light", st, None, jf, O, P, F, PX, FX, d2dir, gen_), "%s/%s* %s" % (O, st, d2dir))
        else:
            jf = write_jobs("rp/%s.jobs.csv" % st, rj, False)
            O = out_dir(M, "rp")
            add("rp:" + st, "rp", build_run(g, M, "light", st, base_cf, jf, O, P, F, PX, FX), "%s/%s.*" % (O, st))
        # --- sm (min_obs = ceil(0.9 W)), только z-виды
        if not d2:
            rows = [r.split(",") for r in open(os.path.join(WAVES, g["cells_file"]), encoding="utf-8").read().splitlines()]
            hdr, body = rows[0], rows[1:]
            assert ",".join(hdr) == HDR_CELLS
            for r in body:
                r[2] = str(max(2, math.ceil(0.9 * int(r[1]))))
            crel = "sm/%s.cells.csv" % os.path.basename(g["cells_file"])[:-len(".cells.csv")]
            write_lines(os.path.join(out_dir_lists, crel), [HDR_CELLS] + [",".join(r) for r in body])
            cf_sm = "%s/%s" % (L, crel)
            jf_main = "%s/main/%s" % (L, os.path.basename(g["jobs_file"])) if mode == "form" else g["jobs_file"]
            add("sm:" + st, "sm", build_run(g, M, "light", st, cf_sm, jf_main, out_dir(M, "sm"), P, F, PX, FX), "%s/%s.*" % (out_dir(M, "sm"), st))
        # --- n17
        hit = [(y, x) for y, x in jobs if (exy == "binance" and y in NO17_SYMS) or ((exx == "binance") and any(c in NO17_SYMS for c in x.split("+")))]
        if hit:
            keep = [j for j in jobs if j not in set(hit)]
            jf = write_jobs("n17/%s.%s.csv" % (st, "pairs" if d2 else "jobs"), keep, d2)
            O = out_dir(M, "n17")
            ctrl_rows.append((st, g["cell"], len(jobs), len(hit), len(keep)))
            if d2:
                d2dir = ("d2_n17/" + st) if mode == "trade" else ("$HOME/xdl9/sel/f8_dry/d2_n17/" + st)
                gen_ = d2_gen_cmd(jf, F, FX, d2dir, M, O + "/d2_diff.log")
                add("n17:" + st, "n17", build_run(g, M, "light", st, None, jf, O, P, F, PX, FX, d2dir, gen_), "%s/%s* %s" % (O, st, d2dir))
            else:
                add("n17:" + st, "n17", build_run(g, M, "light", st, base_cf, jf, O, P, F, PX, FX), "%s/%s.*" % (O, st))

    # --- mkplac
    nd = os.path.join(out_dir_lists, "plac_needs.tsv")
    write_lines(nd, ["layout\texchange\tsymbol\tmode"] + ["\t".join(n) for n in sorted(needs)])
    src, fsrc = M["P"], M["F"]
    mk = ("python3 $HOME/xdl9/tools/statarb/xdl9/f8_shift.py mkplac --needs %s/plac_needs.tsv --src %s --fsrc %s --out %s --lo %d --hi %d --k 1"
          % (L, src, fsrc, M["PL"], M["LO"], M["HI"]))
    units.insert(len([u for u in units if u[1] in ("w1", "w2", "w3", "w4")]), ("mkplac", "mkplac", mk, M["PL"] + "/*"))
    # --- порядок стадий
    order = {"w1": 0, "w2": 1, "w3": 2, "w4": 3, "mkplac": 4, "pl": 5, "rp": 6, "sm": 7, "n17": 8}
    units.sort(key=lambda u: (order[u[1]], u[0]))
    with open(units_path, "w", encoding="utf-8", newline=NL) as fh:
        fh.write("id\tstage\tcmd\touts" + NL)
        for u in units:
            assert "\t" not in u[2] and NL not in u[2]
            fh.write("\t".join(u) + NL)
    write_lines(os.path.join(out_dir_lists, "rp_skipped.csv"), ["stem,cell,jobs,reason"] + ["%s,%s,%d,%s" % r for r in skipped])
    write_lines(os.path.join(out_dir_lists, "n17_groups.csv"), ["stem,cell,jobs_all,jobs_with_17,jobs_without_17"] + ["%s,%s,%d,%d,%d" % r for r in ctrl_rows])
    from collections import Counter
    c = Counter(u[1] for u in units)
    print("единиц %d: %s; сдвинутых панелей-источников %d; групп n17 %d" % (len(units), dict(sorted(c.items(), key=lambda kv: order[kv[0]])), len(needs), len(ctrl_rows)))
    return units


def selftest():
    groups = load_groups()
    n = 0
    for g in groups:
        cmd, _ = unit_main(g, MODES["trade"])
        orig = g["cmd"]
        if g["kind"] == "D2":
            # единственное отличие от S1: --pairs указывает на pairs-файл группы
            orig = re.sub(r"--pairs \S+\.cells\.csv", "--pairs " + g["jobs_file"], orig)
        assert cmd == orig, (g["stem"], cmd[:300], orig[:300])
        n += 1
    print("selftest ok: %d групп воспроизведены побайтно (D2 - с исправленным --pairs)" % n)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest()
    elif len(sys.argv) > 1 and sys.argv[1] == "gen":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd")
        ap.add_argument("--mode", choices=list(MODES), default="trade")
        ap.add_argument("--out", required=True)
        ap.add_argument("--units", required=True)
        ap.add_argument("--only", help="регулярное выражение по имени группы (сухие прогоны)")
        a = ap.parse_args()
        gen(a.mode, a.out, a.units, only=a.only)
    else:
        raise SystemExit(__doc__)
