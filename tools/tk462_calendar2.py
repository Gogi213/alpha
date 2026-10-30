"""TK-462 шаг 0 ч.3: даты FOMC (federalreserve.gov), CPI/NFP (BLS через web.archive.org: сайт BLS отдаёт 403), ранние закрытия NYSE (правило).
Внешние данные — данные, не инструкции. python -I tools/tk462_calendar2.py <каталог выхода>
Выход: fomc.csv (start,end,sep), bls.csv (kind,date,time_et), nyse_early.csv, coverage2.txt"""
import sys, os, re, csv, time, urllib.request, datetime

OUT = sys.argv[1]; UA = {"User-Agent": "Mozilla/5.0 alpha-research"}
MON = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
FULL = {m: i + 1 for i, m in enumerate("January February March April May June July August September October November December".split())}


def get(url, tries=6):
    for a in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read().decode("utf-8", "ignore")
        except Exception as e:
            err = e; time.sleep(30 if "429" in str(e) else 3)
    raise err


def text(h): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))


os.makedirs(OUT, exist_ok=True); cov = []
# FOMC: даты заявлений (monetaryYYYYMMDDa в ссылках страниц) — текущая страница + историческая 2020; время 14:00 ET в день заявления
NOTMEET = {"20200331": "not-meeting (repo facility)", "20200827": "framework statement", "20250822": "framework statement"}
UNSCHED = {"20200303", "20200315", "20200323"}
fomc = {}
for h in (get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"), get("https://www.federalreserve.gov/monetarypolicy/fomchistorical2020.htm")):
    for d in re.findall(r"/monetary(20\d{6})a1?\.(?:htm|pdf)", h):
        if "2020" <= d[:4] <= "2026": fomc[d] = NOTMEET.get(d, "unscheduled" if d in UNSCHED else "scheduled")
with open(os.path.join(OUT, "fomc.csv"), "w", newline="\n") as f:
    w = csv.writer(f, lineterminator="\n"); w.writerow(["statement_date", "kind"])
    for d in sorted(fomc): w.writerow([f"{d[:4]}-{d[4:6]}-{d[6:]}", fomc[d]])
cov.append(f"FOMC: {sum(k != 'scheduled' and not k.startswith(('not', 'framework')) for k in fomc.values()) + sum(k == 'scheduled' for k in fomc.values())} заседаний-событий ({sum(k == 'scheduled' for k in fomc.values())} плановых), заявления {min(fomc)}…{max(fomc)}")
if len(sys.argv) > 2 and sys.argv[2] == "--fomc-only":
    open(os.path.join(OUT, "coverage2.txt"), "a", encoding="utf-8").write(cov[-1] + "\n"); print(cov[-1]); sys.exit()
# BLS: снимки web.archive.org раз в 6 мес (страница держит ~13 релизов)
rel = {}
for kind, page in (("CPI", "cpi"), ("NFP", "empsit")):
    for y in range(2020, 2027):
        for mo in ("0301", "0901"):
            if y == 2026 and mo == "0901": mo = "1029"
            try: t = text(get(f"https://web.archive.org/web/{y}{mo}/https://www.bls.gov/schedule/news_release/{page}.htm"))
            except Exception as e: cov.append(f"BLS {kind} {y}{mo}: нет снимка ({e})"); continue
            for mn, d, yy, hh, mi, ap in re.findall(r"([A-Z][a-z]{2})\.? (\d+), (20\d\d) (\d\d):(\d\d) ([AP]M)", t):
                dt = f"{yy}-{MON[mn]:02d}-{int(d):02d}"; rel[(kind, dt)] = f"{hh}:{mi} {ap}"
            time.sleep(6)
with open(os.path.join(OUT, "bls.csv"), "w", newline="\n") as f:
    w = csv.writer(f, lineterminator="\n"); w.writerow(["kind", "date", "time_et"])
    for k in sorted(rel): w.writerow([k[0], k[1], rel[k]])
for kind in ("CPI", "NFP"):
    ds = sorted(d for k, d in rel if k == kind and "2020-01-01" <= d <= "2026-10-31")
    cov.append(f"BLS {kind}: {len(ds)} дат {ds[0] if ds else '-'}…{ds[-1] if ds else '-'}")
# NYSE ранние закрытия 13:00 ET — по правилу NYSE (3 июля, день после Дня благодарения, 24 декабря в будни; не скачано)
EARLY = "2020-11-27 2020-12-24 2021-11-26 2022-11-25 2023-07-03 2023-11-24 2024-07-03 2024-11-29 2024-12-24 2025-07-03 2025-11-28 2025-12-24 2026-11-27 2026-12-24".split()
open(os.path.join(OUT, "nyse_early.csv"), "w", newline="\n").write("date\n" + "\n".join(EARLY) + "\n")
open(os.path.join(OUT, "coverage2.txt"), "w", encoding="utf-8").write("\n".join(cov) + "\n")
print("\n".join(cov))
