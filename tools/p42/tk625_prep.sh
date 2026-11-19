#!/usr/bin/env bash
# П-42 §13.32 (TK-625), окно истории `w0` честной вселенной (775 монет, деревья TK-621): ссылки на файлы `wa` за 17.06–03.07 → кэш `breakout convert` (P42_T0=2026-06-03) → манифест sha256. Без P&L.
# Запуск на srv: systemd-run --unit=tk625-prep-h775-w0 -p Nice=19 -p IOSchedulingClass=idle -p CPUQuota=400% --collect bash tk625_prep.sh w0
# Ожидание: `bash tools/srv-wait.sh file:/data/p42-tk625/h775/w0/READY` (в файле код возврата). Данные wa читаются только на чтение (ссылки через readlink -f).
set -uo pipefail
W_NAME="${1:?w0}"
[ "$W_NAME" = w0 ] || { echo "только w0" >&2; exit 2; }
ROOT=/data/p42-tk625/h775; SRC=/data/p42-tk621/wa; BIN=/data/p42-tk599/bin/alpha-master; SYMS=/data/p42-tk621/universe/symbols-all.txt
T0=2026-06-03; D0=2026-06-17; D1=2026-07-03
W=$ROOT/$W_NAME
mkdir -p "$ROOT"
avail_gb=$(df -BG --output=avail /data | tail -1 | tr -dc '0-9')
[ "$avail_gb" -ge 8 ] || { echo "FAIL свободно ${avail_gb} ГБ < 8" >&2; exit 3; }
rm -rf "$W"; mkdir -p "$W"; : > "$W/log"
fail() { echo "FAIL $*" >> "$W/log"; echo 1 > "$W/READY"; exit 1; }
python3 - "$W" "$SRC" "$D0" "$D1" "$SYMS" <<'PY' >> "$W/log" 2>&1 || fail build-tree
import datetime, os, sys
W, src, d0, d1, symf = sys.argv[1:6]
syms = [x for x in open(symf, encoding="utf-8").read().replace("\n", ",").split(",") if x]
a, b = datetime.date.fromisoformat(d0), datetime.date.fromisoformat(d1)
days = [(a + datetime.timedelta(i)).isoformat() for i in range((b - a).days + 1)]
nk = nt = nf = 0
for s in syms:
    os.makedirs(os.path.join(W, "klines", s), exist_ok=True)
    os.makedirs(os.path.join(W, "aggtrades", s), exist_ok=True)
    for d in days:
        p = os.path.join(src, "klines", s, d + ".csv")
        if os.path.exists(p) and os.path.getsize(p) > 0:
            os.symlink(os.path.realpath(p), os.path.join(W, "klines", s, d + ".csv")); nk += 1
        p = os.path.join(src, "aggtrades", s, "%s-aggTrades-%s.zip" % (s, d))
        if os.path.exists(p) and os.path.getsize(p) > 0:
            tgt = os.path.realpath(p)
            os.symlink(tgt, os.path.join(W, "aggtrades", s, os.path.basename(p))); nt += 1
    f = os.path.join(src, "klines", s, "funding.csv")
    if os.path.exists(f):
        os.symlink(os.path.realpath(f), os.path.join(W, "klines", s, "funding.csv")); nf += 1
print("деревья w0: минуток %d, сделок %d, funding %d, монет %d, дней %d" % (nk, nt, nf, len(syms), len(days)))
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
