#!/usr/bin/env bash
# П-42 §13.24 (TK-599), шаг 0: окна счёта (дерево ссылок на уже скачанные минутки и сделки → кэш `breakout convert` → манифест sha256). Без P&L, движок не запускается.
# Запуск на srv под systemd-run (Nice=19, idle IO): systemd-run --unit=tk599-<окно> -p Nice=19 -p IOSchedulingClass=idle -p CPUQuota=300% --collect bash tk599_prep.sh <wa|wb|wc|wbx|wcx>
# Ожидание — `bash tools/srv-wait.sh file:/data/p42-tk599/<окно>/READY` (в файле код возврата). Геометрия — §13.24.4 (глубина предыстории H = 32 суток до зачётного начала; данные окна кончаются T0+73;
# движок: сетка 80 суток, прогрев 14, торговое окно COUNT_DAYS = 60 ⇒ сутки 14…73):
#   wa  P42_T0=2026-06-17  данные 17.06…29.08  торговля 01.07–29.08, зачёт 01.07–25.08 (H = 14: данных до 17.06 нет; хвост 26–29.08)
#   wb  P42_T0=2026-07-25  данные 25.07…06.10  торговля 08.08–06.10, зачёт 26.08–02.10 (разгон 08–25.08, хвост 03–06.10)
#   wc  P42_T0=2026-09-01  данные 01.09…13.11  торговля 15.09–13.11, зачёт 03.10–13.11 (разгон 15.09–02.10, конец данных 13.11)
#   wbx P42_T0=2026-07-18  данные 18.07…29.09  — проверка сходимости wb (T0 на 7 суток раньше), сравнение 26.08–25.09; после проверки каталог удаляется
#   wcx P42_T0=2026-08-25  данные 25.08…06.11  — проверка сходимости wc, сравнение 03.10–02.11; после проверки каталог удаляется
# Перекрытия торговли: wa∩wb 08–29.08, wb∩wc 15.09–06.10 — ворота G3 (§13.24.4 п. 4).
#   (окно чтения вперёд wd, P42_T0=2026-11-03 — отдельным шагом после появления 30.11 у источника; здесь не строится)
# Минутки и сделки берутся из уже лежащих деревьев (первый найденный): jul, aug, main ; новых скачиваний нет. Фандинг — файл main klines74/<SYM>/funding.csv (ряд с 01.09).
set -uo pipefail
W_NAME="${1:?wa|wb|wc|wbx|wcx}"
ROOT=/data/p42-tk599; BIN=$ROOT/bin/alpha-master; SYMS=/data/p42-t566/symbols.txt
case "$W_NAME" in
  wa) T0=2026-06-17; D0=2026-06-17; D1=2026-08-29;;
  wb) T0=2026-07-25; D0=2026-07-25; D1=2026-10-06;;
  wc) T0=2026-09-01; D0=2026-09-01; D1=2026-11-13;;
  wbx) T0=2026-07-18; D0=2026-07-18; D1=2026-09-29;;
  wcx) T0=2026-08-25; D0=2026-08-25; D1=2026-11-06;;
  *) echo "окно: wa|wb|wc|wbx|wcx" >&2; exit 2;;
esac
W=$ROOT/$W_NAME; rm -rf "$W"; mkdir -p "$W"; : > "$W/log"
fail() { echo "FAIL $*" >> "$W/log"; echo 1 > "$W/READY"; exit 1; }
python3 - "$W" "$D0" "$D1" "$SYMS" <<'PY' >> "$W/log" 2>&1 || fail build-tree
import datetime, os, sys
W, d0, d1, symf = sys.argv[1:5]
syms = [x for x in open(symf, encoding="utf-8").read().replace("\n", ",").split(",") if x]
a, b = datetime.date.fromisoformat(d0), datetime.date.fromisoformat(d1)
days = [(a + datetime.timedelta(i)).isoformat() for i in range((b - a).days + 1)]
KL = ["/data/p42-tk596/jul/klines/{s}/{d}.csv", "/data/p42-tk596/aug/klines/{s}/{d}.csv", "/data/p42-t566/klines74/{s}/{d}.csv"]
TR = ["/data/p42-tk596/jul/aggtrades/{s}/{s}-aggTrades-{d}.zip", "/data/p42-tk596/aug/aggtrades/{s}/{s}-aggTrades-{d}.zip",
      "/data/aggtrades/binance/{s}/{s}-aggTrades-{d}.zip"]
nk = nt = 0
for s in syms:
    os.makedirs(os.path.join(W, "klines", s), exist_ok=True)
    os.makedirs(os.path.join(W, "aggtrades", s), exist_ok=True)
    for d in days:
        for t in KL:
            p = t.format(s=s, d=d)
            if os.path.exists(p) and os.path.getsize(p) > 0:
                os.symlink(os.path.realpath(p), os.path.join(W, "klines", s, d + ".csv")); nk += 1; break
        for t in TR:
            p = t.format(s=s, d=d)
            if os.path.exists(p) and os.path.getsize(p) > 0:
                tgt = os.path.realpath(p)
                os.symlink(tgt, os.path.join(W, "aggtrades", s, os.path.basename(tgt))); nt += 1; break
    f = "/data/p42-t566/klines74/%s/funding.csv" % s
    if os.path.exists(f):
        os.symlink(f, os.path.join(W, "klines", s, "funding.csv"))
print("деревья: минуток %d, сделок %d, монет %d, дней %d" % (nk, nt, len(syms), len(days)))
tot = 0
for kind, sub, suf in (("aggTrades", "aggtrades", ".zip"), ("klines", "klines", ".csv")):
    for s in syms:
        names = set(os.listdir(os.path.join(W, sub, s)))
        have = [d for d in days if any(d in n and n.endswith(suf) for n in names)]
        if not have:
            continue
        gap = [d for d in days if d > have[0] and d not in have]
        if gap:
            tot += 1; print("ПРОВАЛ", kind, s, gap[:5], len(gap))
print("провалов внутри листинга (монета×вид): %d" % tot)
PY
echo "$(date -u +%T) convert, P42_T0=$T0" >> "$W/log"
P42_T0=$T0 "$BIN" breakout convert --src "$W/klines" --out "$W/cache" --threads 4 >> "$W/log" 2>&1 || fail convert
(cd "$W/aggtrades" && find -L . -type f -printf '%p %s\n' | LC_ALL=C sort) > "$W/manifest-trades.txt"
(cd "$W/klines" && find -L . -type f -printf '%p %s\n' | LC_ALL=C sort) > "$W/manifest-klines.txt"
(cd "$W/cache" && find . -type f -name '*.b42' -printf '%p %s\n' | LC_ALL=C sort) > "$W/manifest-cache.txt"
(cd "$W/cache" && find . -type f -name '*.b42' | LC_ALL=C sort | xargs -r sha256sum) > "$W/manifest-cache-sha256.txt"
{ for f in manifest-trades manifest-klines manifest-cache manifest-cache-sha256; do echo "$f $(wc -l < "$W/$f.txt") файлов, sha256 $(sha256sum "$W/$f.txt" | cut -d' ' -f1)"; done
  echo "binary $(sha256sum "$BIN" | cut -d' ' -f1) md5 $(md5sum "$BIN" | cut -d' ' -f1)"; echo "T0 $T0 D0 $D0 D1 $D1"; } | tee "$W/MANIFEST" >> "$W/log"
echo 0 > "$W/READY"
