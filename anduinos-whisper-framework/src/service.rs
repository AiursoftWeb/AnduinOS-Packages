//! Session D-Bus service. No privileged bus, shell commands or Python runtime.
use crate::audio::{AudioCapture, CaptureConfig};
use crate::commands::{apply_voice_command, remove_punctuation};
use crate::config::{self, SessionConfig};
use crate::runtime::{BackendPaths, Event, Runtime, Shared};
use crate::{APP_ID, OBJECT_PATH};
use gio::prelude::*;
use glib::variant::ToVariant;
use std::cell::RefCell;
use std::rc::Rc;
use std::sync::Arc;
use std::time::{Duration, Instant};

const SHELL: &str = "org.gnome.Shell";
#[cfg(test)]
#[path = "../tests/support/service_state.rs"]
mod tests;
pub trait Capture {
    fn stop(&mut self, flush: bool);
    fn poll(&mut self);
}
impl Capture for AudioCapture {
    fn stop(&mut self, flush: bool) {
        AudioCapture::stop(self, flush);
    }
    fn poll(&mut self) {
        AudioCapture::poll(self);
    }
}
pub type CaptureFactory = Box<
    dyn Fn(
        CaptureConfig,
        Option<crate::resident::VadEngine>,
        Arc<Shared>,
        u64,
    ) -> Result<Box<dyn Capture>, String>,
>;
pub const INTROSPECTION: &str = r#"<node><interface name="com.anduinos.VoiceTyping">
<method name="Start"/><method name="Stop"/><method name="Finish"/><method name="Quit"/>
<method name="StartTest"/><method name="StopTest"/>
<method name="ReportDelivery"><arg name="ticket" type="u" direction="in"/></method>
<method name="GetDiagnostics"><arg name="report" type="s" direction="out"/></method>
<method name="GetState"><arg name="state" type="s" direction="out"/><arg name="detail" type="s" direction="out"/></method>
<signal name="DeliveryTicket"><arg name="ticket" type="u"/></signal>
<signal name="StateChanged"><arg name="state" type="s"/><arg name="detail" type="s"/></signal>
<signal name="LevelChanged"><arg name="level" type="d"/></signal>
<signal name="Transcript"><arg name="text" type="s"/><arg name="final" type="b"/></signal>
</interface></node>"#;

struct Service {
    settings: gio::Settings,
    connection: gio::DBusConnection,
    main_loop: glib::MainLoop,
    runtime: Runtime,
    capture: Option<Box<dyn Capture>>,
    capture_factory: CaptureFactory,
    shell_owner: String,
    state: String,
    detail: String,
    finish_message: String,
    last_activity: Instant,
    countdown: Option<(u64, bool, Instant, u64)>,
    restore: Option<(u64, Instant)>,
}
impl Service {
    fn emit(&self, signal: &str, parameters: glib::Variant) {
        let _ = self
            .connection
            .emit_signal(None, OBJECT_PATH, APP_ID, signal, Some(&parameters));
    }
    fn state(&mut self, state: &str, detail: &str) {
        if state != "calibrating" {
            self.countdown = None;
        }
        self.state = state.into();
        self.detail = detail.into();
        self.emit("StateChanged", (state, detail).to_variant());
    }
    fn cue(&self) {
        if !self.settings.boolean("audio-cues") {
            return;
        }
        let _ = gio::Subprocess::newv(
            &[
                std::ffi::OsStr::new("/usr/bin/pw-play"),
                std::ffi::OsStr::new(
                    "/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga",
                ),
            ],
            gio::SubprocessFlags::STDOUT_SILENCE | gio::SubprocessFlags::STDERR_SILENCE,
        );
    }
    fn drop_capture(&mut self, flush: bool) {
        if let Some(mut capture) = self.capture.take() {
            capture.stop(flush);
        }
    }
    fn capture(
        &self,
        session: u64,
        vad: Option<crate::resident::VadEngine>,
    ) -> Result<Box<dyn Capture>, String> {
        let shared = self.runtime.shared.clone();
        (self.capture_factory)(
            CaptureConfig {
                microphone: self.settings.string("microphone").into(),
                noise_reduction: self.settings.boolean("noise-reduction"),
                ..CaptureConfig::default()
            },
            vad,
            shared,
            session,
        )
    }
    fn start(&mut self) -> Result<(), String> {
        if self.runtime.shared.active() {
            return Ok(());
        }
        self.drop_capture(false);
        self.runtime.shared.new_session(false, false);
        let config = SessionConfig::read(&self.settings);
        if !self.runtime.shared.model_installed(&config.model) {
            return Err("The selected speech model is not installed".into());
        }
        let session = self.runtime.shared.new_session(true, false);
        self.finish_message.clear();
        self.restore = None;
        self.state("preparing", "Loading speech model…");
        self.runtime.shared.prepare(session, config);
        Ok(())
    }
    fn stop(&mut self, play: bool) {
        let was_running = self.runtime.shared.active() || self.runtime.shared.testing();
        self.runtime.shared.new_session(false, false);
        self.drop_capture(false);
        self.restore = None;
        if play && was_running {
            self.cue();
        }
        self.state("idle", "Ready");
    }
    fn finish(&mut self) {
        if !self.runtime.shared.active() {
            return;
        }
        if self.capture.is_none() {
            self.stop(true);
            return;
        }
        // Capture flush admits its final phrase synchronously while active;
        // only then stop admission. Accepted work remains in the queue.
        self.drop_capture(true);
        self.runtime.shared.stop_accepting();
        self.cue();
        if self.runtime.shared.pending() > 0 {
            self.state("finishing", "Finishing recognition…");
        } else {
            self.state("idle", "Ready");
        }
    }
    fn start_test(&mut self) -> Result<(), String> {
        if self.runtime.shared.testing() {
            return Ok(());
        }
        self.stop(false);
        let session = self.runtime.shared.new_session(false, true);
        match self.capture(session, None) {
            Ok(capture) => self.capture = Some(capture),
            Err(error) => {
                self.runtime.shared.new_session(false, false);
                return Err(error);
            }
        }
        self.state("testing", "Speak to test your microphone");
        Ok(())
    }
    fn failed(&mut self, session: u64, message: String, decrement: bool) {
        if session != self.runtime.shared.current_session() {
            return;
        }
        if decrement {
            self.runtime.shared.complete_final(session);
        }
        self.runtime.shared.stop_accepting();
        self.drop_capture(false);
        self.finish_message = message.clone();
        self.state(
            if self.runtime.shared.pending() > 0 {
                "finishing"
            } else {
                "error"
            },
            &message,
        );
    }
    fn no_speech(&mut self, session: u64) {
        self.state("no-speech", "No speech detected");
        self.restore = Some((session, Instant::now() + Duration::from_millis(1800)));
    }
    fn event(&mut self, event: Event) {
        match event {
            Event::FullTuningComplete(config)=>{let _=config.complete_full_tuning(&self.settings);}
            Event::Calibrating{session,full}=>{
                if session==self.runtime.shared.current_session()&&self.runtime.shared.active()&&self.capture.is_none(){
                    let seconds=if full{60}else{10};self.countdown=Some((session,full,Instant::now()+Duration::from_secs(seconds),seconds));
                    self.state("calibrating",&format!("countdown:{}:{seconds}",if full{"full"}else{"quick"}));
                }
            }
            Event::Preparing(session)=>{if session==self.runtime.shared.current_session()&&self.runtime.shared.active()&&self.capture.is_none(){self.state("preparing","Loading speech model…");}}
            Event::Prepared{session,vad}=>{
                if session!=self.runtime.shared.current_session()||!self.runtime.shared.active(){return;}
                match self.capture(session,Some(vad)) {
                    Ok(capture)=>{self.capture=Some(capture);self.cue();self.state("listening","Listening…");}
                    Err(message)=>self.event(Event::CaptureFailed{session,message}),
                }
            }
            Event::Accepted(session)=>{if session==self.runtime.shared.current_session()&&self.runtime.shared.active(){self.state("recognizing","Recognizing…");}}
            Event::Overloaded(session)=>self.failed(session,"Recognition cannot keep up. The last phrase was not accepted; try a smaller model.".into(),false),
            Event::Level{session,level}=>{if session==self.runtime.shared.current_session(){self.emit("LevelChanged",(level,).to_variant());}}
            Event::NoSpeech(session)=>{if session==self.runtime.shared.current_session()&&self.runtime.shared.active()&&self.runtime.shared.pending()==0&&self.state=="listening"{self.no_speech(session);}}
            Event::CaptureFailed{session,message}=>{
                if session!=self.runtime.shared.current_session(){return;}
                self.runtime.shared.new_session(false,false);self.drop_capture(false);
                self.state("error",&format!("Microphone unavailable: {message}"));
            }
            Event::Failed{session,message}=>self.failed(session,message,true),
            Event::Completed{session,generation,partial,mut text,ticket,at}=>{
                if session!=self.runtime.shared.current_session(){return;}
                if !self.settings.boolean("automatic-punctuation"){text=remove_punctuation(&text);}
                if partial {
                    if self.runtime.shared.partial_valid(session,generation)&&!text.is_empty(){self.emit("Transcript",(text,false).to_variant());}
                    return;
                }
                self.runtime.shared.complete_final(session);
                let (text,stop)=apply_voice_command(&text,self.settings.boolean("voice-commands"));
                if !text.is_empty(){
                    self.runtime.shared.history.lock().unwrap().ready_for_delivery(ticket,at);
                    self.emit("DeliveryTicket",(ticket,).to_variant());self.emit("Transcript",(text.as_str(),true).to_variant());
                }
                if stop {self.stop(true);}
                else if self.runtime.shared.pending()>0{self.state(if self.runtime.shared.active(){"recognizing"}else{"finishing"},"Recognizing…");}
                else if self.runtime.shared.active(){if text.is_empty(){self.no_speech(session);}else{self.state("listening","Listening…");}}
                else if !self.runtime.shared.testing(){if self.finish_message.is_empty(){self.state("idle","Ready");}else{let message=self.finish_message.clone();self.state("error",&message);}}
            }
        }
    }
    fn tick(&mut self) {
        if let Some(capture) = self.capture.as_mut() {
            capture.poll();
        }
        if let Some((session, full, end, previous)) = self.countdown {
            let remaining = end
                .saturating_duration_since(Instant::now())
                .as_secs_f64()
                .ceil() as u64;
            if session != self.runtime.shared.current_session() || !self.runtime.shared.active() {
                self.countdown = None;
            } else if remaining == 0 {
                self.state("preparing", "Loading speech model…");
            } else if remaining != previous {
                self.countdown = Some((session, full, end, remaining));
                self.state(
                    "calibrating",
                    &format!(
                        "countdown:{}:{remaining}",
                        if full { "full" } else { "quick" }
                    ),
                );
            }
        }
        if let Some((session, at)) = self.restore {
            if Instant::now() >= at {
                self.restore = None;
                if session == self.runtime.shared.current_session()
                    && self.runtime.shared.active()
                    && self.state == "no-speech"
                {
                    self.state("listening", "Listening…");
                }
            }
        }
        if !self.runtime.shared.active()
            && !self.runtime.shared.testing()
            && self.runtime.shared.pending() == 0
            && self.last_activity.elapsed() >= Duration::from_secs(300)
        {
            self.main_loop.quit();
        }
    }
    fn method(
        &mut self,
        sender: Option<&str>,
        method: &str,
        parameters: glib::Variant,
        invocation: gio::DBusMethodInvocation,
    ) {
        self.last_activity = Instant::now();
        if ["Start", "Stop", "Finish", "Quit", "ReportDelivery"].contains(&method)
            && (self.shell_owner.is_empty() || sender != Some(self.shell_owner.as_str()))
        {
            invocation.return_dbus_error(
                &format!("{APP_ID}.AccessDenied"),
                "Dictation state is controlled by the GNOME Shell extension",
            );
            return;
        }
        let result = match method {
            "Start" => self.start(),
            "Stop" => {
                self.stop(true);
                Ok(())
            }
            "Finish" => {
                self.finish();
                Ok(())
            }
            "StartTest" => self.start_test(),
            "StopTest" => {
                self.stop(false);
                Ok(())
            }
            "GetState" => {
                invocation.return_value(Some(
                    &(self.state.as_str(), self.detail.as_str()).to_variant(),
                ));
                return;
            }
            "GetDiagnostics" => {
                let report = self.runtime.shared.history.lock().unwrap().export_json();
                invocation.return_value(Some(&(report,).to_variant()));
                return;
            }
            "ReportDelivery" => {
                if let Some((ticket,)) = parameters.get::<(u32,)>() {
                    self.runtime
                        .shared
                        .history
                        .lock()
                        .unwrap()
                        .acknowledge_delivery(ticket, Instant::now());
                }
                Ok(())
            }
            "Quit" => {
                invocation.return_value(None);
                self.main_loop.quit();
                return;
            }
            _ => {
                invocation.return_dbus_error(
                    &format!("{APP_ID}.UnknownMethod"),
                    "Unknown Voice Typing method",
                );
                return;
            }
        };
        match result {
            Ok(()) => invocation.return_value(None),
            Err(error) => {
                self.state("error", &error);
                invocation.return_dbus_error(&format!("{APP_ID}.Error"), &error);
            }
        }
    }
}

pub fn run() -> Result<(), String> {
    run_with(
        BackendPaths::default(),
        Box::new(|config, vad, shared, session| {
            AudioCapture::start(config, vad, move |event| shared.capture(session, event))
                .map(|capture| Box::new(capture) as Box<dyn Capture>)
        }),
    )
}

/// Injection point for isolated desktop tests. The installed binary always calls
/// run(), which uses PipeWire and fixed installed paths; no test flags are parsed.
pub fn run_with(paths: BackendPaths, capture_factory: CaptureFactory) -> Result<(), String> {
    let settings = config::settings()?;
    let connection = gio::bus_get_sync(gio::BusType::Session, None::<&gio::Cancellable>)
        .map_err(|e| e.to_string())?;
    let shell_owner = connection
        .call_sync(
            Some("org.freedesktop.DBus"),
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetNameOwner",
            Some(&(SHELL,).to_variant()),
            None,
            gio::DBusCallFlags::NONE,
            5000,
            None::<&gio::Cancellable>,
        )
        .ok()
        .and_then(|v| v.get::<(String,)>())
        .map(|v| v.0)
        .unwrap_or_default();
    let (runtime, events) = Runtime::with_paths(paths);
    runtime.shared.set_live_mode(config::live_mode(&settings));
    let shared = runtime.shared.clone();
    let settings_handler = settings.connect_changed(None, move |settings, _| {
        shared.set_live_mode(config::live_mode(settings))
    });
    let main_loop = glib::MainLoop::new(None, false);
    let service = Rc::new(RefCell::new(Service {
        settings: settings.clone(),
        connection: connection.clone(),
        main_loop: main_loop.clone(),
        runtime,
        capture: None,
        capture_factory,
        shell_owner,
        state: "idle".into(),
        detail: "Ready".into(),
        finish_message: String::new(),
        last_activity: Instant::now(),
        countdown: None,
        restore: None,
    }));
    let node = gio::DBusNodeInfo::for_xml(INTROSPECTION).map_err(|e| e.to_string())?;
    let weak = Rc::downgrade(&service);
    let registration = connection
        .register_object(OBJECT_PATH, &node.lookup_interface(APP_ID).unwrap())
        .method_call(move |_, sender, _, _, method, parameters, invocation| {
            if let Some(service) = weak.upgrade() {
                service
                    .borrow_mut()
                    .method(sender, method, parameters, invocation);
            }
        })
        .build()
        .map_err(|e| e.to_string())?;
    let lost_loop = main_loop.clone();
    let owner = gio::bus_own_name_on_connection(
        &connection,
        APP_ID,
        gio::BusNameOwnerFlags::DO_NOT_QUEUE,
        |_, _| {},
        move |_, _| lost_loop.quit(),
    );
    let weak = Rc::downgrade(&service);
    let vanished_loop = main_loop.clone();
    let watcher = gio::bus_watch_name_on_connection(
        &connection,
        SHELL,
        gio::BusNameWatcherFlags::NONE,
        move |_, _, owner| {
            if let Some(service) = weak.upgrade() {
                service.borrow_mut().shell_owner = owner.into();
            }
        },
        move |_, _| vanished_loop.quit(),
    );
    let weak = Rc::downgrade(&service);
    let event_task = glib::MainContext::default().spawn_local(async move {
        while let Ok(event) = events.recv().await {
            let Some(service) = weak.upgrade() else {
                break;
            };
            service.borrow_mut().event(event);
        }
    });
    let weak = Rc::downgrade(&service);
    let timer = glib::timeout_add_local(Duration::from_millis(200), move || {
        let Some(service) = weak.upgrade() else {
            return glib::ControlFlow::Break;
        };
        service.borrow_mut().tick();
        glib::ControlFlow::Continue
    });
    let signal_loop = main_loop.clone();
    let interrupt = glib_unix::unix_signal_add_local(libc::SIGINT, move || {
        signal_loop.quit();
        glib::ControlFlow::Continue
    });
    let signal_loop = main_loop.clone();
    let terminate = glib_unix::unix_signal_add_local(libc::SIGTERM, move || {
        signal_loop.quit();
        glib::ControlFlow::Continue
    });
    main_loop.run();
    timer.remove();
    interrupt.remove();
    terminate.remove();
    event_task.abort();
    settings.disconnect(settings_handler);
    service.borrow_mut().stop(false);
    service.borrow_mut().runtime.shutdown();
    gio::bus_unwatch_name(watcher);
    gio::bus_unown_name(owner);
    let _ = connection.unregister_object(registration);
    let _ = connection.flush_sync(None::<&gio::Cancellable>);
    Ok(())
}
