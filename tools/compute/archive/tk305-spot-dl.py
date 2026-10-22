#!/usr/bin/env python3
"""TK-305 шаг 2: закачка архива спота Bybit (стакан ob200 + сделки) по списку HEAD `sizes.csv` (sym,day,bytes).

  tk305-spot-dl.py --list sizes.csv --out /srv/alpha-sda/tk305/spot [--jobs 6] [--rate 4M] [--limit N]

Раскладка: <out>/ob/SYM/DAY_SYM_ob200.data.zip, <out>/tr/SYM/SYM_DAY.csv.gz. Файл пишется в `.part` и переименовывается
после проверки (ob: размер == HEAD и zip открывается; tr: gzip читается целиком). Журнал `<out>/../dl/done.csv`:
sym,day,kind,status,bytes,secs (status ok|missing|badsize|fail); готовое (ok|missing) при перезапуске пропускается.
Остановка — файл `<out>/../dl/stop` (воркеры не берут новое). Скорость — `<out>/../dl/run.log` раз в минуту.
Данные как на бирже (В-172): не достраиваем и не пересобираем, нет файла — строка missing.
"""
import argparse, csv, gzip, os, subprocess, threading, time, zipfile
from concurrent.futures import ThreadPoolExecutor

OB = "https://quote-saver.bycsi.com/orderbook/spot/{s}/{d}_{s}_ob200.data.zip"
TR = "https://public.bybit.com/spot/{s}/{s}_{d}.csv.gz"

lock = threading.Lock()
got = [0]  # байт за всё время


def fetch(url, part, rate):
    """-> http-код (0 — сбой curl)."""
    r = subprocess.run(["curl", "-sS", "--limit-rate", rate, "--max-time", "3600", "-o", part, "-w", "%{http_code}", url],
                       capture_output=True, text=True)
    try:
        return int(r.stdout.strip() or 0)
    except ValueError:
        return 0


def one(kind, sym, day, want, out, rate, dl, journal):
    path = (f"{out}/ob/{sym}/{day}_{sym}_ob200.data.zip" if kind == "ob" else f"{out}/tr/{sym}/{sym}_{day}.csv.gz")
    url = (OB if kind == "ob" else TR).format(s=sym, d=day)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part, t0, status, size = path + ".part", time.time(), "fail", 0
    for attempt in range(4):
        code = fetch(url, part, rate)
        size = os.path.getsize(part) if os.path.exists(part) else 0
        if code == 404:
            if attempt >= 1:
                status = "missing"
                break
        elif code == 200:
            try:
                if kind == "ob":
                    if want and size != want:
                        status = "badsize"
                    else:
                        zipfile.ZipFile(part).close()
                        status = "ok"
                else:
                    with gzip.open(part, "rb") as g:
                        while g.read(1 << 20):
                            pass
                    status = "ok"
            except (zipfile.BadZipFile, OSError, EOFError):
                status = "fail"
            if status == "ok":
                break
        time.sleep(10 * (attempt + 1))   # 403 на существующий файл бывает (TK-021) — повтор
    if status == "ok":
        os.replace(part, path)
    else:
        if status == "badsize" and os.path.exists(part):
            os.replace(part, path + ".badsize")
        elif os.path.exists(part):
            os.remove(part)
    with lock:
        got[0] += size if status == "ok" else 0
        journal.writerow([sym, day, kind, status, size if status == "ok" else 0, f"{time.time() - t0:.1f}"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--rate", default="4M")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    dl = os.path.join(os.path.dirname(a.out.rstrip("/")), "dl")
    os.makedirs(dl, exist_ok=True)
    done_p, stop_p = f"{dl}/done.csv", f"{dl}/stop"
    have = set()
    if os.path.exists(done_p):
        with open(done_p, encoding="utf-8") as f:
            for r in csv.reader(f):
                if len(r) >= 4 and r[3] in ("ok", "missing"):
                    have.add((r[0], r[1], r[2]))
    with open(a.list, encoding="utf-8") as f:
        rows = sorted(((r["day"], r["sym"], int(r["bytes"])) for r in csv.DictReader(f)))
    todo = []
    for day, sym, size in rows:
        for kind, want in (("ob", size), ("tr", 0)):
            if (sym, day, kind) not in have:
                todo.append((kind, sym, day, want))
    if a.limit:
        todo = todo[:a.limit]
    jf = open(done_p, "a", encoding="utf-8", newline="")
    journal = csv.writer(jf)
    t_start = time.time()
    runlog = open(f"{dl}/run.log", "a", encoding="utf-8")
    print(f"{time.strftime('%FT%T')} старт: к закачке {len(todo)} файлов (готово {len(have)}), jobs={a.jobs} rate={a.rate}",
          file=runlog, flush=True)

    def tick():
        last, t_last = 0, time.time()
        while not ev.wait(60):
            now = time.time()
            with lock:
                g = got[0]
                jf.flush()
            print(f"{time.strftime('%FT%T')} за запуск {g / 1e9:.2f} ГБ, сейчас {(g - last) / (now - t_last) / 1e6:.1f} МБ/с",
                  file=runlog, flush=True)
            last, t_last = g, now

    ev = threading.Event()
    threading.Thread(target=tick, daemon=True).start()

    def work(t):
        if os.path.exists(stop_p):
            return
        one(*t, a.out, a.rate, dl, journal)

    with ThreadPoolExecutor(a.jobs) as ex:
        list(ex.map(work, todo))
    ev.set()
    jf.close()
    print(f"{time.strftime('%FT%T')} конец: {got[0] / 1e9:.2f} ГБ за {time.time() - t_start:.0f} с", file=runlog, flush=True)


if __name__ == "__main__":
    main()
