import glob, json, sys, collections as c
a = c.defaultdict(lambda: [0, 0, 0.0, 0.0])
for f in glob.glob(sys.argv[1] + '/e2e-*.jsonl'):
    for l in open(f):
        d = json.loads(l)
        if d['t'] == 'stage':
            x = a[d['stage']]; x[0] += 1; x[1] += d['read_bytes']; x[2] += d['wall']; x[3] += d['cpu']
print('stage n read_GB wall_s cpu_s')
for k, v in sorted(a.items(), key=lambda i: -i[1][1]):
    print(k, v[0], round(v[1] / 1e9, 2), round(v[2]), round(v[3]))
