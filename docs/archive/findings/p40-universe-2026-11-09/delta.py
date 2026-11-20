# Дельта к скачанному (TK-509 r4): пары актив×биржа из recon (kept, статус ok/mult_diff/no_price, актив уровня A/B) минус символы, уже лежащие на боксе/srv (список orderbook за 2 часа). Единица ГБ = 10^9 байт.
import json,csv,collections
D=json.load(open('downloaded_syms.json'))
K=json.load(open('recon_kept.json'))
sz={(r['ex'],r['sym']):float(r['mb_per_day']) for r in csv.DictReader(open('probe_all_pairs_mult.csv',encoding='utf-8'))}
T=collections.defaultdict(lambda:[0,0,0.0,0.0,0]) # ex,крипта?: [контрактов, уже есть, ГБ всего, ГБ дельта, дельта контрактов]
for a,c,ex,s,st,mn,me,t in K:
    if st not in('ok','mult_diff','no_price') or t not in('A','B'): continue
    k=(ex,'крипта' if c=='крипта' else 'не-крипта'); r=T[k]
    g=sz[(ex,s)]*36/1000; have=s in set(D[ex])
    r[0]+=1; r[2]+=g
    if have: r[1]+=1
    else: r[3]+=g; r[4]+=1
print('| биржа | класс | контрактов | уже скачано | не скачано | ГБ всего | ГБ дельта (не скачано) |\n|---|---|---|---|---|---|---|')
for k in sorted(T): r=T[k]; print(f'| {k[0]} | {k[1]} | {r[0]} | {r[1]} | {r[4]} | {r[2]:.0f} | {r[3]:.0f} |')
tot=[sum(T[k][i] for k in T) for i in range(5)]; print(f'| итого | | {tot[0]} | {tot[1]} | {tot[4]} | {tot[2]:.0f} | {tot[3]:.0f} |')
nc=[sum(T[k][i] for k in T if k[1]=='не-крипта') for i in range(5)]; print(f'\nне-крипта: контрактов {nc[0]}, не скачано {nc[4]}, ГБ дельта {nc[3]:.0f}')
