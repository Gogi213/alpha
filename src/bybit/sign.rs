//! Подпись приватных запросов Bybit, HMAC-SHA256.
//!
//! Decision 12 (`PLAN.md`): ключи — только из окружения, никогда из файла
//! или аргумента. Формат подписи v5 подтверждён официальной документацией
//! Bybit (`bybit-exchange.github.io/docs/v5/guide`, раздел Authentication)
//! и собственным демо-репозиторием биржи
//! (`bybit-exchange/api-usage-examples`, `V5_demo/api_demo/Encryption_HMAC.py`):
//! `timestamp + api_key + recv_window + body`, HMAC-SHA256, hex в нижнем
//! регистре. Числового примера с реальным секретом Bybit не публикует —
//! страница документации прямо это подтверждает (значение секрета опущено
//! «for security reasons»), и её собственное демо использует один и тот же
//! плейсхолдер `"XXXXXXXXXX"` и для `api_key`, и для `secret_key`. Поэтому
//! известный ответ в тестах ниже — не скопированный из документации вектор,
//! а построенный нами по той же формуле и сверенный независимо через Python
//! `hmac`/`hashlib` и `openssl dgst -sha256 -hmac`: ни та, ни другая
//! реализация не этот крейт, так что сверка не тавтологична.

use hmac::{Hmac, Mac};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

/// Имена переменных окружения — единственный источник ключей (Decision 12).
/// Константы, а не только текст внутри `from_env`: тест «зонд отказывается
/// работать без ключей» (`probe.rs`) обязан проверять отказ на этих же
/// именах, а не на переписанной вручную строке, которая могла разойтись.
pub const API_KEY_VAR: &str = "BYBIT_API_KEY";
pub const API_SECRET_VAR: &str = "BYBIT_API_SECRET";

/// Ключи приватного API. `api_secret` не отдаётся наружу ничем, кроме
/// `sign` — и оттуда выходит уже как HMAC-дайджест, необратимый к секрету,
/// а не как сам секрет.
pub struct Credentials {
    api_key: String,
    api_secret: String,
}

/// `derive(Debug)` здесь не используется намеренно: он напечатал бы оба
/// поля как есть, и `api_secret` попал бы в любой `{:?}` — в панике, в её
/// логе, в `dbg!` посреди отладки. Ручная реализация — единственный способ
/// гарантировать, что секрет не всплывёт там, где никто не собирался его
/// логировать; тест `secret_never_appears_in_debug_output` проверяет это
/// свойство явно, а не доверяет автогенерации.
impl std::fmt::Debug for Credentials {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Credentials")
            .field("api_key", &"<redacted>")
            .field("api_secret", &"<redacted>")
            .finish()
    }
}

/// Ошибка чтения ключей. Несёт только имя переменной, никогда значение —
/// значения тут просто нет, читать было нечего.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CredentialsError {
    Missing(&'static str),
}

impl Credentials {
    /// Единственный конструктор боевого пути: ключи только из окружения
    /// (Decision 12), пользователь выставляет их сам. Пустая строка
    /// приравнена к отсутствующей — иначе `BYBIT_API_KEY=` без значения
    /// проходит здесь и падает уже на бирже, с гораздо менее понятной
    /// ошибкой, чем эта.
    pub fn from_env() -> Result<Self, CredentialsError> {
        Ok(Self {
            api_key: read_var(API_KEY_VAR)?,
            api_secret: read_var(API_SECRET_VAR)?,
        })
    }

    /// Только для тестов: `from_env` — единственный способ получить ключи
    /// в бою, а тестам нужен предсказуемый секрет без переменных окружения
    /// вообще, иначе тест «ключей нет» и тест «ключи есть» гонялись бы по
    /// одной и той же глобальной памяти процесса. `pub(crate)`, а не
    /// приватная: тот же риск и та же нужда — у `probe.rs`, `cfg(test)`
    /// действует на весь крейт при `cargo test`, так что видимость внутри
    /// крейта здесь ничего не открывает наружу.
    #[cfg(test)]
    pub(crate) fn for_test(api_key: &str, api_secret: &str) -> Self {
        Self {
            api_key: api_key.to_string(),
            api_secret: api_secret.to_string(),
        }
    }

    /// Публичный ключ — единственное поле, уходящее в заголовок
    /// `X-BAPI-API-KEY` открытым текстом; так задумано протоколом Bybit,
    /// это не секрет в криптографическом смысле.
    pub fn api_key(&self) -> &str {
        &self.api_key
    }

    /// Подпись v5: `timestamp + api_key + recv_window + body` (см. doc
    /// модуля). HMAC-SHA256, hex в нижнем регистре — оба требования задаёт
    /// сама биржа, не наш выбор.
    pub fn sign(&self, timestamp_ms: i64, recv_window_ms: u32, body: &str) -> String {
        let payload = format!("{timestamp_ms}{}{recv_window_ms}{body}", self.api_key);
        // Ключ HMAC-SHA256 годится любой длины (короткий паддится нулями до
        // блока по определению алгоритма), поэтому `new_from_slice` не
        // может отказать по значению секрета — только по причинам, которых
        // у sha256-семейства попросту нет.
        let mut mac = HmacSha256::new_from_slice(self.api_secret.as_bytes())
            .expect("HMAC-SHA256 принимает ключ любой длины");
        mac.update(payload.as_bytes());
        hex_lower(&mac.finalize().into_bytes())
    }
}

fn read_var(name: &'static str) -> Result<String, CredentialsError> {
    match std::env::var(name) {
        Ok(v) if !v.is_empty() => Ok(v),
        _ => Err(CredentialsError::Missing(name)),
    }
}

/// Ручной hex вместо отдельного крейта: одно место использования на 32
/// байта — заводить ради него ещё одну зависимость дороже, чем эти десять
/// строк, а `Cargo.toml` в этом проходе не наш файл.
fn hex_lower(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push(DIGITS[(b >> 4) as usize] as char);
        out.push(DIGITS[(b & 0x0f) as usize] as char);
    }
    out
}

/// Синхронизация тестов, трогающих `BYBIT_API_KEY`/`BYBIT_API_SECRET`:
/// переменные окружения — общая память процесса, а `cargo test` гоняет
/// тесты параллельно потоками одного процесса. Без общего замка тест «ключ
/// есть» и тест «ключа нет» — из этого файла и из `probe.rs` — видят чужую
/// правку и мигают через раз.
#[cfg(test)]
static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// Общий помощник для обоих файлов, трогающих эти переменные: снимает
/// текущие значения (тест не вправе зависеть от того, что уже стоит в
/// окружении того, кто запускает `cargo test` — у живого трейдера ключи
/// вполне могут быть выставлены), очищает их на время `f`, потом
/// восстанавливает. Восстановление не переживёт `panic!` внутри `f`
/// (не оборачиваем в `catch_unwind` ради простоты) — это не проблема
/// корректности: следующий тест на этих переменных сам вызовет этот же
/// помощник и сам очистит перед собой.
#[cfg(test)]
pub(crate) fn with_cleared_env<T>(f: impl FnOnce() -> T) -> T {
    let _guard = ENV_LOCK.lock().unwrap();
    let saved = (
        std::env::var(API_KEY_VAR).ok(),
        std::env::var(API_SECRET_VAR).ok(),
    );
    std::env::remove_var(API_KEY_VAR);
    std::env::remove_var(API_SECRET_VAR);

    let result = f();

    match saved.0 {
        Some(v) => std::env::set_var(API_KEY_VAR, v),
        None => std::env::remove_var(API_KEY_VAR),
    }
    match saved.1 {
        Some(v) => std::env::set_var(API_SECRET_VAR, v),
        None => std::env::remove_var(API_SECRET_VAR),
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Тест-вектор из RFC 4231, Test Case 1 — независимый от Bybit источник
    /// для самого примитива: key = 0x0b × 20, data = "Hi There". Сверен
    /// независимо через Python `hmac.new(key, data, hashlib.sha256).hexdigest()`
    /// перед тем, как попасть сюда.
    #[test]
    fn hmac_sha256_matches_rfc4231_test_case_1() {
        let key = [0x0b_u8; 20];
        let mut mac = HmacSha256::new_from_slice(&key).unwrap();
        mac.update(b"Hi There");
        let got = hex_lower(&mac.finalize().into_bytes());
        assert_eq!(
            got,
            "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"
        );
    }

    /// Bybit не публикует числовой пример с секретом (см. doc модуля), но
    /// формулу и плейсхолдер `"XXXXXXXXXX"` для `api_key`/`secret_key`
    /// публикуют и документация, и демо `Encryption_HMAC.py` биржи — оба
    /// сходятся на нём. Ожидаемый дайджест ниже — не из документации: он
    /// посчитан нами по той же формуле независимо через Python
    /// `hmac`/`hashlib` и сверен через `openssl dgst -sha256 -hmac`, то есть
    /// двумя реализациями, ни одна из которых не этот крейт — иначе тест
    /// проверял бы крейт самим собой.
    #[test]
    fn signature_matches_bybit_v5_formula_known_answer() {
        let creds = Credentials::for_test("XXXXXXXXXX", "XXXXXXXXXX");
        let body = r#"{"category":"option"}"#;
        let sig = creds.sign(1_658_385_579_423, 5000, body);
        assert_eq!(
            sig,
            "ff137e622dc52629f41b52ea614eab59502e2b0663f153c528cb570379c469b7"
        );
    }

    /// Подпись обязана зависеть от каждого слагаемого формулы: тест на
    /// одну константу проходил бы и в случае регрессии, где, скажем,
    /// `body` потерялся бы из конкатенации.
    #[test]
    fn signature_changes_when_any_ingredient_changes() {
        let creds = Credentials::for_test("k", "s");
        let base = creds.sign(1, 5000, "{}");
        assert_ne!(base, creds.sign(1, 5000, r#"{"x":1}"#), "тело");
        assert_ne!(base, creds.sign(1, 6000, "{}"), "recv_window");
        assert_ne!(base, creds.sign(2, 5000, "{}"), "timestamp");
        assert_ne!(
            base,
            Credentials::for_test("k2", "s").sign(1, 5000, "{}"),
            "api_key"
        );
        assert_ne!(
            base,
            Credentials::for_test("k", "s2").sign(1, 5000, "{}"),
            "секрет"
        );
    }

    #[test]
    fn secret_never_appears_in_debug_output() {
        let secret = "s3cr3t-do-not-leak-9f8a7";
        let creds = Credentials::for_test("visible-key", secret);
        let printed = format!("{creds:?}");
        assert!(!printed.contains(secret));
        assert!(!printed.contains("visible-key"));
        assert!(printed.contains("<redacted>"));
    }

    #[test]
    fn from_env_rejects_a_missing_key() {
        with_cleared_env(|| {
            std::env::set_var(API_SECRET_VAR, "secret");
            assert_eq!(
                Credentials::from_env().unwrap_err(),
                CredentialsError::Missing(API_KEY_VAR)
            );
        });
    }

    #[test]
    fn from_env_rejects_a_missing_secret() {
        with_cleared_env(|| {
            std::env::set_var(API_KEY_VAR, "key");
            assert_eq!(
                Credentials::from_env().unwrap_err(),
                CredentialsError::Missing(API_SECRET_VAR)
            );
        });
    }

    /// Пустая строка — частый исход `KEY=` в `.env`/systemd-юните без
    /// значения. Без этой проверки такой ключ проходит `from_env` и падает
    /// уже на бирже с гораздо менее понятной ошибкой.
    #[test]
    fn from_env_rejects_an_empty_value_the_same_as_missing() {
        with_cleared_env(|| {
            std::env::set_var(API_KEY_VAR, "");
            std::env::set_var(API_SECRET_VAR, "secret");
            assert_eq!(
                Credentials::from_env().unwrap_err(),
                CredentialsError::Missing(API_KEY_VAR)
            );
        });
    }

    #[test]
    fn from_env_succeeds_when_both_are_set() {
        with_cleared_env(|| {
            std::env::set_var(API_KEY_VAR, "key");
            std::env::set_var(API_SECRET_VAR, "secret");
            let creds = Credentials::from_env().unwrap();
            assert_eq!(creds.api_key(), "key");
        });
    }
}
