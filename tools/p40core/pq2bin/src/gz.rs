//! TK-516: gzip целиком в память через libdeflate (быстрее потокового zlib-rs на часовых файлах Gate ≤ ~40 МБ).
//! Несколько склеенных членов gzip читаются подряд, как `MultiGzDecoder`; хвост из нулей после последнего члена не читается.

use libdeflate_sys::{
    libdeflate_alloc_decompressor, libdeflate_free_decompressor, libdeflate_gzip_decompress_ex,
    libdeflate_result_LIBDEFLATE_INSUFFICIENT_SPACE as INSUFFICIENT_SPACE, libdeflate_result_LIBDEFLATE_SUCCESS as SUCCESS,
};

/// Распаковать все члены gzip из `data`. Паника при битом потоке (как `unwrap()` на потоковом чтении раньше).
pub fn gunzip_all(data: &[u8]) -> Vec<u8> {
    let hint = match data {
        [.., a, b, c, d] => u32::from_le_bytes([*a, *b, *c, *d]) as usize,
        _ => 0,
    };
    let mut out: Vec<u8> = Vec::with_capacity(hint.max(1 << 16));
    let mut pos = 0;
    // SAFETY: декомпрессор создаётся и освобождается здесь; указатели выхода — в пределах `spare_capacity`, длина выставляется по факту записи.
    unsafe {
        let d = libdeflate_alloc_decompressor();
        assert!(!d.is_null(), "libdeflate: нет памяти");
        while pos < data.len() {
            if data[pos..].iter().all(|&b| b == 0) {
                break;
            }
            loop {
                let (mut in_n, mut out_n) = (0usize, 0usize);
                let len = out.len();
                let cap = out.capacity() - len;
                let r = libdeflate_gzip_decompress_ex(
                    d,
                    data[pos..].as_ptr().cast(),
                    data.len() - pos,
                    out.as_mut_ptr().add(len).cast(),
                    cap,
                    &mut in_n,
                    &mut out_n,
                );
                if r == SUCCESS {
                    out.set_len(len + out_n);
                    pos += in_n;
                    break;
                } else if r == INSUFFICIENT_SPACE {
                    // reserve считает от len, а не от capacity: прежнее `reserve(capacity.max(1<<20))` при len = 0 и cap ≥ 1 МБ не росло (вечный цикл)
                    out.reserve(out.capacity() + (1 << 20));
                } else {
                    libdeflate_free_decompressor(d);
                    panic!("libdeflate: gzip, код {r}");
                }
            }
        }
        libdeflate_free_decompressor(d);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};

    fn gz(b: &[u8]) -> Vec<u8> {
        let mut e = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::new(6));
        e.write_all(b).unwrap();
        e.finish().unwrap()
    }

    fn sample(n: usize) -> Vec<u8> {
        (0..n).flat_map(|i| format!("{},{},{}\n", i, i * 7 % 13, (i as f64) * 0.001).into_bytes()).collect()
    }

    #[test]
    fn same_as_multigz_single_and_concat() {
        let (a, b) = (sample(50_000), sample(3));
        let mut cat = gz(&a);
        cat.extend(gz(&b));
        for blob in [gz(&a), cat, gz(b"")] {
            let mut want = Vec::new();
            flate2::read::MultiGzDecoder::new(&blob[..]).read_to_end(&mut want).unwrap();
            assert_eq!(gunzip_all(&blob), want);
        }
    }

    #[test]
    fn isize_hint_too_small_grows() {
        // два члена: подсказка ISIZE — только по последнему (малому), буфер обязан вырасти
        let mut cat = gz(&sample(200_000));
        cat.extend(gz(b"x\n"));
        assert_eq!(gunzip_all(&cat).len(), sample(200_000).len() + 2);
    }
}
