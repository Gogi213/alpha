//! Проверка TK-580 (проверяющий): граничные потоки deflate, собранные вручную (длина кода 2…15 у границы `MAX_BITS` = 10, неполный код смещений,
//! расстояние за выходом, лишние байты, несколько членов gzip), и дифференциальный прогон с пересчитанным CRC: повреждённое тело, которое libdeflate
//! ещё разжимает, получает настоящий хвост (CRC32 и длина выхода libdeflate) — так проверяется сам декодер, а не отказ по CRC.

use libdeflater::{CompressionLvl, Compressor, Decompressor};

use super::gzip::Gunzip;
use super::inflate::{Tiny, MAX_OUT};

/// Писатель бит deflate: младший бит первым; коды Хаффмана — старшим битом вперёд.
#[derive(Default)]
struct W {
    v: Vec<u8>,
    acc: u64,
    n: u32,
}

impl W {
    fn bits(&mut self, val: u32, n: u32) {
        self.acc |= u64::from(val) << self.n;
        self.n += n;
        while self.n >= 8 {
            self.v.push(self.acc as u8);
            self.acc >>= 8;
            self.n -= 8;
        }
    }

    fn huff(&mut self, (code, len): (u32, u32)) {
        if len == 0 {
            return;
        }
        self.bits(code.reverse_bits() >> (32 - len), len);
    }

    fn finish(mut self) -> Vec<u8> {
        if self.n > 0 {
            self.v.push(self.acc as u8);
        }
        self.v
    }
}

/// Канонические коды по длинам (RFC 1951, 3.2.2).
fn canon(lens: &[u32]) -> Vec<(u32, u32)> {
    let mut count = [0u32; 16];
    lens.iter()
        .filter(|&&l| l > 0)
        .for_each(|&l| count[l as usize] += 1);
    let (mut next, mut code) = ([0u32; 16], 0u32);
    for b in 1..16 {
        code = (code + count[b - 1]) << 1;
        next[b] = code;
    }
    lens.iter()
        .map(|&l| {
            if l == 0 {
                return (0, 0);
            }
            let c = next[l as usize];
            next[l as usize] += 1;
            (c, l)
        })
        .collect()
}

fn frame(body: &[u8], out: &[u8]) -> Vec<u8> {
    let mut f = vec![0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 0xff];
    f.extend_from_slice(body);
    f.extend_from_slice(&crc32fast::hash(out).to_le_bytes());
    f.extend_from_slice(&(out.len() as u32).to_le_bytes());
    f
}

/// Динамический блок: буквы 'a'.. с длинами кодов 1..k−1, конец блока (256) и длина 257 — длины k (полный код, самый длинный код = k);
/// смещения — один код длины 1 (неполный, допустимый). Данные: каждая буква, затем повтор длины 3 на расстоянии `dist_code + 1`, конец блока.
/// `lens_hook` правит длины литералов перед записью (переполненный / неполный код).
fn dynamic(k: u32, dist_code: u32, lens_hook: impl Fn(&mut Vec<u32>)) -> (Vec<u8>, Vec<u8>) {
    let letters = (k - 1) as usize;
    let mut lit = vec![0u32; 258];
    for i in 0..letters {
        lit[97 + i] = i as u32 + 1;
    }
    lit[256] = k;
    lit[257] = k;
    lens_hook(&mut lit);
    let dist = [1u32];
    // коды длин: 0..15 — длина 5, 16 и 17 — 3, 18 — 2 (полный код)
    let mut pre = [5u32; 19];
    pre[16] = 3;
    pre[17] = 3;
    pre[18] = 2;
    let pc = canon(&pre);
    let mut w = W::default();
    w.bits(1, 1);
    w.bits(2, 2);
    w.bits(258 - 257, 5);
    w.bits(0, 5);
    w.bits(15, 4);
    for idx in [
        16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15,
    ] {
        w.bits(pre[idx], 3);
    }
    let all: Vec<u32> = lit.iter().chain(dist.iter()).copied().collect();
    let mut i = 0;
    while i < all.len() {
        if all[i] == 0 {
            let mut z = all[i..].iter().take_while(|&&l| l == 0).count();
            i += z;
            while z > 0 {
                if z >= 11 {
                    let r = z.min(138);
                    w.huff(pc[18]);
                    w.bits((r - 11) as u32, 7);
                    z -= r;
                } else if z >= 3 {
                    w.huff(pc[17]);
                    w.bits((z - 3) as u32, 3);
                    z = 0;
                } else {
                    w.huff(pc[0]);
                    z -= 1;
                }
            }
        } else {
            w.huff(pc[all[i] as usize]);
            i += 1;
        }
    }
    let lc = canon(&lit);
    let dc = canon(&dist);
    let mut out = Vec::new();
    for i in 0..letters {
        w.huff(lc[97 + i]);
        out.push(97 + i as u8);
    }
    w.huff(lc[257]);
    w.huff(if dist_code == 0 { dc[0] } else { (1, 1) });
    for _ in 0..3 {
        let b = out[out.len() - 1 - dist_code as usize];
        out.push(b);
    }
    w.huff(lc[256]);
    (w.finish(), out)
}

#[test]
fn review_code_length_boundary_and_fallback() {
    let (mut t, mut buf) = (Tiny::new(), Vec::new());
    for k in 2..=15u32 {
        let (body, out) = dynamic(k, 0, |_| {});
        let f = frame(&body, &out);
        assert_eq!(
            Gunzip::libdeflate_only().unpack(&f),
            Some(out.as_slice()),
            "эталон k={k}"
        );
        assert_eq!(
            Gunzip::new().unpack(&f),
            Some(out.as_slice()),
            "Gunzip k={k}"
        );
        let fast = t.unpack(&f, &mut buf);
        if k <= 10 {
            assert_eq!(fast, Some(out.len()), "быстрый путь берёт k={k}");
            assert_eq!(&buf[..out.len()], out.as_slice());
        } else {
            assert_eq!(fast, None, "k={k} > MAX_BITS — прежний путь");
        }
    }
}

#[test]
fn review_malformed_streams_same_verdict() {
    let (mut t, mut buf) = (Tiny::new(), Vec::new());
    let same = |f: &[u8]| {
        let a = Gunzip::libdeflate_only().unpack(f).map(<[u8]>::to_vec);
        let b = Gunzip::new().unpack(f).map(<[u8]>::to_vec);
        assert_eq!(a, b);
        a
    };
    // неиспользуемое слово неполного кода смещений (единственный код длины 1, в потоке — бит 1): быстрый путь отказывает, вердикт как у libdeflate
    let (body, out) = dynamic(4, 1, |_| {});
    same(&frame(&body, &out));
    assert_eq!(t.unpack(&frame(&body, &out), &mut buf), None);
    // расстояние за выходом: повтор на расстоянии 1 при пустом выходе (фиксированный блок)
    let mut w = W::default();
    w.bits(1, 1);
    w.bits(1, 2); // фиксированный блок
    w.huff((1, 7)); // 257 = длина 3
    w.bits(0, 5); // смещение 0 = расстояние 1
    w.huff((0, 7)); // 256
    let body = w.finish();
    assert_eq!(same(&frame(&body, b"xxx")), None);
    assert_eq!(t.unpack(&frame(&body, b"xxx"), &mut buf), None);
    // фиксированный блок, код смещения 30 (недопустим) после литерала
    let mut w = W::default();
    w.bits(1, 1);
    w.bits(1, 2);
    w.huff((0x30 + u32::from(b'a'), 8));
    w.huff((1, 7));
    w.huff((30, 5));
    w.huff((0, 7));
    assert_eq!(same(&frame(&w.finish(), b"aaaa")), None);
    // переполненный код литералов (лишний символ длины k)
    let (body, out) = dynamic(6, 0, |l| l[200] = 6);
    assert_eq!(same(&frame(&body, &out)), None);
    // неполный код литералов (у 257 убрана длина: данные с повтором не декодируются) — быстрый путь отказывает, вердикт как у libdeflate
    let (body, out) = dynamic(6, 0, |l| l[257] = 0);
    same(&frame(&body, &out));
    assert_eq!(t.unpack(&frame(&body, &out), &mut buf), None);
    // лишние байты между потоком deflate и хвостом
    let (body, out) = dynamic(8, 0, |_| {});
    let mut tail = body.clone();
    tail.push(0);
    same(&frame(&tail, &out));
    assert_eq!(t.unpack(&frame(&tail, &out), &mut buf), None);
    // два члена gzip подряд
    let f = frame(&body, &out);
    same(&[f.as_slice(), f.as_slice()].concat());
    // хвост заявляет размер больше потолка быстрого пути
    let mut big = f.clone();
    let n = big.len();
    big[n - 4..].copy_from_slice(&((MAX_OUT + 1) as u32).to_le_bytes());
    same(&big);
}

struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }
}

/// Источники: кадры libdeflate уровней 1–12 (другой кодировщик, чем flate2 в тестах исполнителя) на JSON-подобных и перекошенных данных
/// (геометрическое распределение символов → длинные коды 11–15 бит у уровня 12).
fn sources(r: &mut Rng) -> Vec<Vec<u8>> {
    let mut v = Vec::new();
    for i in 0..600usize {
        let n = 20 + (r.next() % 3000) as usize;
        let data: Vec<u8> = match i % 3 {
            0 => (0..n)
                .map(|_| b"0123456789.,:\"{}abcdefTsEpq"[(r.next() % 27) as usize])
                .collect(),
            1 => (0..n)
                .map(|_| (r.next() | (1 << 40)).trailing_zeros() as u8 + b'A')
                .collect(),
            _ => (0..n).map(|_| (r.next() % 7) as u8 * 31).collect(),
        };
        let lvl = (i % 12) as i32 + 1;
        let mut c = Compressor::new(CompressionLvl::new(lvl).unwrap());
        let mut z = vec![0u8; c.deflate_compress_bound(data.len())];
        let m = c.deflate_compress(&data, &mut z).unwrap();
        z.truncate(m);
        v.push(z);
    }
    v
}

#[test]
fn review_differential_with_recomputed_crc() {
    let mut r = Rng(0x5eed_1234_abcd_0001);
    let src = sources(&mut r);
    let (mut t, mut buf, mut d) = (Tiny::new(), Vec::new(), Decompressor::new());
    let mut out = vec![0u8; MAX_OUT];
    let (mut valid, mut fast, mut total, mut valid_orig, mut fast_orig) =
        (0usize, 0usize, 0usize, 0, 0);
    for z in &src {
        // без повреждений: всё, что libdeflate разжимает, быстрый путь либо разжимает так же, либо отдаёт прежнему
        let n = d.deflate_decompress(z, &mut out).unwrap();
        let f = frame(z, &out[..n]);
        valid_orig += 1;
        if let Some(s) = t.unpack(&f, &mut buf) {
            assert_eq!(&buf[..s], &out[..n]);
            fast_orig += 1;
        }
        assert_eq!(Gunzip::new().unpack(&f), Some(&out[..n]));
        for _ in 0..300 {
            let mut b = z.clone();
            for _ in 0..=(r.next() % 2) {
                let i = (r.next() % b.len() as u64) as usize;
                match r.next() % 3 {
                    0 => b[i] ^= 1 << (r.next() % 8),
                    1 => b[i] = r.next() as u8,
                    _ => b.truncate(i.max(1)),
                }
            }
            total += 1;
            match d.deflate_decompress(&b, &mut out) {
                Ok(n) => {
                    // тело ещё разжимается: настоящий хвост — сам декодер под проверкой
                    valid += 1;
                    let f = frame(&b, &out[..n]);
                    let want = Gunzip::libdeflate_only().unpack(&f).map(<[u8]>::to_vec);
                    assert_eq!(Gunzip::new().unpack(&f).map(<[u8]>::to_vec), want);
                    if let Some(s) = t.unpack(&f, &mut buf) {
                        assert_eq!(&buf[..s], &out[..n]);
                        fast += 1;
                    }
                }
                Err(_) => {
                    // libdeflate отказывает: быстрый путь не должен паниковать; хвост — от исходных данных
                    let f = frame(&b, &out[..0]);
                    assert_eq!(
                        Gunzip::new().unpack(&f).map(<[u8]>::to_vec),
                        Gunzip::libdeflate_only().unpack(&f).map(<[u8]>::to_vec)
                    );
                }
            }
        }
    }
    println!(
        "review: sources {valid_orig}, fast on clean {fast_orig}; mutated {total}, still valid deflate {valid}, fast took {fast}"
    );
    assert!(
        valid > 1000,
        "повреждённых, но разжимаемых тел мало: {valid}"
    );
}

#[test]
fn review_random_bodies_never_panic() {
    let mut r = Rng(0xfeed_beef_0000_0042);
    let mut t = Tiny::new();
    let mut buf = Vec::new();
    for _ in 0..200_000 {
        let n = 1 + (r.next() % 300) as usize;
        let mut b: Vec<u8> = (0..n).map(|_| r.next() as u8).collect();
        // первые биты — динамический/фиксированный/несжатый блок поровну
        b[0] = (b[0] & !6) | (((r.next() % 3) as u8) << 1);
        let size = (r.next() % 2000) as usize;
        let mut f = vec![0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 0xff];
        f.extend_from_slice(&b);
        f.extend_from_slice(&(r.next() as u32).to_le_bytes());
        f.extend_from_slice(&(size as u32).to_le_bytes());
        let _ = t.unpack(&f, &mut buf);
    }
}

/// Все настоящие кадры записи исполнителя (`/data/tk580/bx-raw.bin` на srv, 135 462 кадра): по каждому кадру быстрый путь, libdeflate и
/// flate2 (третий, независимый декодер) дают одни и те же байты. Руками: `cargo test --release review_real_frames -- --ignored --nocapture`.
#[test]
#[ignore = "нужна запись кадров на srv"]
fn review_real_frames_byte_identical_three_decoders() {
    use std::io::Read as _;
    let raw = std::fs::read("/data/tk580/bx-raw.bin").expect("запись кадров");
    let (mut t, mut buf, mut l) = (Tiny::new(), Vec::new(), Gunzip::libdeflate_only());
    let (mut at, mut n, mut fast) = (0usize, 0usize, 0usize);
    while let Some(h) = raw.get(at..at + 13) {
        let len = u32::from_le_bytes(h[..4].try_into().unwrap()) as usize;
        let f = &raw[at + 13..at + 13 + len];
        at += 13 + len;
        let mut z = Vec::new();
        flate2::read::GzDecoder::new(f)
            .read_to_end(&mut z)
            .expect("flate2");
        assert_eq!(l.unpack(f), Some(z.as_slice()), "libdeflate, кадр {n}");
        let s = t.unpack(f, &mut buf).expect("быстрый путь берёт кадр");
        assert_eq!(&buf[..s], z.as_slice(), "быстрый путь, кадр {n}");
        fast += 1;
        n += 1;
    }
    println!("review: frames {n}, fast {fast}");
    assert_eq!(at, raw.len());
}
