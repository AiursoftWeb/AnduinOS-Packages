use regex::Regex;
use std::ffi::{CStr, CString, c_char, c_void};
use std::sync::LazyLock;

static ANNOTATIONS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\s*[\[(](?:blank[ _-]audio|silence|music|applause|inaudible)[\])]\s*").unwrap()
});
static SPACES: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[ \t]+").unwrap());

pub fn clean_transcript(text: &str) -> String {
    SPACES
        .replace_all(&ANNOTATIONS.replace_all(text, " "), " ")
        .trim()
        .to_owned()
}
pub fn remove_punctuation(text: &str) -> String {
    text.chars()
        .filter(|c| !",.!?;:，。！？；：".contains(*c))
        .collect()
}

pub fn apply_voice_command(text: &str, enabled: bool) -> (String, bool) {
    let clean = clean_transcript(text);
    if !enabled {
        return (clean, false);
    }
    let normalized = clean.to_lowercase();
    let normalized = normalized.trim_matches(|c| " .,!?:;，。！？：；\"'“”‘’".contains(c));
    let replacement = match normalized {
        "stop listening" | "stop dictation" | "停止听写" | "停止聽寫" | "停止语音输入"
        | "停止語音輸入" => return (String::new(), true),
        "new line" | "newline" | "换行" | "換行" | "新的一行" => "\n",
        "new paragraph" | "新段落" => "\n\n",
        "comma" => ",",
        "逗号" | "逗號" => "，",
        "period" | "full stop" => ".",
        "句号" | "句號" => "。",
        "question mark" => "?",
        "问号" | "問號" => "？",
        "exclamation mark" => "!",
        "感叹号" | "感嘆號" => "！",
        "tab" | "制表符" | "製表符" => "\t",
        _ => return (clean, false),
    };
    (replacement.to_owned(), false)
}

fn chinese_config(language: &str) -> Option<&'static str> {
    match language {
        "zh" | "zh-CN" | "zh-SG" | "zh-Hans" => Some("t2s.json"),
        "zh-TW" | "zh-HK" | "zh-Hant" => Some("s2t.json"),
        _ => None,
    }
}
pub fn whisper_language(language: &str) -> &str {
    if chinese_config(language).is_some() {
        "zh"
    } else {
        language
    }
}

/// The converter is owned by the recognition thread, never shared with capture.
pub struct ChineseConverter {
    library: libloading::Library,
    converter: *mut c_void,
}
impl ChineseConverter {
    pub fn new(language: &str) -> Result<Option<Self>, String> {
        let Some(config) = chinese_config(language) else {
            return Ok(None);
        };
        // SAFETY: load the distribution's OpenCC C ABI; keep the library alive
        // until its converter has been closed. No pointer escapes this owner.
        unsafe {
            let library = libloading::Library::new("libopencc.so.1.1")
                .map_err(|_| "OpenCC is not installed".to_owned())?;
            let open = library
                .get::<unsafe extern "C" fn(*const c_char) -> *mut c_void>(b"opencc_open\0")
                .map_err(|_| "OpenCC ABI is unavailable".to_owned())?;
            let converter = open(CString::new(config).unwrap().as_ptr());
            if converter.is_null() || converter as isize == -1 {
                return Err("OpenCC failed".into());
            }
            Ok(Some(Self { library, converter }))
        }
    }
    pub fn convert(&self, text: &str) -> Result<String, String> {
        if text.is_empty() {
            return Ok(String::new());
        }
        let input = CString::new(text).map_err(|_| "Invalid recognition text".to_owned())?;
        // SAFETY: pointers and lengths refer to live UTF-8 input and the owned
        // OpenCC converter. The returned allocation is freed with OpenCC's API.
        unsafe {
            let convert = self
                .library
                .get::<unsafe extern "C" fn(*mut c_void, *const c_char, usize) -> *mut c_char>(
                    b"opencc_convert_utf8\0",
                )
                .map_err(|_| "OpenCC ABI is unavailable".to_owned())?;
            let free = self
                .library
                .get::<unsafe extern "C" fn(*mut c_char)>(b"opencc_convert_utf8_free\0")
                .map_err(|_| "OpenCC ABI is unavailable".to_owned())?;
            let output = convert(self.converter, input.as_ptr(), text.len());
            if output.is_null() || output as isize == -1 {
                return Err("OpenCC failed".into());
            }
            let result = CStr::from_ptr(output)
                .to_str()
                .map(str::to_owned)
                .map_err(|_| "OpenCC returned invalid UTF-8".to_owned());
            free(output);
            result
        }
    }
}
impl Drop for ChineseConverter {
    fn drop(&mut self) {
        // SAFETY: close once while the owning library is still loaded.
        unsafe {
            if let Ok(close) = self
                .library
                .get::<unsafe extern "C" fn(*mut c_void) -> i32>(b"opencc_close\0")
            {
                close(self.converter);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn commands_are_exact_and_opt_in() {
        assert_eq!(apply_voice_command("New line!", true), ("\n".into(), false));
        assert_eq!(apply_voice_command("停止聽寫。", true), ("".into(), true));
        assert_eq!(
            apply_voice_command("new line", false),
            ("new line".into(), false)
        );
        assert_eq!(
            apply_voice_command("say new line", true),
            ("say new line".into(), false)
        );
        assert_eq!(
            clean_transcript(" [BLANK_AUDIO] hello\t world (music) "),
            "hello world"
        );
        assert_eq!(remove_punctuation("你好，world!"), "你好world");
    }
    #[test]
    fn chinese_language_mapping() {
        assert_eq!(whisper_language("zh-Hant"), "zh");
        assert_eq!(whisper_language("en"), "en");
    }
}
