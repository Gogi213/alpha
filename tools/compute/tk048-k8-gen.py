#!/usr/bin/env python3
"""К-8 (TK-048): варианты скриптов суток x2/x4 — те же 241 клетка + добавки по осям П-12 (В-198).
Половина добавок — тот же вход (ladder3x0..0.0409sw2), другой стоп/тейк (A); половина — новые формы входа (B).
Использование: tk048-k8-gen.py <каталог cells-by-day> <выход> <сутки,...>; пишет <выход>/x{2,4}/jall-jan-<d>.{sh,txt}."""
import os, re, sys

src, out, days = sys.argv[1], sys.argv[2], sys.argv[3].split(',')
E0 = 'ladder3x0..0.0409sw2'
A_FORMS = [f'{E0}-{s}-{t}-14400-ttl1800' for t in ('tr1x1', 'tr1.5x1', 'tr2x1', '1to1')
           for s in ('pct2.5', 'pct4', 'pct3', 'pct1.5')]
ENTRIES = ['ladder3x0.00409..0.02045sw2', 'ladder3x0.00409..0.0818sw2', 'ladder3x0.010225..0.02045sw2',
           'ladder3x0.010225..0.0818sw2', 'ladder3x0.00409..0.0409sw2', 'ladder3x0.010225..0.0409sw2',
           'ladder3x0..0.02045sw2', 'ladder3x0..0.0818sw2']
B_FORMS = [f'{e}-pct2-tr1x1-14400-ttl1800' for e in ENTRIES]
NEED = {2: (120, 121), 4: (360, 363)}
for d in days:
    base = f'{src}/jall-jan-2026-01-{d}'
    cells = [l.rstrip('\n') for l in open(base + '.txt') if l.strip()]
    have = set(cells)
    sets = []
    for l in cells:
        s = l.split()[1]
        if s not in sets:
            sets.append(s)

    def pairs(forms):
        return [f'{f} {s}' for s in sets for f in forms if f'{f} {s}' not in have]

    PA, PB = pairs(A_FORMS), pairs(B_FORMS)
    sh = open(base + '.sh').read().split('\n')
    for k, (na, nb) in NEED.items():
        add = PA[:na] + PB[:nb]
        assert len(add) == na + nb, (len(PA), len(PB))
        os.makedirs(f'{out}/x{k}', exist_ok=True)
        txt = f'{out}/x{k}/jall-jan-2026-01-{d}.txt'
        open(txt, 'w').write('\n'.join(cells + add) + '\n')
        flags = set()
        for f in sorted({p.split()[0] for p in add}):
            m = re.match(r'(.+?)-(pct[0-9.]+)-(tr[0-9.x]+|1to1|tk[0-9.]+)-(\d+)-ttl(\d+)$', f)
            e, st, tk, dl, ttl = m.groups()
            flags |= {f'--entry-form {e}', f'--stop-form {st}', f'--take-form {tk}'}
        line = sh[2]
        for fl in sorted(flags):
            if f'{fl} ' not in line:
                line = line.replace(' --cells ', f' {fl} --cells ', 1)
        line = line.replace(base + '.txt', txt)
        rc = 'for f in b5/.cellstmp-2026-01-%s/*/rounds.csv; do echo "$f $(wc -l < $f)"; done >> %s/rounds-count-x%d.txt' % (d, out, k)
        open(f'{out}/x{k}/jall-jan-2026-01-{d}.sh', 'w').write('\n'.join(sh[:2] + [line, sh[3], rc] + sh[4:]))
        print(d, k, len(cells), '+', len(add), 'A', na, 'B', nb, 'flags', len(flags))
