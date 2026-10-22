#!/usr/bin/env bash
# TK-212: проверка merge-packs.py на готовых пачках суток 04-20 против A-base (полный проход)
cd /data/tk0212; D=2026-04-20; rm -rf mrg; python3 merge-packs.py out/E-std/out/$D out/K-tape/out/$D mrg || { echo MERGE_FAIL; exit 1; }
python3 - <<EOF
import os
A="out/A-base/out/$D"; M="mrg"; bad=0; n=0
for p,_,fs in os.walk(A):
    for f in fs:
        if f in ("manifest.txt","manifest-tape.txt"): continue
        r=os.path.relpath(os.path.join(p,f),A); n+=1; x=os.path.join(M,r)
        if not os.path.exists(x): print("нет",r); bad+=1; continue
        if not f.endswith(".csv"):
            bad+= open(x,"rb").read()!=open(os.path.join(A,r),"rb").read(); continue
        la=[l for l in open(os.path.join(A,r),encoding="utf-8") if not l.startswith("#")]; lm=[l for l in open(x,encoding="utf-8") if not l.startswith("#")]
        if f=="rounds.csv":
            ca=sorted(",".join(l.rstrip("\n").split(",")[:16]) for l in la); cm=sorted(",".join(l.rstrip("\n").split(",")[:16]) for l in lm)
            if ca!=cm: print("rounds differ",r,len(ca),len(cm)); bad+=1
        elif sorted(la)!=sorted(lm): print("differ",r,len(la),len(lm)); bad+=1
print("files",n,"bad",bad)
EOF
touch /data/tk0212/mg.done
