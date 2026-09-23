//! Voice Typing backend. The D-Bus and native worker protocols remain stable.
pub mod audio;
pub mod calibration;
pub mod commands;
pub mod config;
pub mod diagnostics;
pub mod live_policy;
pub mod resident;
pub mod runtime;
pub mod segmentation;
pub mod service;
pub mod session_engine;
pub mod transport;
pub mod tuning;
pub mod work_queue;

#[cfg(test)]
#[path = "../tests/support/artifacts.rs"]
mod test_artifacts;

pub const APP_ID: &str = "com.anduinos.VoiceTyping";
pub const OBJECT_PATH: &str = "/com/anduinos/VoiceTyping";
