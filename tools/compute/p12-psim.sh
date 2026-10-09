#!/bin/bash
# П-12 (TK-135/С-64): деревья суток волны -> по месяцу -> busy-replay -> portfolio-sim (cap 0 и 3). Бывшие p12-r2-psim.sh / p12-r2a-psim.sh / p12-r3a-psim.sh — теперь один скрипт, набор — аргумент:
#   r2  — волна TK-065 (w-/wt-, --busy-skip off) -> /data/p12r2/ (без досчёта 8 суток TK-040; без --drop; --json)
#   r2a — пересчёт семей A (wa-*/b5/r2a, правка 6b792282) -> /data/p12r2a/closes-va-cap<N>-<мес>.json (только A-клетки; база и не-A — из /data/p12r2)
#   r3a — TK-120: волна a TK-115 (e114 f=1/4,3/4; g95 sw3), янв–сен, без --drop (v171b-ряд) и с --drop TRUMP/TRX/BCH (В-210) -> /data/p12r3a/closes-r3[d]-cap<N>-<мес>.json; метка psim2.done
# Запуск: p12-psim.sh <набор> (юнитом alsched, 1 ядро). Метка psim.done (r3a — psim2.done).
set -u
D=/data/tk065; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT
case ${1:?набор: r2|r2a|r3a} in
  r2)  O=/data/p12r2;  T=/data/tk083/tools;  TB=$T; KINDS="v vt"; DONE=psim.done ;;
  r2a) O=/data/p12r2a; T=/data/tk083/tools;  TB=$T; KINDS="a";    DONE=psim.done ;;
  r3a) O=/data/p12r3a; T=/data/tk0113/tools; TB=/data/tk083/tools; KINDS="a"; DONE=psim2.done ;;
  *) echo "набор: r2|r2a|r3a" >&2; exit 2 ;;
esac
SET=$1
src() {  # src <вид> <сутки> -> каталог деревьев суток волны
  case $SET/$1 in r2/v) echo $D/w-$2/b5/r2/$2 ;; r2/vt) echo $D/wt-$2/b5/r2t/$2 ;; r2a/a) echo $D/wa-$2/b5/r2a/$2 ;; r3a/a) echo $D/t15a-$2/b5/r3a/$2 ;; esac; }
variants() {  # variants <вид> -> args=(--variant ...)
  args=()
  case $SET in
    r3a) S=t-bid-btc4h-q1; B=ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800
         for f in $B-halfstopf1 $B-halfstopf3 $B-halflevelf1 $B-halflevelf3 ladder3x0..0.0409sw3-pct2-tr1x1-14400-ttl1800; do args+=(--variant "$S@$f=$S/$f"); done ;;
    *)   cells=$D/days/$SET-2026-03-10.cells; [ $SET = r2 ] && [ $1 = vt ] && cells=$D/days/r2t-2026-03-10.cells
         while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $cells ;;
  esac; }
mkdir -p $O; cd $O || exit 2; rm -f psim.done $DONE fail.txt
while read d m; do
  mo=${d:0:7}
  for kind in $KINDS; do
    # os.walk busy-replay не заходит в симлинки каталогов: каталоги настоящие, файлы — симлинки (cp -rs)
    mkdir -p s-$kind/$mo
    [ -e s-$kind/$mo/$d ] || cp -rs $(src $kind $d) s-$kind/$mo/
  done
done < $D/days/days.tsv
for mo in $(ls s-${KINDS%% *} | sort); do
  for kind in $KINDS; do
    BF=""; [ $SET = r2 ] && BF="$kind "
    [ -e $kind-b/$mo/busy-replay.txt ] || python3 $TB/busy-replay.py s-$kind/$mo $kind-b/$mo > busy-$kind-$mo.log 2>&1 || { echo "FAILBUSY $BF$mo" >> fail.txt; continue; }
    variants $kind
    for cap in 0 3; do
      for d in "" d; do
        [ -n "$d" ] && [ $SET != r3a ] && continue   # --drop — только у r3a (v171c, В-210)
        case $SET in r2) PS=$kind CS=$kind FL="$kind " ;; r2a) PS=a CS=va FL="" ;; r3a) PS=r3$d CS=r3$d FL="r3$d " ;; esac
        DR=(); [ -n "$d" ] && DR=(--drop $DROP)
        JS=(); [ $SET != r3a ] && JS=(--json psim-$PS-cap$cap-$mo.json)
        python3 $T/portfolio-sim.py --epoch "$mo=$O/$kind-b:$mo" "${args[@]}" "${DR[@]}" --deposit-usd 2500 --position-usd 500 --max-pos $cap "${JS[@]}" \
          --closes-out closes-$CS-cap$cap-$mo.json > psim-$PS-cap$cap-$mo.txt 2> psim-$PS-cap$cap-$mo.log || echo "FAIL $FL$cap $mo" >> fail.txt
      done
    done
  done
done
touch $DONE
