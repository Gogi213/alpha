#!/usr/bin/env python3
# TK-026: замеры чтения Storage Box со Steam Deck. ТОЛЬКО ЧТЕНИЕ ящика; пишет лишь в /dev/shm/tk026 и ~/alpha/sync/tk026.
#   systemd-run --user --working-directory=$HOME/alpha python3 tk026-bench.py [net|fs|ssh|sftp|mnt|all]
# Каждый тест — окно W секунд (по умолчанию 20) на файлах ящика, байты/время → строка CSV.
import os, sys, time, subprocess, threading, shutil, signal, zlib, csv
HOME = os.path.expanduser('~')
BOX = 'u677479@u677479.your-storagebox.de'
KEY = f'{HOME}/.ssh/id_storagebox'
SSHO = ['-p', '23', '-i', KEY, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']
SSH = ['ssh', '-n'] + SSHO
W = float(os.environ.get('W', '20'))
OUT = f'{HOME}/alpha/sync/tk026'
SHM = '/dev/shm/tk026'
os.makedirs(OUT, exist_ok=True)
STAMP = time.strftime('%Y%m%d-%H%M%S')
CSVF = open(f'{OUT}/bench-{STAMP}.csv', 'w', newline='')
CW = csv.writer(CSVF, lineterminator='\n')
CW.writerow(['test', 'desc', 'n', 'bytes', 'secs', 'MB_per_s', 'note'])


def rec(test, desc, n, nbytes, secs, note=''):
    mbs = nbytes / 1e6 / secs if secs > 0 else 0
    CW.writerow([test, desc, n, nbytes, f'{secs:.2f}', f'{mbs:.2f}', note])
    CSVF.flush()
    print(f'{test:8} n={n:<3} {mbs:8.2f} MB/s  {nbytes/1e6:8.1f} MB / {secs:5.1f}s  {desc} {note}', flush=True)


def sh(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# --- файлы ящика (крупнейшие в alpha/root) -------------------------------------------------
lst = sh(SSH + [BOX, 'ls -lS alpha/root']).stdout.splitlines()
FILES = []
for ln in lst:
    p = ln.split()
    if len(p) >= 9 and p[4].isdigit() and p[8].endswith('.binlog') and int(p[4]) >= 100_000_000:
        FILES.append((p[8], int(p[4])))
print('крупных файлов:', len(FILES), FILES[:3], flush=True)
_ix = [0]


def next_file():
    f = FILES[_ix[0] % len(FILES)]
    _ix[0] += 1
    return f


def run_readers(readers, w=None):
    w = w or W
    t0 = time.time()
    dl = t0 + w
    res = [0] * len(readers)
    fin = [t0] * len(readers)

    def run(i):
        res[i] = readers[i](dl)
        fin[i] = time.time()
    ths = [threading.Thread(target=run, args=(i,)) for i in range(len(readers))]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return sum(res), max(max(fin) - t0, 0.001)


def rd_fs(path, off, bs=4 << 20):
    def f(dl):
        n = 0
        fd = os.open(path, os.O_RDONLY)
        try:
            os.lseek(fd, off, 0)
            while time.time() < dl:
                b = os.read(fd, bs)
                if not b:
                    break
                n += len(b)
        finally:
            os.close(fd)
        return n
    return f


def killgrp(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except Exception:
        pass
    p.wait()


def rd_cmd(cmd):
    def f(dl):
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        n = 0
        while time.time() < dl:
            b = p.stdout.read(1 << 20)
            if not b:
                break
            n += len(b)
        killgrp(p)
        return n
    return f


def rd_dest(cmd, dest):
    # sftp/scp/rsync пишут в dest (tmpfs); байты = размер каталога; окно — W
    def f(dl):
        shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(dest)
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        while time.time() < dl and p.poll() is None:
            time.sleep(0.25)
        killgrp(p)
        n = 0
        for r, _, fs in os.walk(dest):
            for x in fs:
                n += os.path.getsize(os.path.join(r, x))
        shutil.rmtree(dest, ignore_errors=True)
        return n
    return f


def dd_cmd(path, skip_mb, count_mb, extra=()):
    return list(SSH) + list(extra) + [BOX, f'dd if=alpha/root/{path} bs=1M skip={skip_mb} count={count_mb} status=none']


# --- сеть ------------------------------------------------------------------------------------
def t_net():
    for host, name in (('192.168.1.1', 'gateway'), ('u677479.your-storagebox.de', 'box'), ('fsn1-speed.hetzner.com', 'fsn1-speed')):
        r = sh(['ping', '-c', '15', '-i', '0.3', '-q', host]).stdout.strip().splitlines()
        print('ping', name, r[-1] if r else '?', flush=True)
        CW.writerow(['ping', name, 15, 0, 0, 0, r[-1] if r else '?'])
    CSVF.flush()
    for n in (1, 4, 8):
        def cu(dl):
            p = sh(['curl', '-s', '-o', '/dev/null', '-m', str(int(W)), '-w', '%{size_download}', 'https://fsn1-speed.hetzner.com/1GB.bin'], timeout=W + 20)
            try:
                return int(p.stdout.strip() or 0)
            except ValueError:
                return 0
        b, el = run_readers([cu] * n, W + 1)
        rec('curl', 'HTTPS fsn1-speed.hetzner.com/1GB.bin (ширина домашнего канала)', n, b, W)


# --- sshfs, как сейчас (~/sb: ro, max_conns=4, kernel_cache) ----------------------------------
def t_fs(mnt=f'{HOME}/sb', tag='sshfs-now', ns=(1, 1, 2, 4, 8, 16), bs=4 << 20, desc='sshfs как сейчас'):
    for n in ns:
        rs = []
        for _ in range(n):
            fn, sz = next_file()
            rs.append(rd_fs(f'{mnt}/root/{fn}', (sz // 3) // (4 << 20) * (4 << 20), bs))
        b, el = run_readers(rs)
        rec(tag, f'{desc}, разные файлы', n, b, el)


def t_fs_ranges(mnt, tag, ns, desc):
    fn, sz = FILES[0]
    for n in ns:
        rs = [rd_fs(f'{mnt}/root/{fn}', (sz // n) * i // (4 << 20) * (4 << 20)) for i in range(n)]
        b, el = run_readers(rs)
        rec(tag, f'{desc}, куски одного файла', n, b, el)


# --- ssh dd: поток через канал ssh (каждый — своё соединение) -----------------------------------
def t_ssh():
    fn, sz = FILES[0]
    mb = sz // (1 << 20)
    # снимок TCP-состояния во время одного потока
    snap = []

    def snapper():
        time.sleep(8)
        snap.append(sh(['ss', '-tin', 'dst', '88.99.48.227']).stdout)
    th = threading.Thread(target=snapper)
    th.start()
    b, el = run_readers([rd_cmd(dd_cmd(fn, 0, mb))])
    th.join()
    rec('ssh-dd', 'ssh box dd, 1 поток, шифр по умолчанию', 1, b, el)
    open(f'{OUT}/ss-snapshot-{STAMP}.txt', 'w').write(snap[0] if snap else '')
    for cipher in ('aes128-gcm@openssh.com', 'chacha20-poly1305@openssh.com', 'aes128-ctr'):
        b, el = run_readers([rd_cmd(dd_cmd(fn, 0, mb, ['-c', cipher]))])
        rec('ssh-dd', f'ssh box dd, 1 поток, шифр {cipher}', 1, b, el)
    b, el = run_readers([rd_cmd(dd_cmd(fn, 0, mb, ['-o', 'Compression=yes']))])
    rec('ssh-dd', 'ssh box dd, 1 поток, Compression=yes (zlib)', 1, b, el)
    for n in (2, 4, 8, 16):
        rs = [rd_cmd(dd_cmd(fn, (mb // n) * i, mb // n, ['-c', 'aes128-gcm@openssh.com'])) for i in range(n)]
        b, el = run_readers(rs)
        rec('ssh-dd', 'ssh box dd, куски одного файла, aes128-gcm', n, b, el, 'ошибки соединений — по числу байт')
    for n in (4, 8):
        rs = []
        for _ in range(n):
            f2, s2 = next_file()
            rs.append(rd_cmd(dd_cmd(f2, 0, s2 // (1 << 20), ['-c', 'aes128-gcm@openssh.com'])))
        b, el = run_readers(rs)
        rec('ssh-dd', 'ssh box dd, разные файлы целиком, aes128-gcm', n, b, el)


# --- sftp / scp / rsync в tmpfs ----------------------------------------------------------------
def t_sftp():
    os.makedirs(SHM, exist_ok=True)
    fn, sz = FILES[1]
    src = f'{BOX}:alpha/root/{fn}'
    d = f'{SHM}/a'
    base = ['sftp', '-P', '23', '-i', KEY, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']
    for R, B, extra, label in ((64, 32768, [], 'sftp по умолчанию (-R64 -B32K)'),
                               (256, 32768, [], 'sftp -R256 -B32K'),
                               (64, 131072, [], 'sftp -R64 -B128K'),
                               (256, 131072, [], 'sftp -R256 -B128K'),
                               (256, 131072, ['-o', 'Ciphers=aes128-gcm@openssh.com'], 'sftp -R256 -B128K aes128-gcm')):
        cmd = base + ['-R', str(R), '-B', str(B)] + extra + [src, d + '/x']
        b, el = run_readers([rd_dest(cmd, d)], W + 5)
        rec('sftp', label, 1, b, min(el, W + 5))
    cmd = ['scp', '-P', '23', '-i', KEY, '-o', 'BatchMode=yes', src, d + '/x']
    b, el = run_readers([rd_dest(cmd, d)], W + 5)
    rec('scp', 'scp (протокол sftp, OpenSSH 10)', 1, b, el)
    cmd = ['scp', '-O', '-P', '23', '-i', KEY, '-o', 'BatchMode=yes', src, d + '/x']
    b, el = run_readers([rd_dest(cmd, d)], W + 5)
    rec('scp', 'scp -O (старый протокол)', 1, b, el)
    cmd = ['rsync', '-e', f'ssh -p 23 -i {KEY} -o BatchMode=yes', '--inplace', f'{BOX}:alpha/root/{fn}', d + '/']
    b, el = run_readers([rd_dest(cmd, d)], W + 5)
    rec('rsync', 'rsync по ssh, один файл', 1, b, el)
    cmd = ['rsync', '-e', f'ssh -p 23 -i {KEY} -o BatchMode=yes -c aes128-gcm@openssh.com', '--inplace', f'{BOX}:alpha/root/{fn}', d + '/']
    b, el = run_readers([rd_dest(cmd, d)], W + 5)
    rec('rsync', 'rsync по ssh, aes128-gcm', 1, b, el)
    for n in (4, 8):
        rs = []
        for i in range(n):
            f2, _ = next_file()
            di = f'{SHM}/p{i}'
            rs.append(rd_dest(base + ['-R', '256', '-B', '131072', f'{BOX}:alpha/root/{f2}', di + '/x'], di))
        b, el = run_readers(rs, W + 5)
        rec('sftp', f'sftp -R256 -B128K, {n} параллельно разными файлами', n, b, W + 5 if el > W + 4 else el)
    shutil.rmtree(SHM, ignore_errors=True)


# --- сжимаемость бинлога -------------------------------------------------------------------------
def t_compress():
    fn, sz = FILES[2]
    out = []
    for skip in (0, 100, 200):
        p = subprocess.run(dd_cmd(fn, skip, 8, ['-c', 'aes128-gcm@openssh.com']), capture_output=True, timeout=60)
        out.append(p.stdout)
    raw = b''.join(out)
    for lv in (1, 3, 6):
        c = len(zlib.compress(raw, lv))
        CW.writerow(['compress', f'zlib-{lv} на {len(raw)>>20} МБ бинлога {fn}', 0, len(raw), 0, 0, f'ratio={len(raw)/c:.2f}'])
        print(f'compress zlib-{lv}: ratio {len(raw)/c:.2f}', flush=True)
    CSVF.flush()


# --- sshfs-варианты (отдельные точки монтирования, только чтение) --------------------------------
def mount(tag, opts):
    mnt = f'{HOME}/sbt-{tag}'
    os.makedirs(mnt, exist_ok=True)
    r = sh(['sshfs', f'{BOX}:alpha', mnt, '-o', 'ro,port=23,IdentityFile=' + KEY + ',BatchMode=yes,ServerAliveInterval=15,' + opts])
    if r.returncode:
        print('mount fail', tag, r.stderr, flush=True)
        return None
    return mnt


def umount(mnt):
    sh(['fusermount3', '-u', mnt])
    try:
        os.rmdir(mnt)
    except OSError:
        pass


def t_mnt():
    variants = [
        ('a', 'Ciphers=aes128-gcm@openssh.com,max_conns=1', (1,), 'sshfs aes128-gcm, 1 соед.'),
        ('b', 'Ciphers=aes128-gcm@openssh.com,max_conns=8,kernel_cache', (1, 8), 'sshfs aes128-gcm, max_conns=8'),
        ('c', 'Ciphers=aes128-gcm@openssh.com,max_conns=8,direct_io', (1, 4), 'sshfs aes128-gcm, max_conns=8, direct_io'),
        ('d', 'Ciphers=aes128-gcm@openssh.com,max_conns=8,max_read=1048576,kernel_cache', (1,), 'sshfs max_read=1M, max_conns=8'),
    ]
    for tag, opts, ns, desc in variants:
        m = mount(tag, opts)
        if not m:
            continue
        try:
            t_fs(m, 'sshfs-' + tag, ns, 8 << 20 if 'direct' in opts else 4 << 20, desc)
            if tag in ('b', 'c'):
                t_fs_ranges(m, 'sshfs-' + tag, (8,), desc)
        finally:
            umount(m)


if __name__ == '__main__':
    what = sys.argv[1:] or ['all']
    print('старт', STAMP, 'loadavg', open('/proc/loadavg').read().strip(), flush=True)
    steps = {'net': t_net, 'fs': t_fs, 'ssh': t_ssh, 'sftp': t_sftp, 'compress': t_compress, 'mnt': t_mnt}
    seq = list(steps) if 'all' in what else what
    for s in seq:
        print('=====', s, time.strftime('%H:%M:%S'), flush=True)
        try:
            steps[s]()
        except Exception as e:
            print('ОШИБКА', s, repr(e), flush=True)
    print('КОНЕЦ', time.strftime('%H:%M:%S'), flush=True)
