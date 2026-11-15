"""Оборот за окно по биржам и монетам из записанных сделок сборщиков (trade<N>-<метка>.tsv.zst), $ = цена × количество × размер контракта.

Запуск: python3 turnover.py <окна.json> <выход.json> [PAR]. Окна (мс, время сделки T, [t0, t1)): {"page": [t0, t1], "chk": {биржа: {"def": [w0, w1], "sym": {символ: [w0, w1]}}}}:
page — окно страницы (как у arb.json), chk — окно 24-часового тикера биржи (общее и по символам) для сверки. Запись t (см. parse_trades.py):
t dns lag ET sym da p q …; T = приём − lag − ET; размер контракта — ctVal из пулов (только OKX; у остальных количество в монете).
Выход: {"win": окна, "sym": {биржа: {символ: [usd_page, base_page, n_page, usd_chk, base_chk, n_chk]}}, "coin": {монета: {биржа: [usd_page, usd_chk]}}, "run": {...}}.
"""
import glob
import json
import re
import subprocess
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import EXCHANGES, ROOTS, norm  # noqa: E402

TRADE = re.compile(r"trade(\d+)-(\d+)\.tsv\.zst$")
DECOMP = ["zstd", "-dc"]
G = {}


def scan(job):
    ex, path = job
    (p0, p1), (d0, d1), cw, units = G["page"], G["cdef"][ex], G["cw"][ex], G["units"][ex]
    acc, cum, lines, rx_min, rx_max = {}, 0, 0, None, 0
    pr = subprocess.Popen([*DECOMP, path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 22)
    stop = max([p1, d1] + [w[1] for w in cw.values()]) + 120_000
    for line in pr.stdout:
        if not line.endswith(b"\n"):
            break
        x = line.split(b"\t", 8)
        cum += int(x[1])
        lines += 1
        if x[0] != b"t":
            continue
        rx = cum // 1_000_000
        if rx_min is None:
            rx_min = rx
        rx_max = rx
        if rx > stop:
            break
        T = rx - int(x[2]) - int(x[3])
        s = x[4]
        c0, c1 = cw.get(s, (d0, d1))
        inp, inc = p0 <= T < p1, c0 <= T < c1
        if not (inp or inc):
            continue
        q = float(x[7]) * units.get(s, 1.0)
        v = float(x[6]) * q
        a = acc.get(s)
        if a is None:
            a = acc[s] = [0.0, 0.0, 0, 0.0, 0.0, 0]
        if inp:
            a[0] += v
            a[1] += q
            a[2] += 1
        if inc:
            a[3] += v
            a[4] += q
            a[5] += 1
    pr.stdout.close()
    pr.wait()
    return ex, path, acc, {"lines": lines, "rx_min": rx_min, "rx_max": rx_max}


def metas(ex):
    out = {}
    for f in sorted(glob.glob(f"{ROOTS[ex]}/pool-*.json")):
        out.update(json.loads(Path(f).read_text(encoding="utf-8")).get("meta") or {})
    return out


def pick_files(days, t0, t1):
    """Файл дня берём, если следующий запуск той же полосы начался не раньше t0 и сам запуск начался до t1 (грубо: по меткам имён)."""
    jobs = []
    for ex in EXCHANGES:
        for day in days:
            by = {}
            for f in glob.glob(f"{ROOTS[ex]}/{day}/trade*.tsv.zst"):
                m = TRADE.search(f)
                if m:
                    by.setdefault(m.group(1), []).append((int(m.group(2)), f))
            for lane, fs in by.items():
                fs.sort()
                for i, (stamp, f) in enumerate(fs):
                    nxt = fs[i + 1][0] if i + 1 < len(fs) else 1 << 60
                    if stamp * 1000 < t1 and nxt * 1000 >= t0:
                        jobs.append((ex, f))
    return jobs


def main(win_path, out_path, par=4):
    w = json.loads(Path(win_path).read_text())
    t0, t1 = w["page"]
    allw = [tuple(c["def"]) for c in w["chk"].values()] + [tuple(v) for c in w["chk"].values() for v in c["sym"].values()]
    lo, hi = min([t0] + [a for a, _ in allw]), max([t1] + [b for _, b in allw])
    from datetime import datetime, timedelta, timezone
    d, days = datetime.fromtimestamp(lo / 1000, timezone.utc).date(), []
    while d <= datetime.fromtimestamp(hi / 1000, timezone.utc).date():
        days.append(d.isoformat())
        d += timedelta(days=1)
    M = {ex: metas(ex) for ex in EXCHANGES}
    G["page"] = (t0, t1)
    G["cdef"] = {ex: tuple(w["chk"][ex]["def"]) for ex in EXCHANGES}
    G["cw"] = {ex: {k.encode(): tuple(v) for k, v in w["chk"][ex]["sym"].items()} for ex in EXCHANGES}
    G["units"] = {ex: ({s.encode(): float(m.get("ctVal") or 1.0) for s, m in M[ex].items()} if ex == "okx" else {}) for ex in EXCHANGES}
    jobs = pick_files(days, lo, hi)
    jobs.sort(key=lambda j: -Path(j[1]).stat().st_size)
    sym = {ex: {} for ex in EXCHANGES}
    runs = {ex: [] for ex in EXCHANGES}
    with Pool(par) as p:
        for ex, path, acc, info in p.imap_unordered(scan, jobs, chunksize=1):
            for s, a in acc.items():
                t = sym[ex].setdefault(s.decode(), [0.0, 0.0, 0, 0.0, 0.0, 0])
                for i in range(6):
                    t[i] += a[i]
            runs[ex].append({"file": path.split("/data/")[-1], **info})
    coin = {}
    for ex in EXCHANGES:
        for s, a in sym[ex].items():
            c, _ = norm(ex, s, M[ex])
            t = coin.setdefault(c, {}).setdefault(ex, [0.0, 0.0])
            t[0] += a[0]
            t[1] += a[3]
    Path(out_path).write_text(json.dumps({"win": w, "sym": sym, "coin": coin, "run": runs}, separators=(",", ":")))
    print(json.dumps({"files": len(jobs), "syms": {ex: len(sym[ex]) for ex in EXCHANGES}}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 4)
