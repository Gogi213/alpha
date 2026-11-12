#!/usr/bin/env python3
"""TK-411: свечи 1 м Gate из месячного архива сделок (stdin-поток gz-csv не нужен: читаем файл).
Формат архива: фьюч `ts,id,price,size` (size со знаком, контракты; v = |size|·quanto_multiplier в базовой монете),
спот `ts,id,price,amount,side` (v = amount). Минута без сделок в выход не пишется (не выдумывается).
Выход csv.gz: t_open_ms,o,h,l,c,v,quote_v,n_trades; o/c — по порядку id в файле, o/h/l/c — исходные строки цен.
Использование: tk411-gate-candles.py <fut|spot> <файл.csv.gz> <quanto|1> <выход.csv.gz>; печатает «минут<TAB>строк-сделок<TAB>вне-месяца».
"""
import gzip
import sys

FMT = "%d,%s,%s,%s,%s,%.12g,%.12g,%d\n"


def main():
    kind, src, quanto, dst = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
    mult = quanto if kind == "fut" else 1.0
    out = []
    cur = None  # [minute, o_s, h_f, h_s, l_f, l_s, c_s, v, qv, n]
    ntr = 0
    bad = 0
    with gzip.open(src, "rt", encoding="ascii", newline="") as f:
        for line in f:
            p = line.split(",")
            if len(p) < 4:
                bad += 1
                continue
            try:
                ts = float(p[0])
                px = float(p[2])
                q = abs(float(p[3])) * mult
            except ValueError:
                bad += 1
                continue
            m = int(ts) // 60
            ntr += 1
            if cur is None or cur[0] != m:
                if cur is not None:
                    out.append(cur)
                cur = [m, p[2], px, p[2], px, p[2], p[2], 0.0, 0.0, 0]
            if px > cur[2]:
                cur[2], cur[3] = px, p[2]
            if px < cur[4]:
                cur[4], cur[5] = px, p[2]
            cur[6] = p[2]
            cur[7] += q
            cur[8] += q * px
            cur[9] += 1
    if cur is not None:
        out.append(cur)
    # сделки в файле идут по id; минуты монотонны, но на всякий случай склеиваем повторы минут
    out.sort(key=lambda r: r[0])
    with gzip.open(dst, "wt", encoding="ascii", compresslevel=6, newline="") as g:
        g.write("t_open_ms,o,h,l,c,v,quote_v,n_trades\n")
        for r in out:
            g.write(FMT % (r[0] * 60000, r[1], r[3], r[5], r[6], r[7], r[8], r[9]))
    print("%d\t%d\t%d" % (len(out), ntr, bad))


main()
