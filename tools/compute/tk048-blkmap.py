#!/usr/bin/env python3
# TK-048 п.3: perf script (block:block_rq_issue) + filefrag-экстенты -> чтение по дискам и классам файлов. Вход: perf-вывод (stdin), список файлов (argv[1]); выход: таблица байт/запросов по (диск, класс, comm).
import re, subprocess, sys, os, bisect, collections
files = [l.strip() for l in open(sys.argv[1]) if l.strip()]
def cls(p):
    if '/approaches/D20/' in p: return 'approaches'
    if '.binlog' in p: return 'binlog'
    if '/regime/' in p: return 'regime'
    if '/sigma240' in p: return 'sigma240'
    return 'other'
part_start = {}
def pstart(path):
    src = subprocess.run(['df', '--output=source', path], capture_output=True, text=True).stdout.split('\n')[1].strip()
    b = os.path.basename(src)
    if b not in part_start:
        sf = f'/sys/class/block/{b}/start'
        part_start[b] = (int(open(sf).read()), re.sub(r'\d+$', '', b)) if os.path.exists(sf) else (0, b)
    return part_start[b]
ext = collections.defaultdict(list)
cur = None; cs = 0; disk = None
for i in range(0, len(files), 200):
    chunk = files[i:i+200]
    out = subprocess.run(['filefrag', '-e'] + chunk, capture_output=True, text=True).stdout
    for l in out.split('\n'):
        m = re.match(r'File size of (.+) is \d+', l)
        if m: cur = m.group(1); cs, disk = pstart(cur); continue
        m = re.match(r'\s*\d+:\s+\d+\.\.\s+\d+:\s+(\d+)\.\.\s+(\d+):\s+(\d+):', l)
        if m and cur: ext[disk].append((cs + int(m.group(1)) * 8, cs + (int(m.group(2)) + 1) * 8, cls(cur)))
for d in ext: ext[d].sort()
starts = {d: [e[0] for e in v] for d, v in ext.items()}
disk_of = {}
for dev in os.listdir('/sys/dev/block'):
    disk_of[dev] = os.path.basename(os.readlink('/sys/dev/block/' + dev))
agg = collections.defaultdict(lambda: [0, 0])
rx = re.compile(r'(\d+),(\d+) (\S+) (\d+) \(.*?\) (\d+) \+ (\d+)(?: \S+)? \[(.*?)\]')
for l in sys.stdin:
    m = rx.search(l)
    if not m or 'R' not in m.group(3): continue
    d = disk_of.get(m.group(1) + ':' + m.group(2), '?'); sec = int(m.group(5)); n = int(m.group(6)) * 512
    c = 'unmapped'
    if d in ext:
        k = bisect.bisect_right(starts[d], sec) - 1
        if k >= 0 and sec < ext[d][k][1]: c = ext[d][k][2]
    a = agg[(d, c, m.group(7)[:15])]; a[0] += n; a[1] += 1
print('disk class comm GB requests avgKB')
for k, v in sorted(agg.items(), key=lambda x: -x[1][0]):
    print(*k, round(v[0] / 1e9, 2), v[1], round(v[0] / v[1] / 1024))
