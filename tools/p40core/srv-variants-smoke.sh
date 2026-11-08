#!/bin/bash
# TK-500: списки за один проход: P40_VARIANTS (L:T_life) и P40_THETAS; DOGE 17.09 Binance x OKX. Сверка: вариант 500:60000 = одиночный прогон (байт в байт), L=2000 = одиночный прогон с L=2000, θ-подмножество = строки полного прогона.
export P40_NOP=1
R=/root/p40core/rs/target/release/p40core
A="/dev/shm/p40/A /dev/shm/p40/B /dev/shm/p40/TA /dev/shm/p40/TB 12300 12.3"
nice -n 19 $R /tmp/v_one500.csv $A 500 2>&1 | tail -1
nice -n 19 $R /tmp/v_one2000.csv $A 2000 2>&1 | tail -1
P40_VARIANTS=500:60000,2000:60000 nice -n 19 $R /tmp/v_multi.csv $A 500 2>&1 | tail -1
for f in .fills.csv .exits.csv; do
  cmp /tmp/v_one500.csv$f /tmp/v_multi.csv.v500_60000$f && echo "v500 байт в байт: $f"
  cmp /tmp/v_one2000.csv$f /tmp/v_multi.csv.v2000_60000$f && echo "v2000 байт в байт: $f"
done
for f in "" .path.csv .features.csv; do cmp /tmp/v_one500.csv$f /tmp/v_multi.csv$f && echo "общие таблицы байт в байт: $f"; done
P40_THETAS=12.6,100 nice -n 19 $R /tmp/v_th.csv $A 500 2>&1 | tail -1
for f in "" .fills.csv .exits.csv .features.csv .path.csv; do
  { head -1 /tmp/v_one500.csv$f; grep -E '^(12\.6|100\.0),' /tmp/v_one500.csv$f; } | cmp - /tmp/v_th.csv$f && echo "θ-подмножество байт в байт: $f"
done
