#!/usr/bin/env python3
# TK-428 (возврат Судьи 2, п.3): перечень символов публичного архива Bybit (public.bybit.com/trading/, /spot/) с файлами в 01–07.2026
# против списка свечей (bybit-instruments.tsv: linear 1966 / spot 527). Выход: <out>/bybit-archive-index.tsv (рынок символ мес файлов),
# <out>/bybit-archive-extra.tsv (символы с файлами в 01–07, которых нет в списке). Использование: tk428-bybit-index.py <instruments.tsv> <out> [потоков]
import sys, re, time, urllib.request, concurrent.futures as cf, collections
BASE = "https://public.bybit.com"
RX = re.compile(r"(\d{4})-(\d{2})(?:-(\d{2}))?\.csv\.gz$")

def get(u):
    for a in range(5):
        try:
            return urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "alpha-tk428"}), timeout=30).read().decode("utf-8", "replace")
        except Exception:
            time.sleep(2 * (a + 1))
    return None

def hrefs(html):
    return re.findall(r'href="([^"]+)"', html or "")

def one(job):
    mk, s = job
    html = get("%s/%s/%s/" % (BASE, "trading" if mk == "linear" else "spot", s))
    if html is None:
        return mk, s, None
    c = collections.Counter()
    for h in hrefs(html):
        m = RX.search(h)
        if m and m.group(1) == "2026" and 1 <= int(m.group(2)) <= 7:
            c["2026" + m.group(2)] += 1 if m.group(3) else 1000  # месячный файл = 1000 (метка M)
    return mk, s, c

def main():
    inst, out = sys.argv[1], sys.argv[2]
    th = int(sys.argv[3]) if len(sys.argv) > 3 else 12
    known = {"linear": set(), "spot": set()}
    with open(inst, encoding="utf-8") as f:
        next(f)
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            known[p[0]].add(p[1])
    jobs = []
    for mk, d in (("linear", "trading"), ("spot", "spot")):
        for h in hrefs(get("%s/%s/" % (BASE, d))):
            h = h.strip("/")
            if h and not h.startswith(("?", "..", "http")):
                jobs.append((mk, h))
    print("символов в архиве: linear(trading) %d, spot %d" % (sum(1 for j in jobs if j[0] == "linear"), sum(1 for j in jobs if j[0] == "spot")), flush=True)
    rows, fail = [], []
    with cf.ThreadPoolExecutor(th) as ex:
        for mk, s, c in ex.map(one, jobs):
            if c is None:
                fail.append((mk, s)); continue
            for m in sorted(c):
                rows.append((mk, s, m, c[m]))
    with open(out + "/bybit-archive-index.tsv", "w", encoding="utf-8") as f:
        f.write("рынок\tсимвол\tмесяц\tфайлов(1000=месячный)\n")
        for r in rows:
            f.write("%s\t%s\t%s\t%d\n" % r)
    ext = sorted({(mk, s) for mk, s, m, n in rows if s not in known[mk]})
    with open(out + "/bybit-archive-extra.tsv", "w", encoding="utf-8") as f:
        f.write("рынок\tсимвол\tмесяцы\n")
        for mk, s in ext:
            f.write("%s\t%s\t%s\n" % (mk, s, ",".join(m for a, b, m, n in rows if a == mk and b == s)))
    print("с файлами в 01–07: linear %d spot %d; вне списка: %d; сбой листинга: %d" % (
        len({s for mk, s, m, n in rows if mk == "linear"}), len({s for mk, s, m, n in rows if mk == "spot"}), len(ext), len(fail)), flush=True)
    for x in fail:
        print("FAIL", *x)

main()
