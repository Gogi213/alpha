#!/usr/bin/env bash
# П-42 §13.22 (TK-596): входы окон июль / август для движка v21 — докачка пропусков, деревья минуток и сделок, кэш (`breakout convert`), манифест.
# Запуск на srv под systemd-run (Nice=19, idle IO); ожидание — `bash tools/srv-wait.sh file:<W>/READY` (код возврата — в <W>/READY).
#   bash tk596_prep.sh fetch                 — докачать то, чего нет в /data/p42-past06 и /data/p42-past: 0GUSDT и 龙虾USDT (первая и последняя монета symbols.txt: старый
#                                              скрипт склеил их задачи — в symbols.txt нет перевода строки в конце) и разовые сбои (CLOUSDT aggTrades 07-17, GRVTUSDT klines 07-01);
#                                              результат — /data/p42-tk596/dl (то, что уже лежит в p42-past06 / p42-past, не качается повторно)
#   bash tk596_prep.sh build <jul|aug>       — W=/data/p42-tk596/<окно>: aggtrades/ (symlink-лес), klines/<SYM>/<день>.csv, cache/ (`convert`, P42_T0 окна), MANIFEST, READY
# Окна: jul — P42_T0=2026-06-17, дни 17.06…31.07 (прогрев 14 суток + 01–31.07); aug — P42_T0=2026-07-18, дни 18.07…31.08 (прогрев 14 суток + 01–31.08).
# Фандинг в кэш не кладётся (funding.csv нет): как у августа TK-594/TK-595 — см. §13.22, отклонение D1.
set -uo pipefail
SRV=/data/p42-t566; BIN=${BIN:-$SRV/bin/breakout-v21}; ROOT=/data/p42-tk596; DL=$ROOT/dl
SYMS=$ROOT/symbols.list
mkdir -p "$ROOT"
tr ',' '\n' < "$SRV/symbols.txt" | grep -v '^$' > "$SYMS"
SRC_TR="$DL/aggtrades /data/p42-past06/aggtrades /data/p42-past/aggtrades"   # порядок поиска файла; строки, не массивы (экспорт в xargs)
SRC_KL="$DL/klines /data/p42-past06/klines /data/p42-past/klines"

dayrange() { local d="$1"; while [ "$(date -u -d "$d" +%s)" -le "$(date -u -d "$2" +%s)" ]; do echo "$d"; d=$(date -u -d "$d +1 day" +%F); done; }

fetch_one() { # <kind> <sym> <день> — в $DL, с проверкой sha256 по .CHECKSUM; уже лежащее в любом из деревьев не качается
  local kind=$1 s=$2 d=$3 base=https://data.binance.vision/data/futures/um/daily path dst f c want got srcs
  if [ "$kind" = aggTrades ]; then path="aggTrades/$s/$s-aggTrades-$d.zip"; dst="$DL/aggtrades/$s"; srcs=$SRC_TR; else path="klines/$s/1m/$s-1m-$d.zip"; dst="$DL/klines/$s"; srcs=$SRC_KL; fi
  f="$dst/$(basename "$path")"
  for r in $srcs; do [ -s "$r/$s/$(basename "$path")" ] && return 0; done
  mkdir -p "$dst"
  c=$(curl -s -m 300 --retry 3 -o "$f.part" -w '%{http_code}' "$base/$path")
  if [ "$c" != 200 ]; then rm -f "$f.part"; printf '%s\t%s\t%s\t%s\n' "$kind" "$s" "$d" "$c" >> "$DL/miss.tsv"; return 0; fi
  want=$(curl -s -m 60 --retry 3 "$base/$path.CHECKSUM" | awk '{print $1}'); got=$(sha256sum "$f.part" | awk '{print $1}')
  if [ -n "$want" ] && [ "$want" = "$got" ]; then mv "$f.part" "$f"; echo "$want  $(basename "$f")" > "$f.sha256"
  else rm -f "$f.part"; printf '%s\t%s\t%s\tsha\n' "$kind" "$s" "$d" >> "$DL/miss.tsv"; fi
}

cmd_fetch() {
  mkdir -p "$DL"; : > "$DL/miss.tsv"; rm -f "$DL/READY"
  export DL SRC_TR SRC_KL; export -f fetch_one
  { for d in $(dayrange 2026-06-17 2026-08-31); do for s in 0GUSDT 龙虾USDT; do for k in aggTrades klines; do echo "$k $s $d"; done; done; done
    for d in $(dayrange 2026-07-17 2026-07-17); do echo "aggTrades CLOUSDT $d"; done
    echo "klines GRVTUSDT 2026-07-01"; } > "$DL/tasks.txt"
  xargs -a "$DL/tasks.txt" -P 6 -L 1 bash -c 'fetch_one "$@"' _
  echo "задач $(wc -l < "$DL/tasks.txt"), файлов zip $(find "$DL" -name '*.zip' | wc -l), не-200/sha: $(wc -l < "$DL/miss.tsv")" | tee "$DL/run.log"
  echo 0 > "$DL/READY"
}

find_src() { # <kind> <sym> <день> → путь zip или пусто
  local kind=$1 s=$2 d=$3 r name
  if [ "$kind" = aggTrades ]; then name="$s-aggTrades-$d.zip"; for r in $SRC_TR; do [ -s "$r/$s/$name" ] && { echo "$r/$s/$name"; return; }; done
  else name="$s-1m-$d.zip"; for r in $SRC_KL; do [ -s "$r/$s/$name" ] && { echo "$r/$s/$name"; return; }; done; fi
}

build_sym() { # <окно-каталог> <первый день> <последний день> <sym>
  local W=$1 d0=$2 d1=$3 s=$4 d z
  mkdir -p "$W/aggtrades/$s" "$W/klines/$s"
  d="$d0"
  while [ "$d" \< "$d1" ] || [ "$d" = "$d1" ]; do
    z=$(find_src aggTrades "$s" "$d"); [ -n "$z" ] && ln -sf "$z" "$W/aggtrades/$s/$(basename "$z")"
    z=$(find_src klines "$s" "$d")
    if [ -n "$z" ]; then unzip -p "$z" > "$W/klines/$s/$d.csv.tmp" && mv "$W/klines/$s/$d.csv.tmp" "$W/klines/$s/$d.csv"; fi
    d=$(date -u -d "$d +1 day" +%F)
  done
}

cmd_build() {
  local win=${1:?jul|aug} T0 D0 D1 W
  case "$win" in jul) T0=2026-06-17; D0=2026-06-17; D1=2026-07-31;; aug) T0=2026-07-18; D0=2026-07-18; D1=2026-08-31;; *) echo "окно: jul|aug" >&2; exit 2;; esac
  W=$ROOT/$win; rm -rf "$W"; mkdir -p "$W"; : > "$W/log"
  export -f build_sym find_src dayrange; export SRC_TR SRC_KL
  xargs -a "$SYMS" -P 6 -I{} bash -c 'build_sym "$0" "$1" "$2" "$3"' "$W" "$D0" "$D1" {} 2>> "$W/log" || { echo 1 > "$W/READY"; exit 1; }
  echo "$(date -u +%T) деревья: минуток BTCUSDT $(ls "$W/klines/BTCUSDT" | wc -l), сделок BTCUSDT $(ls "$W/aggtrades/BTCUSDT" | wc -l), монет с минутками $(find "$W/klines" -name '*.csv' | awk -F/ '{print $(NF-1)}' | sort -u | wc -l)" >> "$W/log"
  # провалы внутри листинга: день без файла после первого имеющегося дня монеты (до конца окна), раздельно по виду
  python3 - "$W" "$D0" "$D1" >> "$W/log" 2>&1 <<'EOF'
import os, sys, datetime
W, d0, d1 = sys.argv[1], sys.argv[2], sys.argv[3]
a, b = datetime.date.fromisoformat(d0), datetime.date.fromisoformat(d1)
days = [(a + datetime.timedelta(i)).isoformat() for i in range((b - a).days + 1)]
tot = 0
for kind, sub, suf in (("aggTrades", "aggtrades", ".zip"), ("klines", "klines", ".csv")):
    for s in sorted(os.listdir(os.path.join(W, sub))):
        names = set(os.listdir(os.path.join(W, sub, s)))
        have = [d for d in days if any(d in n and n.endswith(suf) for n in names)]
        if not have: continue
        gap = [d for d in days if d > have[0] and d not in have]
        if gap:
            tot += 1; print("ПРОВАЛ", kind, s, gap[:5], len(gap))
print("провалов внутри листинга (монета×вид): %d" % tot)
EOF
  echo "$(date -u +%T) convert, P42_T0=$T0" >> "$W/log"
  rm -rf "$W/cache"
  P42_T0=$T0 "$BIN" breakout convert --src "$W/klines" --out "$W/cache" --threads 6 >> "$W/log" 2>&1 || { echo 1 > "$W/READY"; exit 1; }
  # манифест входов: sha256 по отсортированному списку «путь размер» (сделки — по целевым файлам symlink-ов; минутки csv; кэш b42)
  (cd "$W/aggtrades" && find -L . -type f -printf '%p %s\n' | LC_ALL=C sort) > "$W/manifest-trades.txt"
  (cd "$W/klines" && find -L . -type f -printf '%p %s\n' | LC_ALL=C sort) > "$W/manifest-klines.txt"
  (cd "$W/cache" && find . -type f -name '*.b42' -printf '%p %s\n' | LC_ALL=C sort) > "$W/manifest-cache.txt"
  { for f in manifest-trades manifest-klines manifest-cache; do echo "$f $(wc -l < "$W/$f.txt") файлов, sha256 $(sha256sum "$W/$f.txt" | cut -d' ' -f1)"; done; } | tee "$W/MANIFEST" >> "$W/log"
  echo 0 > "$W/READY"
}

case "${1:-}" in fetch) cmd_fetch;; build) shift; cmd_build "$@";; *) sed -n 2,12p "$0"; exit 2;; esac
