// TK-402 П-28 v3.1: ядро событийной ленты step2 (Gate x Bybit). gcc -O2 -shared -fPIC -o tk402-step2-core.so tk402-step2-core.c -lm
// Лента = объединённые мс-времена ts[M] (события Gate, Bybit, границы разрывов); состояние (gb,ga,bb,ba) и ok[] — as-of на каждой метке (готовит питон).
// d[i] = лог-разница середин в бп (nan при !ok). Решение в ts[i] видит только состояние на ts[i]; опора — только по интервалам, закрытым к ts[i].
#include <stdint.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>
typedef int64_t i64;
#define DAY 86400000LL
static i64 lb(const i64 *a, i64 n, i64 x) { i64 lo = 0, hi = n; while (lo < hi) { i64 m = (lo + hi) >> 1; if (a[m] < x) lo = m + 1; else hi = m; } return lo; }
static i64 asof(const i64 *a, i64 n, i64 x) { i64 lo = 0, hi = n; while (lo < hi) { i64 m = (lo + hi) >> 1; if (a[m] <= x) lo = m + 1; else hi = m; } return lo - 1; }
// взвешенное (временем) дерево Фенвика по рангам значений d
typedef struct { double *t; int n, lg; double tot; } BIT;
static void bit_init(BIT *b, int n) { b->n = n; b->t = calloc(n + 1, sizeof(double)); b->tot = 0; b->lg = 1; while ((1 << b->lg) <= n) b->lg++; }
static void bit_add(BIT *b, int r, double w) { b->tot += w; for (int i = r + 1; i <= b->n; i += i & -i) b->t[i] += w; }
static int bit_median(BIT *b) { // наименьший ранг с префиксом >= tot/2
  double need = b->tot / 2, acc = 0; int pos = 0;
  for (int k = b->lg; k >= 0; k--) { int np = pos + (1 << k); if (np <= b->n && acc + b->t[np] < need) { pos = np; acc += b->t[np]; } }
  return pos; // 0-индексный ранг = pos
}
// kind 0: медиана по времени за окно par (мс); 1: EMA с полупериодом par (мс); 2: медиана прошлых суток UTC (фикс на сутки). out[i] = опора на метке i или nan.
// rank[i] — ранг d[i] среди ok-значений (0..nr-1), dsorted[r] — значения по рангам.
void anchor(i64 M, const i64 *ts, const double *d, const uint8_t *ok, const int32_t *rank, const double *dsorted, int nr, int kind, double par, double *out) {
  for (i64 i = 0; i < M; i++) out[i] = NAN;
  if (kind == 0) {
    BIT b; bit_init(&b, nr); i64 tail = 0;
    for (i64 i = 0; i < M; i++) {
      if (i > 0 && ok[i - 1]) bit_add(&b, rank[i - 1], (double)(ts[i] - ts[i - 1]));
      while (tail < i && ts[tail + 1] <= ts[i] - (i64)par) { if (ok[tail]) bit_add(&b, rank[tail], -(double)(ts[tail + 1] - ts[tail])); tail++; }
      if (b.tot >= 0.95 * par) out[i] = dsorted[bit_median(&b)];
    }
    free(b.t);
  } else if (kind == 1) {
    double a = 0, el = 0; int st = 0;
    for (i64 i = 0; i < M; i++) {
      if (i > 0 && ok[i - 1]) {
        double dt = (double)(ts[i] - ts[i - 1]), al = exp(-dt * 0.6931471805599453 / par);
        a = st ? a * al + d[i - 1] * (1 - al) : d[i - 1]; st = 1; el += dt;
      }
      if (st && el >= par) out[i] = a;
    }
  } else {
    BIT b; bit_init(&b, nr); i64 cur = ts[0] / DAY, first = 0; double prev = NAN; i64 prevday = -10;
    for (i64 i = 0; i < M; i++) {
      i64 dy = ts[i] / DAY;
      if (i > 0 && ok[i - 1]) bit_add(&b, rank[i - 1], (double)(ts[i] - ts[i - 1])); // интервал относится к суткам своей левой метки
      if (dy != cur) { // сутки cur закончены: фиксируем медиану
        prev = (b.tot >= 0.95 * DAY) ? dsorted[bit_median(&b)] : NAN; prevday = cur;
        for (i64 j = first; j < i; j++) if (ok[j]) bit_add(&b, rank[j], -(double)(ts[j + 1] - ts[j]));
        b.tot = 0; first = i; cur = dy;
      }
      out[i] = (prevday == dy - 1) ? prev : NAN;
    }
    free(b.t);
  }
}
static double fcum_at(const i64 *pt, const double *cs, i64 n, i64 T) { i64 k = asof(pt, n, T); return k < 0 ? 0.0 : cs[k]; } // сумма ставок выплат с временем <= T
// Клетки одного якоря. Клетка c: thr[c] — порог в бп (k*круг), Dl[c] — задержка мс, To[c] — тайм-аут мс. Клетки сгруппированы по thr (список кандидатов перестраивается).
// mode 0 — кандидаты по списку, 1 — полный перебор (гейт «байт в байт»). Выход (на клетку × NH часов): sg gross бп, fg фандинг бп, cnt, ep, cr1, cr2; dropk (4 причины) на клетку.
// funding: pg/cg (Gate), pb/cb (Bybit; nb=0 — нет ноги) — времена выплат и накопленные ставки (доля, не бп).
void cells(i64 M, const i64 *ts, const double *gb, const double *ga, const double *bb, const double *ba, const uint8_t *ok, const double *d, const double *b, const double *gbw, const double *gaw, i64 tend, i64 t0, int NH,
           int mode, int nc, const double *thr, const int32_t *Dl, const int32_t *To, int minhold,
           const i64 *pg, const double *cg, i64 ng, const i64 *pb, const double *cb, i64 nb,
           double *sg, double *fg, int32_t *cnt, int32_t *ep, int32_t *cr1, int32_t *cr2, int64_t *dropk) {
  int8_t *side = calloc(M, 1); double *ed = malloc(M * sizeof(double)); int32_t *n1 = malloc((M + 1) * 4), *n2 = malloc((M + 1) * 4); int32_t *cand = malloc(M * 4);
  for (i64 i = 0; i < M; i++) {
    ed[i] = NAN;
    if (ok[i] && isfinite(b[i]) && isfinite(d[i])) {
      double mid = (gb[i] + ga[i] + bb[i] + ba[i]) / 4, e1 = (bb[i] - ga[i]) / mid * 1e4 + b[i], e2 = (gb[i] - ba[i]) / mid * 1e4 - b[i];
      side[i] = e1 >= e2 ? 1 : 2; ed[i] = side[i] == 1 ? e1 : e2;
    }
  }
  n1[M] = n2[M] = (int32_t)M;
  for (i64 i = M - 1; i >= 0; i--) {
    double dev = (side[i] ? d[i] - b[i] : NAN);
    n1[i] = (isfinite(dev) && dev >= 0) ? (int32_t)i : n1[i + 1];
    n2[i] = (isfinite(dev) && dev <= 0) ? (int32_t)i : n2[i + 1];
  }
  double cur_thr = NAN; i64 nca = 0;
  for (int c = 0; c < nc; c++) {
    if (mode == 0 && !(thr[c] == cur_thr)) { cur_thr = thr[c]; nca = 0; for (i64 i = 0; i < M; i++) if (side[i] && ed[i] > cur_thr) cand[nca++] = (int32_t)i; }
    double *csg = sg + (i64)c * NH, *cfg = fg + (i64)c * NH; int32_t *ccn = cnt + (i64)c * NH, *cep = ep + (i64)c * NH, *c1 = cr1 + (i64)c * NH, *c2 = cr2 + (i64)c * NH; int64_t *dk = dropk + (i64)c * 4;
    i64 D = Dl[c], T = To[c], pos = 0, last_exit = -(1LL << 60), ci = 0;
    for (;;) {
      i64 i = -1;
      if (mode == 0) { while (ci < nca && cand[ci] < pos) ci++; if (ci < nca) i = cand[ci]; }
      else { for (i64 k = pos; k < M; k++) if (side[k] && ed[k] > thr[c]) { i = k; break; } }
      if (i < 0) break;
      i64 t = ts[i], te = t + D;
      if (te + minhold > tend) { dk[3]++; break; }
      i64 je = asof(ts, M, te);
      if (je < 0 || !ok[je]) { dk[0]++; pos = i + 1; continue; }
      i64 i0 = lb(ts, M, te + minhold), nx = i0 >= M ? M : (side[i] == 1 ? n1[i0] : n2[i0]);
      i64 tx = nx < M ? ts[nx] : (1LL << 60), E = te + T < tx ? te + T : tx, x = E + D;
      if (x > tend) { dk[3]++; break; }
      i64 jx = asof(ts, M, x);
      if (jx < 0 || !ok[jx]) { dk[1]++; pos = i + 1; continue; }
      if (E == te + T && E < tx) { i64 jd = asof(ts, M, E); if (jd < 0 || !ok[jd]) { dk[2]++; pos = i + 1; continue; } }
      i64 h = (te - t0) / 3600000LL;
      if (h < 0 || h >= NH) { dk[3]++; pos = i + 1; continue; }
      double gr = side[i] == 1 ? ((gbw[jx] / gaw[je] - 1) + (1 - ba[jx] / bb[je])) * 1e4 : ((1 - gaw[jx] / gbw[je]) + (bb[jx] / ba[je] - 1)) * 1e4;  // gbw/gaw — худшее из 2 состояний Gate (а′ п.2)
      double fr = fcum_at(pg, cg, ng, x) - fcum_at(pg, cg, ng, te) - (nb > 0 ? fcum_at(pb, cb, nb, x) - fcum_at(pb, cb, nb, te) : 0.0);
      csg[h] += gr; cfg[h] += side[i] == 1 ? -fr * 1e4 : fr * 1e4; ccn[h]++;
      if (te - last_exit >= 5000) cep[h]++;
      i64 nbd = x / 28800000LL - te / 28800000LL;
      if (nbd > 0) { if (side[i] == 1) c1[h] += (int32_t)nbd; else c2[h] += (int32_t)nbd; }
      last_exit = x; i64 np = lb(ts, M, x + 1); pos = np > i + 1 ? np : i + 1;
    }
  }
  free(side); free(ed); free(n1); free(n2); free(cand);
}
