# П-42 §13.16, калибровка: для каждого сигнала (символ, T_ord мс UTC) — последняя запись bookTicker коллектора со временем T_book ≤ T_ord
# (не старше 15 с). Вход: поток строк файла коллектора (zstd -dc), -v SIGS=<файл строк "символ<TAB>T_ord">.
# Формат строки `b`: тип, dns (нс после предыдущей записи файла; первая — абсолютное нс UTC), lag (приём_мс − E), ET (E − T), символ, du,
# bid, bsz, ask, asz (docs/collector-ws-format.md). Время приёма набирается точно (секунды и нс отдельно), T_book = приём_мс − lag − ET.
# Выход: "символ T_ord T_book bid bsz ask asz" — по сигналу, у которого запись нашлась.
BEGIN {
    FS = "\t"
    OFS = "\t"
    while ((getline l < SIGS) > 0) {
        split(l, a, "\t")
        c[a[1]]++
        tt[a[1], c[a[1]]] = a[2] + 0
    }
}
{
    if (NR == 1) {
        d = $2
        L = length(d)
        hi = substr(d, 1, L - 9) + 0
        lo = substr(d, L - 8) + 0
    } else {
        lo += $2
        if (lo >= 1000000000) {
            q = int(lo / 1000000000)
            hi += q
            lo -= q * 1000000000
        }
    }
    if ($1 != "b") next
    sym = $5
    if (!(sym in c)) next
    T = hi * 1000 + int(lo / 1000000) - $3 - $4
    n = c[sym]
    for (i = 1; i <= n; i++) {
        k = sym SUBSEP i
        to = tt[k]
        if (T <= to && T >= to - 15000) {
            if (!(k in bt) || T >= bt[k]) {
                bt[k] = T
                bb[k] = $7
                bq[k] = $8
                ba[k] = $9
                bs[k] = $10
            }
        }
    }
}
END {
    for (k in bt) {
        split(k, p, SUBSEP)
        print p[1], tt[k], bt[k], bb[k], bq[k], ba[k], bs[k]
    }
}
