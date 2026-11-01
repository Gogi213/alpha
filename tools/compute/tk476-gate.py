#!/usr/bin/env python3
"""TK-476 (В-85): минутные свечи Gate futures_usdt с 2020-01 из месячных архивов сделок download.gatedata.org (сырьё ХРАНИТСЯ).
usage: tk476-gate.py <out> <contracts.txt> [процессов=12] [потоков_закачки=1]
Раскладка: <out>/gate-raw/<C>/<C>-<ГГГГММ>.csv.gz (сырьё), <out>/gate-parts/<C>/<ГГГГММ>.csv.gz (свечи месяца), <out>/gate-futures_usdt/<C>.csv.gz (все месяцы подряд),
<out>/gate-cov/<C>.tsv (контракт, минут, строк-сделок, первая_мс, последняя_мс, месяцев, нет_месяцев). Месяца нет на источнике (404) — не выдумывается.
Свечи: tk411-gate-candles.py (o/c по порядку id, v = |size|·quanto, минута без сделок не пишется). Готовый месяц пропускается."""
import gzip, json, os, shutil, subprocess, sys, time, urllib.error, urllib.request
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
CANDLES = os.path.join(HERE, "tk476-gate-candles.py")  # TK-476: порядок строк архива не важен (старые месяцы — от новых к старым)
BASE = "https://download.gatedata.org/futures_usdt/trades/%s/%s-%s.csv.gz"
FIRST, LAST = (2020, 1), tuple(int(x) for x in os.environ.get("G_LAST", "2026-10").split("-"))


def months():
    y, m = FIRST
    while (y, m) <= LAST:
        yield "%04d%02d" % (y, m)
        m += 1
        if m == 13:
            y, m = y + 1, 1


def fetch(url, dst):
    """200 → файл; 404 → None; прочее — повтор, потом исключение."""
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(dst + ".part", "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            with gzip.open(dst + ".part", "rb") as g:  # целостность gzip
                while g.read(1 << 24):
                    pass
            os.replace(dst + ".part", dst)
            return True
        except urllib.error.HTTPError as e:
            if e.code in (404, 403):  # Gate отдаёт 403 на несуществующие ключи S3-хранилища
                return None
            time.sleep(5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError, EOFError, OSError):
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("не вышло: " + url)


def work(a):
    try:
        return _work(a)
    except Exception as e:  # noqa: BLE001
        return a[1], "FAIL " + str(e)


def _work(a):
    out, c, q = a
    if os.path.exists("%s/gate-cov/%s.tsv" % (out, c)):
        return c, "skip"
    for d in ("gate-raw/" + c, "gate-parts/" + c):
        os.makedirs("%s/%s" % (out, d), exist_ok=True)
    have, miss, ntr = [], [], 0
    for ym in months():
        raw = "%s/gate-raw/%s/%s-%s.csv.gz" % (out, c, c, ym)
        part = "%s/gate-parts/%s/%s.csv.gz" % (out, c, ym)
        if not os.path.exists(part):
            if not os.path.exists(raw):
                if not fetch(BASE % (ym, c, ym), raw):
                    miss.append(ym); continue
            r = subprocess.run([sys.executable, "-I", CANDLES, "fut", raw, q, part + ".part"], capture_output=True, text=True)
            if r.returncode:
                raise RuntimeError("%s %s: %s" % (c, ym, r.stderr[-300:]))
            os.replace(part + ".part", part)
            ntr += int(r.stdout.split("\t")[1])
        have.append(ym)
    n, first, last = 0, "", ""
    dst = "%s/gate-futures_usdt/%s.csv.gz" % (out, c)
    with gzip.open(dst + ".part", "wb", 6) as g:
        g.write(b"t_open_ms,o,h,l,c,v,quote_v,n_trades\n")
        for ym in have:
            with gzip.open("%s/gate-parts/%s/%s.csv.gz" % (out, c, ym), "rb") as f:
                next(f)
                for line in f:
                    t = int(line.split(b",", 1)[0])
                    if last and t <= int(last):
                        raise RuntimeError("%s: время не растёт на %d" % (c, t))
                    first = first or str(t); last = str(t); n += 1
                    g.write(line)
    os.replace(dst + ".part", dst)
    with open("%s/gate-cov/%s.tsv" % (out, c), "w") as f:
        f.write("%s\t%d\t%d\t%s\t%s\t%d\t%s\n" % (c, n, ntr, first, last, len(have), ",".join(miss)))
    return c, "ok %d" % n


def main():
    out, cs = sys.argv[1], [s.strip() for s in open(sys.argv[2]) if s.strip()]
    procs = int(sys.argv[3]) if len(sys.argv) > 3 else 12
    os.makedirs(out + "/gate-cov", exist_ok=True); os.makedirs(out + "/gate-futures_usdt", exist_ok=True)
    with urllib.request.urlopen("https://api.gateio.ws/api/v4/futures/usdt/contracts", timeout=60) as r:
        qm = {x["name"]: x["quanto_multiplier"] for x in json.load(r)}
    jobs, nq = [], []
    for c in cs:
        if c in qm:
            jobs.append((out, c, qm[c]))
        else:
            nq.append(c)  # снят с торгов / нет в списке REST
    print("контрактов", len(cs), "с quanto", len(jobs), "без quanto (нужно из tk411 gate-list):", nq, flush=True)
    if nq and os.path.exists("/data/tk0393/tk411/gate-list.tsv"):
        old = {l.split("\t")[1]: l.split("\t")[2].strip() for l in open("/data/tk0393/tk411/gate-list.tsv") if l.startswith("fut\t")}
        jobs += [(out, c, old[c]) for c in nq if c in old]
        print("из gate-list:", [c for c in nq if c in old], "нет нигде:", [c for c in nq if c not in old], flush=True)
    t0, bad = time.time(), []
    with ProcessPoolExecutor(procs) as ex:
        for i, (c, st) in enumerate(ex.map(work, jobs), 1):
            print("%d/%d %s %s %.0f с" % (i, len(jobs), c, st, time.time() - t0), flush=True)
    print("готово", flush=True)


if __name__ == "__main__":
    main()
