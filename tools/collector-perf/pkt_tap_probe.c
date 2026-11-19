// G-022 collector-perf: проба «AF_PACKET tpacket_v3 как источник точных меток приёма пакетов»: цена кольца на живом потоке коллекторов srv.
// gcc -O2 -o pkt_tap_probe pkt_tap_probe.c ; ./pkt_tap_probe <секунд> <snaplen: 0 — весь кадр>
// Печатает: пакетов/с, из них TCP, CPU процесса (user/sys), блоков/с, максимальный разрыв между меткой пакета и временем разбора.
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <linux/if_ether.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <netinet/ip.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

static long long now_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_REALTIME, &t);
    return t.tv_sec * 1000000000LL + t.tv_nsec;
}

int main(int argc, char **argv) {
    int secs = argc > 1 ? atoi(argv[1]) : 20;
    int snap = argc > 2 ? atoi(argv[2]) : 0;
    int fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
    if (fd < 0) { perror("socket"); return 1; }
    int ver = TPACKET_V3;
    if (setsockopt(fd, SOL_PACKET, PACKET_VERSION, &ver, sizeof ver)) { perror("version"); return 1; }
    struct tpacket_req3 req;
    memset(&req, 0, sizeof req);
    req.tp_block_size = 1 << 20;
    req.tp_block_nr = 16;
    req.tp_frame_size = 2048;
    req.tp_frame_nr = req.tp_block_size / req.tp_frame_size * req.tp_block_nr;
    req.tp_retire_blk_tov = 1; // мс
    if (setsockopt(fd, SOL_PACKET, PACKET_RX_RING, &req, sizeof req)) { perror("ring"); return 1; }
    unsigned char *ring = mmap(NULL, (size_t)req.tp_block_size * req.tp_block_nr, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (ring == MAP_FAILED) { perror("mmap"); return 1; }
    (void)snap;
    long long t_end = now_ns() + secs * 1000000000LL, pk = 0, tcp = 0, blocks = 0, maxlag = 0;
    unsigned bi = 0;
    struct rusage r0, r1;
    getrusage(RUSAGE_SELF, &r0);
    long long t0 = now_ns();
    while (now_ns() < t_end) {
        struct tpacket_block_desc *bd = (struct tpacket_block_desc *)(ring + (size_t)bi * req.tp_block_size);
        if (!(bd->hdr.bh1.block_status & TP_STATUS_USER)) {
            struct pollfd p = {.fd = fd, .events = POLLIN};
            poll(&p, 1, 10);
            continue;
        }
        long long tnow = now_ns();
        struct tpacket3_hdr *ph = (struct tpacket3_hdr *)((unsigned char *)bd + bd->hdr.bh1.offset_to_first_pkt);
        for (unsigned i = 0; i < bd->hdr.bh1.num_pkts; i++) {
            pk++;
            unsigned char *eth = (unsigned char *)ph + ph->tp_mac;
            if (ph->tp_snaplen > 34 && eth[12] == 0x08 && eth[13] == 0x00 && eth[23] == 6) {
                tcp++;
                long long ts = (long long)ph->tp_sec * 1000000000LL + ph->tp_nsec;
                if (tnow - ts > maxlag) maxlag = tnow - ts;
            }
            ph = (struct tpacket3_hdr *)((unsigned char *)ph + ph->tp_next_offset);
        }
        bd->hdr.bh1.block_status = TP_STATUS_KERNEL;
        bi = (bi + 1) % req.tp_block_nr;
        blocks++;
    }
    getrusage(RUSAGE_SELF, &r1);
    double dt = (now_ns() - t0) / 1e9;
    double ut = (r1.ru_utime.tv_sec - r0.ru_utime.tv_sec) + (r1.ru_utime.tv_usec - r0.ru_utime.tv_usec) / 1e6;
    double st = (r1.ru_stime.tv_sec - r0.ru_stime.tv_sec) + (r1.ru_stime.tv_usec - r0.ru_stime.tv_usec) / 1e6;
    printf("%.0f s: %.0f pkt/s (TCP IPv4 %.0f/s), blocks %.0f/s, CPU process user %.3f + sys %.3f = %.3f cores, max lag tap->parse %.2f ms\n", dt, pk / dt, tcp / dt, blocks / dt, ut / dt, st / dt, (ut + st) / dt, maxlag / 1e6);
    return 0;
}
