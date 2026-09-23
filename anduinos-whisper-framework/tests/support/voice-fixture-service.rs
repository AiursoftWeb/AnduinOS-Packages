//! Test-only binary. Same Rust service/worker/state machine, public audio instead
//! of a microphone. Never installed in a package or built with default features.
use anduinos_whisper_framework::{
    calibration,
    runtime::BackendPaths,
    segmentation::CaptureEvent,
    service::{self, Capture},
};
use std::path::{Path, PathBuf};
use std::time::Duration;

struct FixtureCapture {
    timer: Option<glib::SourceId>,
    _vad: Option<anduinos_whisper_framework::resident::VadEngine>,
}
impl Capture for FixtureCapture {
    fn stop(&mut self, _flush: bool) {
        if let Some(timer) = self.timer.take() {
            timer.remove();
        }
        self._vad = None;
    }
    fn poll(&mut self) {}
}
impl Drop for FixtureCapture {
    fn drop(&mut self) {
        self.stop(false);
    }
}
fn artifact(name: &str) -> PathBuf {
    let path =
        PathBuf::from(std::env::var(name).expect("Missing explicit source-built test artifact"));
    assert!(path.is_file());
    path
}
fn main() {
    assert_eq!(std::env::var("ANDUINOS_DESKTOP_SMOKE").as_deref(), Ok("1"));
    let runtime = PathBuf::from(std::env::var("XDG_RUNTIME_DIR").unwrap());
    assert_eq!(runtime.file_name().unwrap(), "runtime");
    assert!(
        runtime
            .parent()
            .and_then(Path::file_name)
            .unwrap()
            .to_string_lossy()
            .starts_with("anduinos-shell-e2e.")
    );
    let fixtures = PathBuf::from(std::env::var("ANDUINOS_VOICE_FIXTURES").unwrap());
    let (pcm, _) = calibration::load(&fixtures, "en").unwrap();
    let paths = BackendPaths {
        worker: artifact("ANDUINOS_VOICE_WORKER"),
        model: Some(artifact("ANDUINOS_VOICE_MODEL")),
        vad: artifact("ANDUINOS_VAD_MODEL"),
        fixtures,
    };
    let factory = Box::new(
        move |_config,
              vad,
              shared: std::sync::Arc<anduinos_whisper_framework::runtime::Shared>,
              session| {
            let pcm = pcm.clone();
            // A repeating source avoids removing an already-destroyed SourceId on
            // Stop. Only its first tick delivers; subsequent ticks are inert.
            let mut pending = Some(pcm);
            let timer = glib::timeout_add_local(Duration::from_millis(500), move || {
                if let Some(pcm) = pending.take() {
                    shared.capture(
                        session,
                        Ok(CaptureEvent::Final {
                            pcm,
                            endpoint_ms: 800.0,
                            reason: "silence",
                        }),
                    );
                }
                glib::ControlFlow::Continue
            });
            Ok(Box::new(FixtureCapture {
                timer: Some(timer),
                _vad: vad,
            }) as Box<dyn Capture>)
        },
    );
    if let Err(error) = service::run_with(paths, factory) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
