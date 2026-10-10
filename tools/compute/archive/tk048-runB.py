#!/usr/bin/env python3
# tk048-runB.py <jall-скрипт суток> <NP>: строки 5..конец скрипта суток (нарезка ячеек awk) — секции между `rm -rf`, ячейки внутри секции независимы и идут NP параллельных bash; rm — после секции. Тот же набор команд, что sed -n '5,$p' | bash, быстрее хвост суток (TK-048, рычаг 3).
import subprocess, sys
J, NP = sys.argv[1], int(sys.argv[2])
L = open(J, encoding='utf-8').read().split('\n')[4:]
secs, cur = [], []
for l in L:
    if not l: continue
    if l.startswith('rm -rf'): secs.append((cur, l)); cur = []
    else: cur.append(l)
if cur: secs.append((cur, None))
for body, rm in secs:
    blocks = []
    for l in body:
        if l.startswith('mkdir -p "b5/') or not blocks: blocks.append([])
        blocks[-1].append(l)
    parts = [[] for _ in range(NP)]
    for i, b in enumerate(blocks): parts[i % NP] += b
    ps = [subprocess.Popen(['bash', '-c', '\n'.join(p) + '\n']) for p in parts if p]
    if any([p.wait() for p in ps]): sys.exit(3)
    if rm and subprocess.call(['bash', '-c', rm]): sys.exit(3)
