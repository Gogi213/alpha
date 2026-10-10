#!/usr/bin/env bash
# TK-026: сверка sha256 копии суток в stage с оригиналом на ящике (хэш считает ящик, канал не грузится).
#   bash tk026-sha.sh 2026-01-01 [2026-01-02 ...]   -> ~/alpha/stage/sha-<D>.txt (ok|DIFF + счётчики); rc=0 только если все сутки ok
# Требует <stage>/<D>/.ready. Читает только ящик (sha256sum), ничего не меняет и не удаляет.
STAGE="${STAGE:-/dev/shm/alpha-stage}"; META="${META:-$HOME/alpha/stage}"
KEY="$HOME/.ssh/id_storagebox"; BOX=u677479@u677479.your-storagebox.de
MON=(x jan feb mar apr may jun jul aug sep oct nov dec)
rc=0
for D in "$@"; do
  m=${MON[$((10#${D:5:2}))]}
  out="$META/sha-$D.txt"; : >"$out"; bad=0; n=0
  for sub in root D20; do
    if [ $sub = root ]; then rp="alpha/epochs/e-$m/root"; else rp="alpha/derived/tk015/e-$m/D20/$D"; fi
    ( cd "$STAGE/$D/$sub" && sha256sum -- * ) | sort -k2 >"$out.loc.$sub"
    names=$(awk '{print $2}' "$out.loc.$sub" | sed "s|^|$rp/|" | tr '\n' ' ')
    # shellcheck disable=SC2086
    ssh -n -p 23 -i "$KEY" -o BatchMode=yes "$BOX" sha256sum $names | sed "s|  $rp/|  |" | sort -k2 >"$out.box.$sub"
    nl=$(wc -l <"$out.loc.$sub"); nb=$(wc -l <"$out.box.$sub")
    if [ "$nl" != "$nb" ]; then echo "$sub: ящик вернул $nb из $nl хэшей (неполный ответ)" >>"$out"; bad=1; fi
    if ! diff -q "$out.loc.$sub" "$out.box.$sub" >/dev/null; then echo "$sub: DIFF" >>"$out"; diff "$out.loc.$sub" "$out.box.$sub" | head -5 >>"$out"; bad=1; fi
    n=$((n + nl)); rm -f "$out.loc.$sub" "$out.box.$sub"
  done
  if [ $bad = 0 ]; then echo "ok $D файлов=$n $(date +%FT%T)" >"$out"; else echo "DIFF $D $(date +%FT%T)" >>"$out"; rc=1; fi
  cat "$out" | head -3
done
exit $rc
