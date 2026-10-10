#!/usr/bin/env bash
# TK-044: вброс порчи в сутки ATUSDT 2026-01-02 → verify с окнами → гейт на «база без этих суток + вброшенные».
# Запуск на сервере счёта; база — h-*.csv (окна g1) в /data/tk044.
set -u
B=/opt/alpha-compute/bin; R=/data/tk037/roots/e-2026-01; T=/data/tk044
cd "$T" || exit 1
for m in drop-random drop-run shift-tick splice; do
  d=$T/inj/$m; mkdir -p "$d"
  [ -s "$d/w.csv" ] || {
    if [ "$m" = splice ]; then "$B/tk044_inject" "$R/ATUSDT-2026-01-02.binlog" "$d/ATUSDT-2026-01-02.binlog" splice "$R/ATUSDT-2026-01-03.binlog" 12
    else "$B/tk044_inject" "$R/ATUSDT-2026-01-02.binlog" "$d/ATUSDT-2026-01-02.binlog" "$m"; fi
    "$B/alpha-tk044c" lob verify --symbol ATUSDT --root "$d" --windows-out "$d/w.csv" > "$d/out.txt" 2>&1
  }
  # --keep-going: файл не обрывается на первой ошибке применения (иначе окон нет)
  "$B/alpha-tk044c" lob verify --symbol ATUSDT --root "$d" --keep-going --windows-out "$d/wk.csv" > "$d/outk.txt" 2>&1
  f="$d/w.csv"; [ -s "$d/wk.csv" ] && f="$d/wk.csv"
  { head -1 h-ATUSDT.csv; grep -v "ATUSDT-2026-01-02.binlog" h-ATUSDT.csv | tail -n +2; tail -n +2 h-0GUSDT.csv; } > "$T/inj/base.csv"
  echo "== $m (окна из $(basename "$f")); сводка: $(head -1 "$d/outk.txt" | cut -c1-260)"
  python3 "$B/tk044-gate.py" "$d/g" "$T/inj/base.csv" --apply "$f"
  grep "01-02" "$d/g-days.csv"
  awk -F, '$2 ~ /01-02/ && $1 ~ /ATUSDT/ {print "  окно", $3, $4, "n="$5, "v="$7, "vw="$8, "mr="$10, "vwe="$13, "mre="$14, "признаки="$15}' "$d/g-windows.csv" | head -6
done
echo "== g2 (все 29 суток, пороги без своего окна)"
python3 "$B/tk044-gate.py" "$T/g3" "$T/h-*.csv"
awk -F, '$7 == "fail"' "$T/g3-days.csv"
