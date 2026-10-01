#!/usr/bin/env python3
# TK-020 (В-154, владелец 02.10: «удаляй лишнее»): удаление дублей по манифесту. Перед КАЖДЫМ файлом заново считаются sha256 локальной
# копии и sha256sum канона на Storage Box (сам ящик); удаляется, только если обе равны сумме манифеста. Расхождение, нет канона на
# ящике, симлинк, размер не тот, файл всё ещё указан симлинком — не удалять, в журнал. По умолчанию сухой прогон (ничего не удаляет).
#   local     --plan <csv> --allow-prefix <путь>... [--links relink-plan.csv] [--apply]   дека/коллектор: удаляет локальные дубли
#             csv: либо delete-plan-deck.csv (class,file,bytes,sha256,canon_on_box,deck_path), либо local,canon,bytes[,sha256,class]
#   box-links --plan <txt> --allow-prefix <путь>... [--apply]    ящик: удаляет ТОЛЬКО симлинки из списка (цель обязана быть симлинком)
#   box-dup   --plan <csv: dup,canon> --allow-prefix <путь>... [--apply]   ящик: удаляет дубль внутри ящика, если sha256sum обоих равны
# Журнал каждого решения: $LOG (по умолчанию ~/alpha/sync/tk020/dedupe-<режим>-<время>.csv): ts,status,class,path,bytes,sha256,canon.
import argparse, csv, hashlib, os, re, subprocess, sys, time

SBH = 'u677479@u677479.your-storagebox.de'
SSH = ['ssh', '-p', '23', '-i', os.path.expanduser('~/.ssh/id_storagebox'), '-o', 'BatchMode=yes',
       '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4', SBH]
SAFE = re.compile(r'^[A-Za-z0-9._/@+=,-]+$')
BATCH = 100
ORDER = {'e-aug': 0, 'e-archive': 1, 'deep': 2, 'root': 3}


def sb(args, tries=3):
    for i in range(tries):
        r = subprocess.run(SSH + args, capture_output=True, text=True)
        if r.returncode == 0 or r.stdout:
            return r
        time.sleep(3 * (i + 1))
    return r


def box_sums(paths):
    """canon-путь -> sha256 по sha256sum самого ящика; нет в ответе — нет в словаре (пропуск, не удаление)."""
    out = {}
    for i in range(0, len(paths), BATCH):
        chunk = paths[i:i + BATCH]
        for p in chunk:
            assert SAFE.match(p), p
        r = sb(['sha256sum'] + chunk)
        for ln in r.stdout.splitlines():
            h, _, p = ln.partition('  ')
            if len(h) == 64: out[p] = h
        for p in chunk:  # ложные «нет» при параллельных сессиях ящика (замер 02.10) — переспросить поштучно
            if p not in out:
                r = sb(['sha256sum', p])
                h, _, q = r.stdout.strip().partition('  ')
                if len(h) == 64 and q == p: out[p] = h
    return out


def local_sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(4 << 20)
            if not b: break
            h.update(b)
    return h.hexdigest()


class Journal:
    def __init__(self, mode):
        d = os.path.expanduser('~/alpha/sync/tk020') if os.path.isdir(os.path.expanduser('~/alpha/sync/tk020')) else os.getcwd()
        self.path = os.environ.get('LOG') or os.path.join(d, 'dedupe-%s-%s.csv' % (mode, time.strftime('%Y%m%dT%H%M%S')))
        self.f = open(self.path, 'w', newline='', buffering=1); self.w = csv.writer(self.f)
        self.w.writerow(['ts', 'status', 'class', 'path', 'bytes', 'sha256', 'canon'])
        self.stat = {}

    def row(self, st, cls, path, nbytes, sha, canon):
        self.w.writerow([time.strftime('%FT%T'), st, cls, path, nbytes, sha, canon])
        c = self.stat.setdefault((cls, st), [0, 0]); c[0] += 1; c[1] += int(nbytes or 0)

    def summary(self, apply):
        print('журнал:', self.path)
        for (cls, st), (n, b) in sorted(self.stat.items()):
            print('  %-10s %-16s файлов %6d  %9.2f ГБ' % (cls, st, n, b / 1e9))
        done = 'deleted' if apply else 'would-delete'
        tot = sum(b for (c, s), (n, b) in self.stat.items() if s == done)
        print('ИТОГО %s: %.2f ГБ; не удалено по причинам: %s' % (done, tot / 1e9, {s: sum(n for (c, s2), (n, b) in self.stat.items() if s2 == s)
              for s in {s for (c, s) in self.stat if s != done}}))


def load_local(path):
    rows = list(csv.DictReader(open(path, encoding='utf-8')))
    out = []
    for x in rows:
        if 'deck_path' in x:
            out.append(dict(cls=x['class'], local=os.path.expanduser(x['deck_path']), canon=x['canon_on_box'], bytes=int(x['bytes']), sha=x['sha256']))
        else:
            out.append(dict(cls=x.get('class') or 'x', local=x['local'], canon=x['canon'], bytes=int(x['bytes']), sha=x.get('sha256') or ''))
    return out


def cmd_local(a):
    ent = load_local(a.plan)
    pref = tuple(os.path.normpath(p) + '/' for p in a.allow_prefix)
    still = set()
    if a.links:
        for x in csv.DictReader(open(a.links)):
            still.add(x['old_real'])
    ent.sort(key=lambda e: (ORDER.get(e['cls'], 9), e['local']))
    J = Journal('local'); t0 = time.time(); n = 0
    for i in range(0, len(ent), BATCH):
        chunk = ent[i:i + BATCH]; todo = []
        for e in chunk:
            p = e['local']
            if not p.startswith(pref): J.row('REFUSED-prefix', e['cls'], p, e['bytes'], e['sha'], e['canon']); continue
            if '/sb/' in p or not p.startswith('/'): J.row('REFUSED-path', e['cls'], p, e['bytes'], e['sha'], e['canon']); continue
            if not os.path.lexists(p): J.row('gone', e['cls'], p, 0, e['sha'], e['canon']); continue
            if os.path.islink(p): J.row('symlink-skip', e['cls'], p, e['bytes'], e['sha'], e['canon']); continue
            if os.path.realpath(p) in still or p in still: J.row('still-linked', e['cls'], p, e['bytes'], e['sha'], e['canon']); continue
            if os.path.getsize(p) != e['bytes']: J.row('size-mismatch', e['cls'], p, e['bytes'], e['sha'], e['canon']); continue
            todo.append(e)
        bs = box_sums([e['canon'] for e in todo])
        for e in todo:
            p = e['local']; b = bs.get(e['canon'])
            if not b: J.row('box-missing', e['cls'], p, e['bytes'], e['sha'], e['canon']); continue
            l = local_sha(p)
            if e['sha'] and l != e['sha']: J.row('MISMATCH-plan', e['cls'], p, e['bytes'], l, e['canon']); continue
            if l != b: J.row('MISMATCH-box', e['cls'], p, e['bytes'], l, e['canon']); continue
            if a.apply:
                os.remove(p); J.row('deleted', e['cls'], p, e['bytes'], l, e['canon'])
            else:
                J.row('would-delete', e['cls'], p, e['bytes'], l, e['canon'])
        n += len(chunk); print('%d/%d  %.0f с' % (n, len(ent), time.time() - t0), flush=True)
    J.summary(a.apply)
    os.system('df -h %s | tail -1' % os.path.dirname(pref[0].rstrip('/')))


def cmd_box_links(a):
    pref = tuple(a.allow_prefix); J = Journal('box-links')
    paths = [ln.strip() for ln in open(a.plan, encoding='utf-8') if ln.strip()]
    for p in paths:
        if not p.startswith(pref) or not SAFE.match(p): J.row('REFUSED-prefix', 'link', p, 0, '', ''); continue
        r = sb(['ls', '-ld', p]); ln = r.stdout.strip()
        if not ln: J.row('gone', 'link', p, 0, '', ''); continue
        if not ln.startswith('l'): J.row('NOT-A-SYMLINK-skip', 'link', p, 0, '', ln[:60]); continue
        tgt = ln.split(' -> ', 1)[1] if ' -> ' in ln else ''
        if a.apply:
            sb(['rm', p]); g = sb(['ls', '-ld', p]).stdout.strip()
            J.row('deleted' if not g else 'DELETE-FAILED', 'link', p, 0, '', tgt)
        else:
            J.row('would-delete', 'link', p, 0, '', tgt)
    J.summary(a.apply)


def cmd_box_dup(a):
    pref = tuple(a.allow_prefix); J = Journal('box-dup')
    pairs = [(x['dup'], x['canon']) for x in csv.DictReader(open(a.plan, encoding='utf-8'))]
    for i in range(0, len(pairs), BATCH // 2):
        chunk = pairs[i:i + BATCH // 2]
        ok = [(d, c) for d, c in chunk if d.startswith(pref) and SAFE.match(d) and SAFE.match(c) and d != c]
        for d, c in chunk:
            if (d, c) not in ok: J.row('REFUSED', 'dup', d, 0, '', c)
        bs = box_sums([p for dc in ok for p in dc])
        for d, c in ok:
            sd, sc = bs.get(d), bs.get(c)
            if not sd or not sc: J.row('box-missing', 'dup', d, 0, sd or '', c); continue
            if sd != sc: J.row('MISMATCH-box', 'dup', d, 0, sd, c); continue
            if a.apply:
                sb(['rm', d]); g = sb(['ls', d]).stdout.strip()
                J.row('deleted' if not g else 'DELETE-FAILED', 'dup', d, 0, sd, c)
            else:
                J.row('would-delete', 'dup', d, 0, sd, c)
    J.summary(a.apply)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['local', 'box-links', 'box-dup'])
    ap.add_argument('--plan', required=True); ap.add_argument('--allow-prefix', nargs='+', required=True)
    ap.add_argument('--links'); ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    {'local': cmd_local, 'box-links': cmd_box_links, 'box-dup': cmd_box_dup}[a.mode](a)
