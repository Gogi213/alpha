import csv,glob,os,collections
os.chdir(os.path.expanduser('~/alpha/tmp-t38/g14/weat'))
tot=collections.Counter(); bad=collections.Counter()
for f in sorted(glob.glob('*/rounds.csv')):
    rows=[r for r in csv.reader(l for l in open(f) if not l.startswith('#'))]
    h=rows[0]; ix={k:i for i,k in enumerate(h)}
    by=collections.defaultdict(dict)
    for r in rows[1:]:
        form=r[ix['form']]; base=form.split('-weat')[0]; w=form[len(base)+1:] or 'base'
        by[(r[ix['symbol']],r[ix['signal_index']],base)][w]=r
    def rest(r): return [x for i,x in enumerate(r) if i!=ix['form']]
    for k,d in by.items():
        b=d.get('base')
        if b is None: bad['nobase']+=1; continue
        for w,r in d.items():
            tot[w]+=1
            if r[ix['reason']].startswith('wall_eat'): tot[w+':eat']+=1; tot[w+':'+r[ix['reason']]]+=1
            for c in ('t0_ns','entry_px','entry_vwap','qty'):
                if r[ix[c]]!=b[ix[c]]: bad['entry≠base '+w]+=1
            if not r[ix['reason']].startswith('wall_eat') and rest(r)!=rest(b): bad['noeat≠base '+w]+=1
        a,m,l=d.get('weat50s60a5'),d.get('weat50s60m5'),d.get('weat50s60l5')
        if a and m and l:
            ea,em,el=(int(x[ix['exit_ns']]) for x in (a,m,l))
            if ea!=min(em,el): bad['a≠min(m,l)']+=1
            ra=a[ix['reason']]
            if ra=='wall_eat_btc' and rest(a)!=rest(m): bad['a_btc≠m']+=1
            if ra=='wall_eat_local' and rest(a)!=rest(l): bad['a_loc≠l']+=1
            if m[ix['reason']]=='wall_eat_local' or l[ix['reason']]=='wall_eat_btc': bad['wrong reason']+=1
        a20=d.get('weat20s60a5')
        if a and a20 and int(a20[ix['exit_ns']])>int(a[ix['exit_ns']]): bad['a20>a50']+=1
print('checked',dict(tot)); print('bad',dict(bad))
