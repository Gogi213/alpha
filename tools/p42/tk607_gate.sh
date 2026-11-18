#!/usr/bin/env bash
# TK-607: ворота «байт в байт» нового бинаря движка против эталонных выходов. Запускать на srv под systemd-run (приказ координатора 18.11: --nice=0 -p CPUWeight=1000).
# Использование: bash tk607_gate.sh <BIN> <метка> <окно wa|wb|wc|rtfz> <файл вариантов> [ключи движка: --tick-store … --sig-cache …]
# Окна wa/wb/wc — как `Engine.command` TK-599 (P42_T0 окна, --symbols, --threads 4); эталон — /data/p42-tk599/out/<окно>/<вариант> (посчитан a03c91df),
# сравниваются `<сторона>-{trades,daily,coinday,entryrej}.csv`, `<сторона>-detail.jsonl`, `meta.json`.
# Окно rtfz — ворота TK-597 `rt-fz` (L_C, S_C; P42_DETAIL=1; 80 суток × 566 монет; T0 2026-09-01): эталон /data/p42-t566/out/rt-fz (бинарь v21), без meta.json.
# Выходы — /data/p42-tk607/gate/<метка>/<окно>/; md5 новых файлов — <метка>/<окно>.md5. REF_LOG=<журнал эталонного вызова> — сверка счётчиков загрузки/диагностики фазы 1
# (только для холодного прогона: при попадании кэша сигналов диагностические счётчики фазы 1 не накапливаются).
# `KEEP_OUT=0` — при 0 различий выход после сверки удаляется (остаются md5 и журнал). Итог: `GATE OK|DIFF <метка> <окно> вариантов=<n> файлов=<n> различий=<n>`.
set -uo pipefail
BIN="$1"; LABEL="$2"; W="$3"; VARS="$4"; shift 4
ROOT=/data/p42-tk599; G=/data/p42-tk607/gate/$LABEL
CACHE=$ROOT/$W/cache; TRADES=$ROOT/$W/aggtrades; REFROOT=$ROOT/out/$W; XENV=(); META=1
case "$W" in
  wa) T0=2026-06-17;; wb) T0=2026-07-25;; wc) T0=2026-09-01;;
  rtfz) T0=2026-09-01; CACHE=/data/p42-t566/cache80; TRADES=/data/aggtrades/binance; REFROOT=/data/p42-t566/out/rt-fz; XENV=(P42_DETAIL=1); META=0;;
  *) echo "окно?"; exit 2;;
esac
mkdir -p "$G"; OUT="$G/$W"; rm -rf "$OUT"
# варианты с фильтром признака (fa=) эталон гнал с `--ftab <union окна и стороны>`: путь берётся из первой строки эталонного журнала пакета (текущий union — надмножество прежнего, значения общих ключей те же)
FT=()
if grep -q ' fa=' "$VARS"; then
  F=$(head -1 "$ROOT/log/$(basename "$VARS" .txt)-$W.log" 2>/dev/null | grep -o -- '--ftab [^ ]*' | cut -d' ' -f2)
  [ -n "$F" ] && FT=(--ftab "$F")
fi
LOG="$G/$W.log"
S=$(date +%s)
env P42_T0=$T0 "${XENV[@]}" "$BIN" breakout tick --cache "$CACHE" --trades "$TRADES" --variants "$VARS" \
  --symbols "$(cat /data/p42-t566/symbols.txt)" --out "$OUT" --threads 4 "${FT[@]}" "$@" > "$LOG" 2>&1
RC=$?
echo "движок rc=$RC за $(( $(date +%s) - S )) с"
[ $RC -eq 0 ] || { tail -5 "$LOG"; echo "GATE FAIL $LABEL $W rc=$RC"; exit 1; }
n=0; d=0; nv=0; : > "$G/$W.md5"
while read -r vid _; do
  [ -z "$vid" ] && continue
  nv=$((nv+1))
  for side in long short; do
    for f in trades.csv daily.csv coinday.csv entryrej.csv detail.jsonl; do
      a="$OUT/$vid/$side-$f"; b="$REFROOT/$vid/$side-$f"
      [ ! -e "$a" ] && [ ! -e "$b" ] && continue
      n=$((n+1))
      if [ ! -e "$a" ] || [ ! -e "$b" ] || ! cmp -s "$a" "$b"; then d=$((d+1)); echo "  DIFF $vid $side-$f"; fi
      [ -e "$a" ] && md5sum "$a" >> "$G/$W.md5"
    done
  done
  if [ "$META" = 1 ]; then
    a="$OUT/$vid/meta.json"; b="$REFROOT/$vid/meta.json"
    n=$((n+1))
    if [ ! -e "$a" ] || [ ! -e "$b" ] || ! cmp -s "$a" "$b"; then d=$((d+1)); echo "  DIFF $vid meta.json"; fi
    [ -e "$a" ] && md5sum "$a" >> "$G/$W.md5"
  fi
done < "$VARS"
if [ -n "${REF_LOG:-}" ] && [ -e "$REF_LOG" ]; then
  cnt() { { grep -m1 '^фаза 1 готова' "$1" | sed 's/.*; файлов/файлов/'; grep -E '^(pk |pb |свежий уровень)' "$1"; } | md5sum; }
  n=$((n+1)); if [ "$(cnt "$LOG")" != "$(cnt "$REF_LOG")" ]; then d=$((d+1)); echo "  DIFF счётчики журнала vs $REF_LOG"; fi
fi
grep -E "^фаза 1 готова|^готово:|^tick-store|^sig-cache" "$LOG" | sed 's/^/  /'
if [ "$d" = 0 ]; then [ "${KEEP_OUT:-1}" = 1 ] || rm -rf "$OUT"; echo "GATE OK $LABEL $W вариантов=$nv файлов=$n различий=0"; else echo "GATE DIFF $LABEL $W вариантов=$nv файлов=$n различий=$d"; fi
