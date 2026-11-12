#!/usr/bin/env python3
"""TK-476: докачка Bybit REST с другого IP (лимит запросов Bybit — на IP). Качалка — tk476-bybit-hist.py (one()).
Режимы:
  stage <out> <symbols.txt> [threads] [rps]   — только качает в <out>/{bybit-linear,cov} (машина без доступа на calc: София);
  push  <out> <symbols.txt> [threads] [rps]   — качает и сразу заливает на calc (ПК); перед монетой claim на calc (mkdir) и
                                                проверка, что файла ещё нет; заливка tar в /data/tk0476/incoming/{bybit-linear,cov};
  relay <vps-out>                             — забирает готовое (cov/SYM.tsv есть) с VPS и заливает на calc, VPS-копию удаляет.
SSH: env T_SSH (через «|»), T_CALC (user@host calc), T_VPS (user@host VPS, только для relay). Готово на calc подхватывает mover."""
import importlib.util, os, subprocess, sys, tarfile, threading, time
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("K_END", "1793491200000")  # = END прогона на calc (2026-11-01 00:00 UTC)
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("hist", max((os.path.join(here, "tk476-bybit-hist.py"), os.path.join(here, "..", "tk476-bybit-hist.py")), key=os.path.exists))
hist = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hist)

SSH = os.environ.get("T_SSH", "ssh").split("|")
CALC = os.environ.get("T_CALC", "")
VPS = os.environ.get("T_VPS", "")
DEST = "/srv/alpha-sda/candles-1m-476/bybit-linear"
INC = "/data/tk0476/incoming"
CLAIMS = "/data/tk0476/claims"
claim_lock = threading.Lock()  # 24 одновременных ssh.exe на Windows зависали после выполнения команды


def upload(out, sym):
    p = subprocess.Popen(SSH + [CALC, "tar -xf - -C %s" % INC], stdin=subprocess.PIPE)
    with tarfile.open(fileobj=p.stdin, mode="w|") as t:
        t.add("%s/bybit-linear/%s.csv.gz" % (out, sym), arcname="bybit-linear/%s.csv.gz" % sym)
        t.add("%s/cov/%s.tsv" % (out, sym), arcname="cov/%s.tsv" % sym)  # cov последним — маркер готовности
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError("заливка %s" % sym)


class Claimer:
    """Один постоянный ssh на calc: claim монеты = строка в stdin → «yes»/«no» (без рукопожатия ssh на каждую монету)."""
    CMD = ("while read s; do if [ -e %s/$s.csv.gz ]; then echo no; elif mkdir %s/$s 2>/dev/null; "
           "then echo yes; else echo no; fi; done" % (DEST, CLAIMS))

    def __init__(self):
        self.p = subprocess.Popen(SSH + [CALC, self.CMD], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def claim(self, sym):
        with claim_lock:
            self.p.stdin.write(sym + "\n"); self.p.stdin.flush()
            return self.p.stdout.readline().strip() == "yes"


claimer = None


def work_push(sym, out, rps):
    if not claimer.claim(sym):
        return "skip " + sym
    hist.one(sym, out, rps)
    upload(out, sym)
    os.remove("%s/bybit-linear/%s.csv.gz" % (out, sym))
    return "ok " + sym


def run_pool(fn, syms, out, threads, rps):
    os.makedirs(out + "/bybit-linear", exist_ok=True); os.makedirs(out + "/cov", exist_ok=True)
    t0, fails = time.time(), []
    with ThreadPoolExecutor(threads) as ex:
        futs = {ex.submit(fn, s, out, rps): s for s in syms}
        for i, fu in enumerate(futs, 1):
            try:
                print(i, len(syms), fu.result(), "%.0f с, запросов %d" % (time.time() - t0, hist.reqs), flush=True)
            except Exception as e:  # noqa: BLE001
                fails.append((futs[fu], str(e))); print("FAIL", futs[fu], e, flush=True)
    print("готово, провалов", len(fails), flush=True)
    return 1 if fails else 0


def relay(vout):
    done = set()
    while True:
        r = subprocess.run(SSH + [VPS, "ls %s/cov" % vout], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        for f in r.stdout.split():
            sym = f[:-4]
            if not f.endswith(".tsv") or sym in done:
                continue
            p1 = subprocess.Popen(SSH + [VPS, "cd %s && tar -cf - bybit-linear/%s.csv.gz cov/%s.tsv" % (vout, sym, sym)],
                                  stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
            p2 = subprocess.Popen(SSH + [CALC, "tar -xf - -C %s" % INC], stdin=p1.stdout)
            p1.stdout.close()
            if p2.wait() == 0 and p1.wait() == 0:
                subprocess.run(SSH + [VPS, "rm -f %s/bybit-linear/%s.csv.gz %s/cov/%s.tsv" % (vout, sym, vout, sym)],
                               stdin=subprocess.DEVNULL)
                done.add(sym); print("relay ok", sym, len(done), flush=True)
        if os.path.exists("relay.stop"):
            return 0
        time.sleep(30)


def main():
    mode = sys.argv[1]
    if mode == "relay":
        sys.exit(relay(sys.argv[2]))
    out, syms = sys.argv[2], [s.strip() for s in open(sys.argv[3]) if s.strip()]
    threads = int(sys.argv[4]) if len(sys.argv) > 4 else 24
    rps = float(sys.argv[5]) if len(sys.argv) > 5 else 100.0
    global claimer
    if mode == "push":
        claimer = Claimer()
    fn = work_push if mode == "push" else (lambda s, o, r: hist.one(s, o, r) or "ok " + s)
    sys.exit(run_pool(fn, syms, out, threads, rps))


if __name__ == "__main__":
    main()
