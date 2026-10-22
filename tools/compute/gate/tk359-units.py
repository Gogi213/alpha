#!/usr/bin/env python3
"""TK-359: список единиц боевого прогона `lob seconds` — монето-сутки кэша касаний TK-355 (фьюч TK-046 + спот TK-319).
Строка: <корень суток> <SYM> <сутки> <fut|spot>. Корень = study/root-<сутки> эпохи; единица берётся, если рядом лежит бинлог.
Те же глобы, что `g015-ceilings.py` (49 724 файла кэша); обход каталогов — только эти глобы, без find/du.
    tk359-units.py <выход.txt>
"""
import glob, os, sys

MON = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep"]
FUT = "/data/tk046/{m}/home/alpha/epochs/e-{m}/study/approaches/D20/2026-*/touches-*USDT.csv"
SPOT = "/data/tk0319/jrn/2026-*/wt/sub/D20/2026-*/touches-*USDT.csv"
FUT_ROOT = "/data/tk046/{m}/home/alpha/epochs/e-{m}/study/root-{d}"
SPOT_ROOT = "/data/tk0319/e-{m}/study/root-{d}"


def parts(p):
    day = p.split("/D20/")[1][:10]
    sym = os.path.basename(p)[len("touches-"):-len(".csv")]
    return sym, day


def main(out):
    units, miss = [], 0
    for m in MON:
        for p in glob.glob(FUT.format(m=m)):
            sym, day = parts(p)
            units.append((FUT_ROOT.format(m=m, d=day), sym, day, "fut"))
    for p in glob.glob(SPOT):
        sym, day = parts(p)
        units.append((SPOT_ROOT.format(m=MON[int(day[5:7]) - 1], d=day), sym, day, "spot"))
    keep = []
    for u in sorted(set(units), key=lambda u: (u[3], u[2], u[1])):
        if glob.glob(f"{u[0]}/{u[1]}-{u[2]}.binlog*"):
            keep.append(u)
        else:
            miss += 1
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        for u in keep:
            f.write(" ".join(u) + "\n")
    os.replace(out + ".tmp", out)
    print(f"units={len(keep)} fut={sum(u[3] == 'fut' for u in keep)} spot={sum(u[3] == 'spot' for u in keep)} без_бинлога={miss}")


if __name__ == "__main__":
    main(sys.argv[1])
