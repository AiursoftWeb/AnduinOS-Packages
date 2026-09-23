//! Exercise the real service state transitions without native models or a mic.
use super::*;
use std::cell::Cell;
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};

struct PrivateBus(Child);
impl Drop for PrivateBus {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
struct SilentCapture(Rc<Cell<Option<bool>>>);
impl Capture for SilentCapture {
    fn stop(&mut self, flush: bool) {
        self.0.set(Some(flush));
    }
    fn poll(&mut self) {}
}

#[test]
fn countdown_no_speech_finish_and_stale_events_preserve_session_boundaries() {
    let directory = tempfile::tempdir().unwrap();
    std::fs::copy(
        "data/com.anduinos.voice-typing.gschema.xml",
        directory
            .path()
            .join("com.anduinos.voice-typing.gschema.xml"),
    )
    .unwrap();
    assert!(
        Command::new("glib-compile-schemas")
            .arg(directory.path())
            .status()
            .unwrap()
            .success()
    );
    let source = gio::SettingsSchemaSource::from_directory(directory.path(), None, false).unwrap();
    let schema = source.lookup(config::SETTINGS_SCHEMA, false).unwrap();
    let settings =
        gio::Settings::new_full(&schema, Some(&gio::memory_settings_backend_new()), None);
    settings.set_boolean("audio-cues", false).unwrap();

    // Never change DBUS_SESSION_BUS_ADDRESS or connect to the user's session bus.
    let mut bus = PrivateBus(
        Command::new("dbus-daemon")
            .args(["--session", "--nofork", "--print-address=1"])
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap(),
    );
    let mut address = String::new();
    BufReader::new(bus.0.stdout.take().unwrap())
        .read_line(&mut address)
        .unwrap();
    let connection = gio::DBusConnection::for_address_sync(
        address.trim(),
        gio::DBusConnectionFlags::AUTHENTICATION_CLIENT
            | gio::DBusConnectionFlags::MESSAGE_BUS_CONNECTION,
        None::<&gio::DBusAuthObserver>,
        None::<&gio::Cancellable>,
    )
    .unwrap();
    connection.set_exit_on_close(false);
    let (runtime, _events) = Runtime::with_paths(BackendPaths {
        model: Some(directory.path().join("absent-model")),
        worker: directory.path().join("absent-worker"),
        vad: directory.path().join("absent-vad"),
        fixtures: directory.path().join("absent-fixtures"),
    });
    let stopped = Rc::new(Cell::new(None));
    let factory_stopped = stopped.clone();
    let mut service = Service {
        settings,
        connection,
        main_loop: glib::MainLoop::new(None, false),
        runtime,
        capture: None,
        capture_factory: Box::new(move |_, _, _, _| {
            Ok(Box::new(SilentCapture(factory_stopped.clone())))
        }),
        shell_owner: String::new(),
        state: "idle".into(),
        detail: "Ready".into(),
        finish_message: String::new(),
        last_activity: Instant::now(),
        countdown: None,
        restore: None,
    };
    let session = service.runtime.shared.new_session(true, false);
    service.event(Event::Calibrating {
        session,
        full: true,
    });
    assert_eq!(service.detail, "countdown:full:60");
    service.countdown = Some((session, true, Instant::now(), 1));
    service.tick();
    assert_eq!(service.state, "preparing");
    assert!(service.countdown.is_none());
    service.finish(); // Finish during preparation cancels instead of opening a mic later.
    assert_eq!(service.state, "idle");
    assert!(!service.runtime.shared.active());
    service.event(Event::Calibrating {
        session,
        full: false,
    });
    service.event(Event::Preparing(session));
    assert_eq!(service.state, "idle");

    let session = service.runtime.shared.new_session(true, false);
    service.state("listening", "Listening…");
    service.event(Event::NoSpeech(session));
    assert_eq!(service.state, "no-speech");
    service.restore = Some((session, Instant::now()));
    service.tick();
    assert_eq!(service.state, "listening");
    service.capture = Some(Box::new(SilentCapture(stopped.clone())));
    service.finish();
    assert_eq!(stopped.get(), Some(true));
    assert_eq!(service.state, "idle");

    service.start_test().unwrap();
    assert_eq!(service.state, "testing");
    service.event(Event::CaptureFailed {
        session,
        message: "old failure".into(),
    });
    service.event(Event::Completed {
        session,
        generation: 0,
        partial: false,
        text: "stale transcript".into(),
        ticket: 0,
        at: Instant::now(),
    });
    assert_eq!(service.state, "testing");
    service.stop(false);
    assert_eq!(stopped.get(), Some(false));
    assert!(!service.runtime.shared.testing());
    assert!(service.start().is_err()); // Missing model never starts native work or capture.
    assert!(service.capture.is_none());
    assert!(!service.runtime.shared.active());
    service.runtime.shutdown();
    service
        .connection
        .close_sync(None::<&gio::Cancellable>)
        .unwrap();
}
