#!/bin/bash
# TK-525 п.5: листинг бокса с dot-файлами (ls -aR по бирже) + аудит p40-rc-audit525.py. Юнитом на srv; ssh к боксу — только ls.
O=/data/p40l1; rm -f $O/AUDIT525_DONE
for ex in okx_futures bitget_futures hyperliquid_futures binance_futures; do ssh -n box "ls -aR alpha/stakan/p40l1/$ex" > $O/boxla_$ex.txt 2>$O/boxla.err; done
nice -n 12 python3 /root/p40-rc-audit525.py
