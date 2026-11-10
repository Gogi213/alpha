#!/bin/bash
# TK-522 (Судья 11.11): K1 −60 с на трёх юнитах по спискам d1naive2 (a — вне кэша L1, b — в кэше, c — крупные после b); метка out_d1naive2m/D1NAIVE2M_DONE
set -e
cd /root/p40core
OUT=/root/p40core/out_d1naive2m
mkdir -p $OUT
E="--setenv=P40_L1=1 --setenv=P40_NOTRADE=through --setenv=P40_CORE=/root/p40core-tk522o/rs/target/release/p40core --setenv=P40_L1_CACHE=/data/p40l1c"
systemd-run --unit=p40-d1naive2ma --collect $E --setenv=P40_SHM_FRAC=0.6 bash /root/p40core/naive-run522m.sh $OUT "$(cat /root/p40core/naive2_a.txt)"
systemd-run --unit=p40-d1naive2mb --collect $E --setenv=P40_SHM_FRAC=0.55 bash /root/p40core/naive-run522m.sh $OUT "$(cat /root/p40core/naive2_b.txt)"
systemd-run --unit=p40-d1naive2mc --collect $E --setenv=P40_SHM_FRAC=0.8 bash /root/p40core/naive2m-c.sh
systemd-run --unit=p40-d1naive2m-fin --collect bash -c 'sleep 60; while systemctl is-active -q p40-d1naive2ma || systemctl is-active -q p40-d1naive2mb || systemctl is-active -q p40-d1naive2mc; do sleep 30; done; date > /root/p40core/out_d1naive2m/D1NAIVE2M_DONE'
date
systemctl is-active p40-d1naive2ma p40-d1naive2mb p40-d1naive2mc p40-d1naive2m-fin
