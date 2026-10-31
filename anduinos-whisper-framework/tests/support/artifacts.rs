//! Native test artifacts only; production service paths are not environment-controlled.
use std::path::PathBuf;

fn artifact(variable: &str, fallback: &str) -> PathBuf {
    let path = std::env::var_os(variable)
        .map(PathBuf::from)
        .unwrap_or_else(|| fallback.into());
    assert!(
        path.is_file(),
        "{variable} must point to an existing native test artifact: {}",
        path.display()
    );
    path
}
pub fn worker() -> PathBuf {
    artifact(
        "ANDUINOS_VOICE_WORKER",
        "/usr/libexec/anduinos-whisper-worker",
    )
}
pub fn model() -> PathBuf {
    artifact("ANDUINOS_VOICE_MODEL", "obj/models/ggml-base.bin")
}
pub fn vad_model() -> PathBuf {
    artifact("ANDUINOS_VAD_MODEL", "obj/models/ggml-silero-v6.2.0.bin")
}
