#!/usr/bin/env python3
# TK-020: симлинки деки, смотрящие в ~/alpha/root|deep|epochs/e-aug|e-archive (копия есть на ящике, sha сверена), → ~/sb (sshfs ro)
# перед удалением дублей. Сухой прогон по умолчанию (sync/tk020/relink-plan.csv); --apply — атомарная подмена (ln в tmp + rename),
# только статус ok. Ссылки, уже ведущие в ~/sb или вне ~/alpha, отсеиваются лексически: realpath по sshfs стоит ~сотни мс на звено.
import os, sys, csv, errno, time
A = '/home/deck/alpha'; SB = '/home/deck/sb'; OUT = A + '/sync/tk020/'
apply = '--apply' in sys.argv
SRC = (A + '/root/', A + '/deep/', A + '/epochs/e-aug/root/', A + '/epochs/e-archive/root/')
def resolve(p):
    # realpath, не заходящий в ~/sb: (путь, 'ok'|'sb'|'missing'); lstat/readlink по sshfs стоят сотни мс (замер 02.10)
    p = os.path.normpath(p)
    for _ in range(40):
        parts = p.split('/')[1:]; cur = ''
        for i, c in enumerate(parts):
            nxt = cur + '/' + c
            if nxt == SB or nxt.startswith(SB + '/'): return nxt, 'sb'
            try: t = os.readlink(nxt)
            except OSError as e:
                if e.errno == errno.EINVAL: cur = nxt; continue
                return p, 'missing'
            rest = '/'.join(parts[i + 1:])
            p = os.path.normpath(os.path.join(os.path.dirname(nxt), t) + ('/' + rest if rest else '')); break
        else: return p, 'ok'
    return p, 'missing'
ok = {}
for x in csv.DictReader(open(OUT + 'delete-plan-deck.csv')):
    r, st = resolve(os.path.expanduser(x['deck_path']))
    if st == 'ok': ok[r] = x['canon_on_box']
box_dirs = {}
def on_box(path):
    d, n = os.path.split(path)
    if d not in box_dirs:
        try: box_dirs[d] = set(os.listdir(d))
        except OSError: box_dirs[d] = set()
    return n in box_dirs[d]
rows = []; stat = {}
def bump(k): stat[k] = stat.get(k, 0) + 1
def links(root):
    stack = [root]
    while stack:
        with os.scandir(stack.pop()) as it:
            for e in it:
                if e.is_symlink(): yield e.path  # is_symlink без stat: os.walk/is_dir ходили бы по sshfs за каждую ссылку
                elif e.is_dir(follow_symlinks=False): stack.append(e.path)
n = 0; t0 = time.time()
for p in links(A):
    if True:
        n += 1
        if n % 20000 == 0: print(n, 'ссылок,', int(time.time() - t0), 'с', stat, flush=True)
        t = os.readlink(p); lex = os.path.normpath(os.path.join(os.path.dirname(p), t))
        if lex.startswith(SB + '/') or lex == SB: bump('skip-sb'); continue
        if not lex.startswith(A + '/'): bump('skip-outside'); continue
        rt, rs = resolve(lex)
        if rs == 'sb': bump('skip-sb'); continue
        if not rt.startswith(SRC):
            if rs == 'missing': rows.append((p, rt, '', 'BROKEN')); bump('BROKEN')
            else: bump('skip-other')
            continue
        if rs == 'missing': rows.append((p, rt, '', 'BROKEN')); bump('BROKEN'); continue
        canon = ok.get(rt)
        if not canon: rows.append((p, rt, '', 'NO-CANON')); bump('NO-CANON'); continue
        new = SB + '/' + canon[len('alpha/'):]
        st = 'ok' if on_box(new) else 'MISSING'
        rows.append((p, rt, new, st)); bump(st)
        if apply and st == 'ok':
            tmp = p + '.relink'; os.symlink(new, tmp); os.replace(tmp, p)
with open(OUT + ('relink-applied.csv' if apply else 'relink-plan.csv'), 'w', newline='') as f:
    csv.writer(f).writerows([('link', 'old_real', 'new_target', 'status')] + rows)
print('apply' if apply else 'dry', stat)
