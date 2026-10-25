#!/usr/bin/env bash
# TK-393 (G-016) докачка 2: (а) Ethereum v2/v3 — курсор первого прогона был битый (начало>конца), берём блоки v4 26194139..26251600;
# (б) Solidly-свопы (Aerodrome v2 на Base, Velodrome v2 на Optimism): topic0 Swap(address,address,uint256×4) = 0xb3e2…f75b, данные как у v2.
# Выход /data/tk0393/sqd2/. usage: tk393-dl2.sh (на calc: /data/tk0393/{pools-new.csv,tk380-sqd-evm.py,tk380-sqd-parse.py})
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd2; T0=1792022400; T1=1792713599; SOL=0xb3e2773606abfd36b5bd91394b3a54d1398336c65005baf7bf7a05efeffaf75b
mkdir -p "$R/ethereum" "$R/base" "$R/optimism"; cd "$S" || exit 1
echo "26194139 26251600" > "$R/ethereum/cursor"
for c in base optimism; do cp sqd/$c/cursor "$R/$c/cursor"; done
head -1 pools-new.csv > pools-solidly.csv; awk -F, '$2=="base"&&($4=="aero"||$4=="aerodrome")||$2=="optimism"&&$4=="velodrome"' pools-new.csv >> pools-solidly.csv
sed "s/^SW = \[.*/SW = ['$SOL']  # solidly only/; /^      '0xc4207/d; /^      '0x19b47/d" tk380-sqd-evm.py > tk393-sqd-solidly.py
sed "s/^V2 = .*/V2 = '0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822'; SOL = '$SOL'/; s/k = 'v2' if l\['topics'\]\[0\] == V2 else 'v3'/k = 'v2' if l['topics'][0] in (V2, SOL) else 'v3'/" tk380-sqd-parse.py > tk393-sqd-parse2.py
python3 -u tk380-sqd-evm.py ethereum-mainnet pools-new.csv ethereum "$R" $T0 $T1 > "$R/ethereum.log" 2>&1 &
python3 -u tk393-sqd-solidly.py base-mainnet pools-solidly.csv base "$R" $T0 $T1 > "$R/base.log" 2>&1 &
python3 -u tk393-sqd-solidly.py optimism-mainnet pools-solidly.csv optimism "$R" $T0 $T1 > "$R/optimism.log" 2>&1 &
wait
for c in ethereum base optimism; do [ -f "$R/$c/DONE.json" ] && python3 tk393-sqd-parse2.py "$R/$c" "$R/$c-swaps" > "$R/parse-$c.log" 2>&1; done
echo '{"done": true}' > "$R/ALLDONE.json"
