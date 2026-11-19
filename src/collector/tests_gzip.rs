//! Быстрый путь мелких gzip-кадров (`inflate.rs`, TK-580): выход совпадает с libdeflate на настоящих и сгенерированных кадрах; повреждённые кадры не дают иного вердикта,
//! чем прежний путь; редкие формы (несжатые и фиксированные блоки, перекрывающиеся повторы, длинный заголовок, большой кадр) разжимаются.

use std::io::Write as _;

use super::gzip::Gunzip;
use super::inflate::{Tiny, MAX_OUT};

const SAMPLE: &[u8] = include_bytes!("testdata/bingx-gz-sample.bin");

fn real_frames() -> Vec<(u8, Vec<u8>)> {
    let (mut out, mut at) = (Vec::new(), 0usize);
    while let Some(h) = SAMPLE.get(at..at + 13) {
        let len = u32::from_le_bytes(h[..4].try_into().unwrap()) as usize;
        out.push((h[4], SAMPLE[at + 13..at + 13 + len].to_vec()));
        at += 13 + len;
    }
    out
}

fn gz(data: &[u8], level: u32) -> Vec<u8> {
    let mut e = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::new(level));
    e.write_all(data).unwrap();
    e.finish().unwrap()
}

struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }

    fn below(&mut self, n: u64) -> u64 {
        self.next() % n
    }
}

/// Тексты формы кадров BingX (книга, сделки, mark) со случайными числами и произвольной длины хвоста.
fn json_like(r: &mut Rng) -> Vec<u8> {
    let sym = ["BTC-USDT", "ETH-USDT", "1000PEPE-USDT", "SOL-USDT"][r.below(4) as usize];
    let mut s = String::new();
    match r.below(3) {
        0 => s.push_str(&format!(
            "{{\"code\":0,\"dataType\":\"{sym}@bookTicker\",\"data\":{{\"e\":\"bookTicker\",\"u\":{},\"E\":{},\"T\":{},\"s\":\"{sym}\",\"b\":\"{}.{}\",\"B\":\"{}\",\"a\":\"{}.{}\",\"A\":\"{}\"}}}}",
            r.next() % 1_000_000_000, r.next() % 1_800_000_000_000, r.next() % 1_800_000_000_000,
            r.below(100_000), r.below(1000), r.below(5000), r.below(100_000), r.below(1000), r.below(5000)
        )),
        1 => {
            s.push_str(&format!("{{\"code\":0,\"dataType\":\"{sym}@trade\",\"data\":["));
            for i in 0..=r.below(8) {
                if i > 0 {
                    s.push(',');
                }
                s.push_str(&format!(
                    "{{\"t\":\"{}\",\"q\":\"{}\",\"p\":\"{}.{}\",\"T\":{},\"m\":{},\"s\":\"{sym}\"}}",
                    r.next() % 100_000_000_000, r.below(1000), r.below(100_000), r.below(100), r.next() % 1_800_000_000_000, r.below(2) == 0
                ));
            }
            s.push_str("]}");
        }
        _ => s.push_str(&format!(
            "{{\"code\":0,\"dataType\":\"{sym}@markPrice\",\"data\":{{\"e\":\"markPriceUpdate\",\"E\":{},\"s\":\"{sym}\",\"p\":\"{}.{}\"}}}}",
            r.next() % 1_800_000_000_000, r.below(100_000), r.below(10_000)
        )),
    }
    s.into_bytes()
}

#[test]
fn tiny_decodes_real_bingx_frames_like_libdeflate() {
    let (mut fast, mut n) = (0usize, 0usize);
    let (mut t, mut l) = (Tiny::new(), Gunzip::libdeflate_only());
    let (mut buf, mut both) = (Vec::new(), Gunzip::new());
    for (_, f) in real_frames() {
        let want = l
            .unpack(&f)
            .expect("libdeflate разжимает настоящий кадр")
            .to_vec();
        assert_eq!(both.unpack(&f), Some(want.as_slice()));
        n += 1;
        if let Some(size) = t.unpack(&f, &mut buf) {
            assert_eq!(&buf[..size], want.as_slice());
            fast += 1;
        }
    }
    assert!(n >= 300, "кадров в образце: {n}");
    assert_eq!(fast, n, "быстрый путь берёт все настоящие кадры образца");
}

#[test]
fn tiny_matches_libdeflate_on_generated_frames_all_levels() {
    let mut r = Rng(0x9e37_79b9_7f4a_7c15);
    let (mut t, mut l, mut both) = (Tiny::new(), Gunzip::libdeflate_only(), Gunzip::new());
    let (mut buf, mut fast, mut total) = (Vec::new(), 0usize, 0usize);
    for i in 0..3000 {
        let mut body = json_like(&mut r);
        // иногда — длинный повторяющийся хвост (перекрывающиеся повторы) или чистый случайный шум (несжимаемые блоки)
        match i % 7 {
            0 => body.extend(std::iter::repeat_n(b'a', r.below(3000) as usize)),
            1 => body = (0..r.below(500)).map(|_| r.next() as u8).collect(),
            2 => body.clear(),
            _ => {}
        }
        for level in [0u32, 1, 6, 9] {
            let z = gz(&body, level);
            total += 1;
            assert_eq!(
                l.unpack(&z),
                Some(body.as_slice()),
                "эталон, кадр {i} уровень {level}"
            );
            assert_eq!(
                both.unpack(&z),
                Some(body.as_slice()),
                "Gunzip, кадр {i} уровень {level}"
            );
            if let Some(size) = t.unpack(&z, &mut buf) {
                assert_eq!(
                    &buf[..size],
                    body.as_slice(),
                    "быстрый путь, кадр {i} уровень {level}"
                );
                fast += 1;
            }
        }
    }
    // miniz_oxide иногда пишет неполные коды (у зарезервированных деревьев) — их берёт прежний путь; основная масса — быстрый
    assert!(
        fast * 10 >= total * 9,
        "быстрый путь взял {fast} из {total}"
    );
}

#[test]
fn damaged_frames_get_the_same_verdict_as_the_old_path() {
    let mut r = Rng(0x1234_5678_9abc_def1);
    let (mut l, mut both) = (Gunzip::libdeflate_only(), Gunzip::new());
    let mut checked = 0usize;
    let mut sources: Vec<Vec<u8>> = real_frames()
        .into_iter()
        .map(|(_, f)| f)
        .take(120)
        .collect();
    for _ in 0..80 {
        let b = json_like(&mut r);
        sources.push(gz(&b, 6));
    }
    for f in &sources {
        for _ in 0..40 {
            let mut d = f.clone();
            match r.below(5) {
                0 => {
                    let i = r.below(d.len() as u64) as usize;
                    d[i] ^= 1 << r.below(8);
                }
                1 => {
                    let i = r.below(d.len() as u64) as usize;
                    d[i] = r.next() as u8;
                }
                2 => d.truncate(r.below(d.len() as u64) as usize),
                3 => {
                    let i = r.below(d.len() as u64) as usize;
                    d.insert(i, r.next() as u8);
                }
                _ => {
                    let i = r.below(d.len() as u64) as usize;
                    d.remove(i);
                }
            }
            let want = l.unpack(&d).map(<[u8]>::to_vec);
            assert_eq!(both.unpack(&d).map(<[u8]>::to_vec), want, "кадр {checked}");
            checked += 1;
        }
    }
    assert!(checked > 7000);
}

#[test]
fn tiny_handles_rare_shapes_and_declines_what_it_does_not_cover() {
    let (mut t, mut both, mut buf) = (Tiny::new(), Gunzip::new(), Vec::new());
    // кадр больше потолка быстрого пути — отказ, прежний путь разжимает
    let big: Vec<u8> = (0..200_000u32).map(|i| (i % 251) as u8).collect();
    let z = gz(&big, 0);
    assert!(big.len() > MAX_OUT);
    assert_eq!(t.unpack(&z, &mut buf), None);
    assert_eq!(both.unpack(&z), Some(big.as_slice()));
    // несколько блоков подряд (синхронный сброс пишет пустой несжатый блок между сжатыми) и несжатый блок в пределах быстрого пути
    let (a, b) = (json_like(&mut Rng(7)), json_like(&mut Rng(9)));
    let mut e = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
    e.write_all(&a).unwrap();
    e.flush().unwrap();
    e.write_all(&b).unwrap();
    let z = e.finish().unwrap();
    let ab = [a.as_slice(), b.as_slice()].concat();
    assert_eq!(t.unpack(&z, &mut buf), Some(ab.len()));
    assert_eq!(&buf[..ab.len()], ab.as_slice());
    let mid: Vec<u8> = (0..10_000u32).map(|i| (i * 7 % 253) as u8).collect();
    let z = gz(&mid, 0);
    assert_eq!(t.unpack(&z, &mut buf), Some(mid.len()));
    assert_eq!(&buf[..mid.len()], mid.as_slice());
    // перекрывающийся повтор на расстоянии 1 и 2
    for body in [vec![b'x'; 1000], b"ab".repeat(700)] {
        let z = gz(&body, 6);
        assert_eq!(t.unpack(&z, &mut buf), Some(body.len()));
        assert_eq!(&buf[..body.len()], body.as_slice());
    }
    // заголовок с именем, комментарием и дополнительным полем
    let mut b = flate2::GzBuilder::new()
        .filename("a.json")
        .comment("c")
        .extra(vec![1, 2, 3])
        .write(Vec::new(), flate2::Compression::default());
    b.write_all(b"{\"code\":0}").unwrap();
    let z = b.finish().unwrap();
    assert_eq!(t.unpack(&z, &mut buf), Some(10));
    // CRC заголовка (FHCRC): быстрый путь отказывает, прежний решает сам (libdeflate принимает такой заголовок)
    let mut h = z.clone();
    h[3] |= 2;
    assert_eq!(t.unpack(&h, &mut buf), None);
    // пустой и совсем короткий вход
    assert_eq!(t.unpack(&[], &mut buf), None);
    assert_eq!(t.unpack(&[0x1f, 0x8b, 8], &mut buf), None);
}

/// Долгий прогон повреждений (руками: `cargo test --release fuzz_long -- --ignored`): вердикт `Gunzip` всегда равен вердикту libdeflate.
#[test]
#[ignore = "долгий прогон, не гейт"]
fn fuzz_long_damaged_frames_match_the_old_path() {
    let mut r = Rng(0x0dd_ba11_cafe_f00d);
    let (mut l, mut both) = (Gunzip::libdeflate_only(), Gunzip::new());
    let mut sources: Vec<Vec<u8>> = real_frames().into_iter().map(|(_, f)| f).collect();
    for i in 0..400 {
        let mut b = json_like(&mut r);
        if i % 5 == 0 {
            b.extend(std::iter::repeat_n(b'z', r.below(2000) as usize));
        }
        sources.push(gz(&b, [1, 6, 9][i % 3]));
    }
    let mut accepted = 0usize;
    for f in &sources {
        for _ in 0..600 {
            let mut d = f.clone();
            for _ in 0..=r.below(3) {
                let i = r.below(d.len() as u64) as usize;
                match r.below(6) {
                    0 => d[i] ^= 1 << r.below(8),
                    1 => d[i] = r.next() as u8,
                    2 => d.truncate(i),
                    3 => d.insert(i, r.next() as u8),
                    4 => {
                        d.remove(i);
                    }
                    _ => d[i] = d[i].wrapping_add(1),
                }
                if d.is_empty() {
                    break;
                }
            }
            let want = l.unpack(&d).map(<[u8]>::to_vec);
            accepted += usize::from(want.is_some());
            assert_eq!(both.unpack(&d).map(<[u8]>::to_vec), want);
        }
    }
    assert!(accepted > 0);
}
