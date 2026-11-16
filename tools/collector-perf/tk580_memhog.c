// TK-580 load generator: bw = streaming memcpy over 2x256 MB (memory bandwidth), chase = random pointer chase over 512 MB (memory latency / LLC thrash). usage: memhog bw|chase seconds
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdint.h>
int main(int argc, char **argv) {
    int bw = argc > 1 && argv[1][0] == 'b'; int secs = argc > 2 ? atoi(argv[2]) : 60;
    time_t end = time(0) + secs;
    if (bw) {
        size_t n = 256u << 20; char *a = malloc(n), *b = malloc(n); memset(a, 1, n); memset(b, 2, n);
        while (time(0) < end) { memcpy(b, a, n); memcpy(a, b, n); }
        return a[n / 2] + b[n / 3] > 1000;
    } else {
        size_t n = (512u << 20) / sizeof(uint32_t); uint32_t *p = malloc(n * sizeof *p);
        for (size_t i = 0; i < n; i++) p[i] = (uint32_t)i;
        uint64_t x = 88172645463325252ull;
        for (size_t i = n - 1; i > 0; i--) { x ^= x << 13; x ^= x >> 7; x ^= x << 17; size_t j = x % (i + 1); uint32_t t = p[i]; p[i] = p[j]; p[j] = t; }
        uint32_t c = 0; while (time(0) < end) for (int k = 0; k < (1 << 20); k++) c = p[c];
        return c == 12345;
    }
}
