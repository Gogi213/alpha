# Потолок способа (2)/(3): разбор тел кадров (контейнер L1 из chain61) на колонки varint-потоков, сжатие колонок zstd -9/-19 против контейнера. Аргумент — файл *.binlog.zst.
import sys, struct, subprocess, lzma
def run(a, data): return len(subprocess.run(a, input=data, capture_output=True).stdout)
p = sys.argv[1]
raw = subprocess.run(['zstd', '-d', '-c', '--long=31', p], capture_output=True).stdout
pos = 6
assert raw[pos:pos+4] and raw[6+4] in (3, 4), raw[6:12]
ver = raw[10]; pos = 6 + 25
if ver == 4:
    n = struct.unpack_from('<H', raw, pos)[0]; pos += 2 + 24 * n
cols = {k: bytearray() for k in ('ev', 'xd', 'ld', 'at', 'cn', 'pd', 'qd', 'ep')}
L = len(raw)
def uv(b, i):
    s = 0; v = 0
    while True:
        c = b[i]; i += 1; v |= (c & 127) << s
        if c < 128: return v, i
        s += 7
def take(b, i, name):
    j = i
    while b[j] >= 128: j += 1
    cols[name] += b[i:j+1]; return j + 1
nrec = 0
while pos < L:
    ln = struct.unpack_from('<I', raw, pos)[0]; pos += 4
    b = raw[pos:pos+ln]; pos += ln
    cols['ep'] += b[:8]; i = 8; n = len(b)
    while i < n:
        i = take(b, i, 'ev'); i = take(b, i, 'xd'); i = take(b, i, 'ld'); i = take(b, i, 'at')
        j = i; cnt, i2 = uv(b, i); i = take(b, i, 'cn')
        for _ in range(cnt):
            i = take(b, i, 'pd'); i = take(b, i, 'qd')
        nrec += cnt
tot = sum(len(v) for v in cols.values())
print(p, 'raw', L, 'cols', tot, 'records', nrec)
for lv in (9, 19):
    s = sum(run(['zstd', f'-{lv}', '--long=27', '-c'], bytes(v)) for v in cols.values())
    print(' colsplit zstd', lv, s)
s = sum(len(lzma.compress(bytes(v), preset=9)) for v in cols.values()); print(' colsplit xz9', s)
for k, v in cols.items(): print('  ', k, len(v), run(['zstd', '-9', '-c'], bytes(v)))
