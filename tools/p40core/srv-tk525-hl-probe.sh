#!/bin/bash
# TK-525 п.1: HL (книга из одних снимков) — без P40_L1_SNAPSEQ L1 = 0 часов (rc=3 при P40_L1_STRICT=1), с ним — 24. Вход: /data/p40rc-probe/hl (TAO 2026-10-05, 24 ч).
PQ=${PQ:-/root/pq2bin-tk525/target/release/pq2bin}; cd /data/p40rc-probe || exit 1
rm -rf s0 s1; P40_L1_STRICT=1 $PQ l1 book hl TAO_orderbook.parquet s0 1 2>&1 | tail -2; echo "без SNAPSEQ rc=${PIPESTATUS[0]} часов=$(ls s0 2>/dev/null | wc -l)"
P40_L1_STRICT=1 P40_L1_SNAPSEQ=1 $PQ l1 book hl TAO_orderbook.parquet s1 1 2>&1 | tail -2; echo "SNAPSEQ rc=${PIPESTATUS[0]} часов=$(ls s1 2>/dev/null | wc -l)"
rm -rf s2 s3; mkdir s3; P40_L1_STRICT=1 P40_L1_CARRY=1 $PQ l1 book hl TAO_orderbook.parquet s2 1 2>&1 | tail -2; echo "CARRY без SNAPSEQ rc=${PIPESTATUS[0]}"
mkdir -p empty; P40_L1_STRICT=1 $PQ l1 book empty TAO_orderbook.parquet s3 1 2>&1 | tail -1; echo "пустой вход rc=${PIPESTATUS[0]} (ожидаем 0)"
