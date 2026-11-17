# П-42 §13.16, правка 4 (TK-585 v3): для каждого сигнала калибровки — последняя запись bookTicker ≤ T_ord в окне 1 ч (не 15 с, как в entrycal_book.awk)
# и число записей `b` символа за файл. Вход: zstd -dc файла коллектора, -v SIGS=<строки "символ<TAB>T_ord">.
# Выход: "S символ T_ord T_book" и "C символ число_записей".
BEGIN {
    FS = "\t"; OFS = "\t"
    while ((getline l < SIGS) > 0) {
        split(l, a, "\t")
        c[a[1]]++
        tt[a[1], c[a[1]]] = a[2] + 0
    }
}
{
    if (NR == 1) {
        d = $2; L = length(d)
        hi = substr(d, 1, L - 9) + 0
        lo = substr(d, L - 8) + 0
    } else {
        lo += $2
        if (lo >= 1000000000) { q = int(lo / 1000000000); hi += q; lo -= q * 1000000000 }
    }
    if ($1 != "b") next
    sym = $5
    if (!(sym in c)) next
    cnt[sym]++
    T = hi * 1000 + int(lo / 1000000) - $3 - $4
    n = c[sym]
    for (i = 1; i <= n; i++) {
        k = sym SUBSEP i
        to = tt[k]
        if (T <= to && T >= to - 3600000 && (!(k in bt) || T >= bt[k])) bt[k] = T
    }
}
END {
    for (k in bt) { split(k, p, SUBSEP); print "S", p[1], tt[k], bt[k] }
    for (s in cnt) print "C", s, cnt[s]
}
