use gio::prelude::*;
use std::path::{Path, PathBuf};

pub const SETTINGS_SCHEMA: &str = "com.anduinos.voice-typing";
pub const SYSTEM_MODEL_DIR: &str = "/usr/share/anduinos-whisper-framework/models";

pub fn model_path(key: &str) -> PathBuf {
    resolve_model(
        key,
        &glib::home_dir().join(".local/share/anduinos-whisper/models"),
        Path::new(SYSTEM_MODEL_DIR),
    )
}
fn resolve_model(key: &str, user: &Path, system: &Path) -> PathBuf {
    let key = match key {
        "tiny" | "base" | "small" => key,
        _ => "base",
    };
    let name = format!("ggml-{key}.bin");
    let local = user.join(&name);
    if local.is_file() {
        local
    } else {
        system.join(name)
    }
}
pub fn model_installed(key: &str) -> bool {
    model_path(key)
        .metadata()
        .is_ok_and(|s| s.is_file() && s.len() >= 1_000_000)
}
pub fn settings() -> Result<gio::Settings, String> {
    let schema = gio::SettingsSchemaSource::default()
        .and_then(|s| s.lookup(SETTINGS_SCHEMA, true))
        .ok_or_else(|| "Voice Typing settings schema is not installed".to_owned())?;
    Ok(gio::Settings::new_full(
        &schema,
        None::<&gio::SettingsBackend>,
        None,
    ))
}
pub fn live_mode(settings: &gio::Settings) -> String {
    let explicit = settings
        .user_value("live-transcription-mode")
        .and_then(|v| v.get::<String>());
    let legacy = settings
        .user_value("live-transcription")
        .and_then(|v| v.get::<bool>());
    crate::live_policy::live_mode(
        explicit.as_deref(),
        legacy,
        settings.string("live-transcription-mode").as_str(),
    )
}

/// Immutable recognition choices for accepted audio; later UI changes apply to
/// the next session. Runtime punctuation/cue choices remain live as before.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SessionConfig {
    pub model: String,
    pub language: String,
    pub backend: String,
    pub threads: u32,
    pub generation: u32,
    pub full_tuning: bool,
}
impl SessionConfig {
    pub fn read(settings: &gio::Settings) -> Self {
        let model = settings.string("model");
        let language = settings.string("language");
        Self {
            model: if model.is_empty() {
                "base".into()
            } else {
                model.into()
            },
            language: if language.is_empty() {
                "auto".into()
            } else {
                language.into()
            },
            backend: settings.string("recognition-backend").into(),
            threads: settings.uint("recognition-threads"),
            generation: settings.uint("tuning-generation"),
            full_tuning: settings.boolean("full-tuning-pending"),
        }
    }
    pub fn complete_full_tuning(&self, settings: &gio::Settings) -> Result<(), glib::BoolError> {
        if settings.uint("tuning-generation") == self.generation {
            settings.set_boolean("full-tuning-pending", false)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn settings_snapshot_and_legacy_preview_preserve_user_choices() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::copy(
            "data/com.anduinos.voice-typing.gschema.xml",
            directory
                .path()
                .join("com.anduinos.voice-typing.gschema.xml"),
        )
        .unwrap();
        assert!(
            std::process::Command::new("glib-compile-schemas")
                .arg(directory.path())
                .status()
                .unwrap()
                .success()
        );
        let source =
            gio::SettingsSchemaSource::from_directory(directory.path(), None, false).unwrap();
        let schema = source.lookup(SETTINGS_SCHEMA, false).unwrap();
        let settings =
            gio::Settings::new_full(&schema, Some(&gio::memory_settings_backend_new()), None);
        assert_eq!(live_mode(&settings), "auto");
        settings.set_boolean("live-transcription", false).unwrap();
        assert_eq!(live_mode(&settings), "off");
        settings
            .set_string("live-transcription-mode", "auto")
            .unwrap();
        assert_eq!(live_mode(&settings), "auto");
        settings.set_boolean("full-tuning-pending", true).unwrap();
        let snapshot = SessionConfig::read(&settings);
        settings.set_string("model", "small").unwrap();
        assert_eq!(snapshot.model, "base");
        settings.set_uint("tuning-generation", 1).unwrap();
        snapshot.complete_full_tuning(&settings).unwrap();
        assert!(settings.boolean("full-tuning-pending"));
        SessionConfig::read(&settings)
            .complete_full_tuning(&settings)
            .unwrap();
        assert!(!settings.boolean("full-tuning-pending"));
    }
    #[test]
    fn unknown_model_never_becomes_a_path_component() {
        assert_eq!(
            resolve_model(
                "../../secret",
                Path::new("/nonexistent-user-models"),
                Path::new("/models")
            ),
            Path::new("/models/ggml-base.bin")
        );
    }
}
