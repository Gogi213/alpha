#!/usr/bin/env python3
"""Расход токенов Claude Code по проекту alpha (В-158): python -X utf8 tools/token-usage.py [дней=7]

Источник — транскрипты ~/.claude/projects/*alpha*/ (+ subagents). Единица — «эффективные токены»:
вход ×1 + запись кеша ×1,25 + чтение кеша ×0,1 + вывод ×5, Opus ×1 / Sonnet ×0,6 / Haiku ×0,2 (соотношение цен API).
Роли — по названию треда bb (worktree thr-…); сессии основной папки — координатор. Инструменты — оценка:
символы результата / 3 × (1,25 + 0,1 × число следующих шагов до сжатия контекста).
"""
import datetime
import shutil
import subprocess
import json, os, sys, glob, collections, re
root = os.path.expanduser('~/.claude/projects')
out = []
seen = set()
for proj in os.listdir(root):
    if 'alpha' not in proj: continue
    pdir = os.path.join(root, proj)
    files = glob.glob(os.path.join(pdir, '*.jsonl')) + glob.glob(os.path.join(pdir, '*', 'subagents', '*.jsonl'))
    for f in files:
        sess = os.path.basename(f)[:-6]
        sub = 'subagents' in f
        title = None; first_user = None
        turns = []  # per assistant msg
        tool_names = {}  # tool_use_id -> name
        pending = collections.Counter()  # chars of tool results by name since last assistant msg
        try:
            fh = open(f, encoding='utf-8', errors='replace')
        except Exception: continue
        for line in fh:
            try: d = json.loads(line)
            except Exception: continue
            t = d.get('type')
            if t in ('custom-title',): title = d.get('customTitle')
            elif t == 'agent-name' and not title: title = d.get('agentName')
            elif t == 'user':
                c = d.get('message', {}).get('content')
                if isinstance(c, str):
                    if first_user is None and not d.get('isMeta'): first_user = c[:300]
                    pending['__user_text'] += len(c)
                elif isinstance(c, list):
                    for b in c:
                        if b.get('type') == 'tool_result':
                            cc = b.get('content')
                            if isinstance(cc, str): n = len(cc)
                            elif isinstance(cc, list):
                                n = 0
                                for x in cc:
                                    if x.get('type') == 'image': n += 4500
                                    else: n += len(x.get('text', '') or '')
                            else: n = 0
                            nm0 = tool_names.get(b.get('tool_use_id'), '?')
                            pending[nm0] += n
                            continue
                        elif b.get('type') == 'text':
                            if first_user is None: first_user = b.get('text', '')[:300]
                            pending['__user_text'] += len(b.get('text', ''))
            elif t == 'attachment' or t == 'system':
                pass
            elif t == 'assistant':
                m = d.get('message', {}); u = m.get('usage')
                for b in m.get('content', []) or []:
                    if isinstance(b, dict) and b.get('type') == 'tool_use':
                        nm = b.get('name')
                        if nm == 'Bash' or nm == 'PowerShell':
                            cmd = (b.get('input') or {}).get('command', '')
                            cmd2 = re.sub(r'^\s*cd\s+("[^"]*"|\S+)\s*(&&|;)\s*', '', cmd)
                            w = cmd2.strip().split()
                            k = 'ssh' if 'ssh ' in cmd else (' '.join(w[:2]) if w and w[0] in ('bb','git','python','bash','gh') else (w[0] if w else ''))
                            nm = nm + ':' + k[:30]
                        elif nm == 'Agent':
                            nm = 'Agent:' + str((b.get('input') or {}).get('subagent_type'))
                        elif nm == 'Read':
                            p = str((b.get('input') or {}).get('file_path', ''))
                            ext = p.rsplit('.',1)[-1].lower() if '.' in p[-6:] else ''
                            nm = 'Read:' + ('img' if ext in ('png','jpg','jpeg','gif','webp') else 'pdf' if ext=='pdf' else 'jsonl/log' if ext in ('jsonl','log','txt','json','csv') else ext[:6])
                        tool_names[b.get('id')] = nm
                if not u: continue
                key = (m.get('id'), d.get('requestId'))
                if key in seen: 
                    continue
                seen.add(key)
                turns.append(dict(ts=d.get('timestamp', '')[:10], model=m.get('model'), i=u.get('input_tokens', 0) or 0,
                    cw=u.get('cache_creation_input_tokens', 0) or 0, cr=u.get('cache_read_input_tokens', 0) or 0,
                    o=u.get('output_tokens', 0) or 0, pend=dict(pending)))
                pending = collections.Counter()
        if turns:
            out.append(dict(proj=proj, sess=sess, sub=sub, title=title, first=first_user, turns=turns))

S = out
since = (datetime.date.today() - datetime.timedelta(days=int(sys.argv[1]) if len(sys.argv) > 1 else 7)).isoformat()

W = dict(i=1, cw=1.25, cr=0.1, o=5)  # relative price weights per model-equivalent
MW = {'opus':1.0, 'sonnet':0.6, 'haiku':0.2}
def mw(m):
    for k,v in MW.items():
        if m and k in m: return v
    return 0
try:
    TH = json.loads(subprocess.run([shutil.which('bb') or 'bb','thread','list','--include-archived','--include-hidden','--json'],capture_output=True,text=True,encoding='utf-8').stdout or '[]')
except OSError:
    TH = []
TH = TH.get('threads', TH) if isinstance(TH, dict) else TH
TT = {t['id']: t.get('title') or '' for t in TH}
def role(s):
    m = re.search(r'thr-([a-z0-9]{10})', s['proj'])
    if m:
        t = TT.get('thr_'+m.group(1), '')
        for k in ['исп','пров','чит']:
            if t.startswith(k): return ('sub:' if s['sub'] else '')+k
        return 'thr-other'
    f0 = (s['first'] or '')
    if not s['sub'] and ('Brief' in f0[:40] or f0.startswith('Executor') or f0.startswith('Reviewer') or f0.startswith('Reader')): return 'child-in-checkout'
    if not s['sub']: return 'коорд(checkout)'
    t = (s['title'] or '')
    f = (s['first'] or '')
    if s['sub']: return 'subagent'
    for k in ['коорд','исп','пров','чит']:
        if t.startswith(k): return k
    if t: return 'old:'+t.split()[0][:12]
    if 'bb message' in f or 'Brief' in f or 'TK-' in f: return 'bb-untitled'
    return 'untitled'

agg = lambda: collections.defaultdict(float)
tot = collections.Counter(); byrole = collections.defaultdict(collections.Counter); bymodel = collections.defaultdict(collections.Counter)
byday = collections.defaultdict(collections.Counter); ctx = collections.Counter(); ctxw = collections.Counter()
tool = collections.Counter(); toolcalls = collections.Counter()
sess_rank = []
for s in S:
    r = role(s)
    T = [t for t in s['turns'] if t['ts'] >= since]
    if not T: continue
    sc = collections.Counter()
    turns = s['turns']
    # tool attribution: chars of result * remaining turns until context drop
    n = len(turns)
    for idx,t in enumerate(turns):
        if t['ts'] < since: continue
        w = mw(t['model'])
        c = collections.Counter(i=t['i'], cw=t['cw'], cr=t['cr'], o=t['o'])
        c['wt'] = w*(t['i']*W['i']+t['cw']*W['cw']+t['cr']*W['cr']+t['o']*W['o'])
        c['turns'] = 1
        tot.update(c); byrole[r].update(c); bymodel[(t['model'] or '?')].update(c); byday[t['ts']].update(c); sc.update(c)
        size = t['i']+t['cw']+t['cr']
        b = '<50k' if size<50e3 else '50-100k' if size<100e3 else '100-200k' if size<200e3 else '200-400k' if size<400e3 else '400k+'
        ctx[b]+=1; ctxw[b]+=c['wt']
        # remaining life of this turn's new content
        life = 0; prev = size
        for j in range(idx+1, n):
            sz = turns[j]['i']+turns[j]['cw']+turns[j]['cr']
            if sz < prev*0.6: break
            life += 1; prev = sz
        for k,v in t['pend'].items():
            tok = v/3.0
            # first written (cw 1.25) then read life times at 0.1
            tool[k] += w*tok*(1.25 + 0.1*life); toolcalls[k]+=1
    sess_rank.append((sc['wt'], r, s['title'], (s['first'] or '')[:90].replace('\n',' '), sc['turns'], s['sess']))
def pr(name, d, key='wt', top=25):
    print('\n==', name)
    tw = tot['wt']
    for k,c in sorted(d.items(), key=lambda x:-x[1][key])[:top]:
        print(f"{str(k)[:40]:40s} wt%={100*c['wt']/tw:5.1f} turns={int(c['turns']):6d} in={c['i']/1e6:7.2f}M cw={c['cw']/1e6:7.1f}M cr={c['cr']/1e6:8.1f}M out={c['o']/1e6:6.2f}M ctx/turn={(c['i']+c['cw']+c['cr'])/max(c['turns'],1)/1e3:5.0f}k")
print('TOTAL', {k:(round(v/1e6,1)) for k,v in tot.items()})
tw=tot['wt']
#: in %.1f cw %.1f cr %.1f out %.1f' % tuple(100*sum(mw(None) for _ in [])+100*x/tw for x in [0,0,0,0]))
pr('role', byrole); pr('model', bymodel)
print('\n== context size per turn: turns, wt%'); [print(b, ctx[b], round(100*ctxw[b]/tw,1)) for b in ['<50k','50-100k','100-200k','200-400k','400k+']]
print('\n== tool results (est weighted share of total)')
for k,v in tool.most_common(30): print(f"{k[:45]:45s} {100*v/tw:5.1f}% calls={toolcalls[k]}")
print('\n== days'); [print(d, round(c['wt']/1e6,1), int(c['turns'])) for d,c in sorted(byday.items())[-14:]]
print('\n== top sessions')
for x in sorted(sess_rank, key=lambda x: -x[0])[:30]: print(f"{100*x[0]/tw:5.1f}% {x[1]:10s} {str(x[2])[:45]:45s} t={x[4]} | {x[3]}")
