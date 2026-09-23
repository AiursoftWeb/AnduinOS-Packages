//! Native GStreamer capture. Callbacks must enqueue events, never re-enter capture.
//! NULL state joins streaming before pending frames/VAD ownership are reclaimed.
use crate::resident::VadEngine;
use crate::segmentation::{CaptureEvent, Segmenter};
use gst::prelude::*;
use gstreamer as gst;
use std::sync::{Arc, Mutex, atomic::AtomicBool};
use std::time::{Duration, Instant};

pub struct CaptureConfig {
    pub microphone: String,
    pub noise_reduction: bool,
    pub silence: Duration,
    pub max_phrase: Duration,
    pub partial_interval: Duration,
}
impl Default for CaptureConfig {
    fn default() -> Self {
        Self {
            microphone: String::new(),
            noise_reduction: false,
            silence: Duration::from_millis(800),
            max_phrase: Duration::from_secs(12),
            partial_interval: Duration::from_millis(800),
        }
    }
}
type Notify = Arc<dyn Fn(Result<CaptureEvent, String>) + Send + Sync>;
struct StreamState {
    vad: Option<VadEngine>,
    pending: Vec<u8>,
    last_voiced: bool,
    segmenter: Segmenter,
    started: Instant,
    last_sample: Instant,
}
impl StreamState {
    fn consume(&mut self, data: &[u8]) -> Result<Vec<CaptureEvent>, String> {
        self.last_sample = Instant::now();
        let now = self.started.elapsed();
        if data.len() % 2 != 0 {
            return Err("Invalid S16LE microphone data".into());
        }
        let Some(vad) = self.vad.as_mut() else {
            // Microphone testing meters audio but never starts inference.
            return Ok(self.segmenter.consume(data, false, now));
        };
        self.pending.extend_from_slice(data);
        let mut events = Vec::new();
        let complete = self.pending.len() / VadEngine::FRAME_BYTES * VadEngine::FRAME_BYTES;
        for frame in self.pending[..complete].chunks_exact(VadEngine::FRAME_BYTES) {
            self.last_voiced = vad
                .classify(frame, &AtomicBool::new(false))
                .map_err(|e| e.to_string())?
                >= 0.5;
            events.extend(self.segmenter.consume(frame, self.last_voiced, now));
        }
        self.pending.drain(..complete);
        Ok(events)
    }
}

pub struct AudioCapture {
    pipeline: gst::Pipeline,
    state: Arc<Mutex<StreamState>>,
    notify: Notify,
    stopped: bool,
    error_reported: bool,
}
impl AudioCapture {
    /// Speech sessions provide a prepared VAD. None is microphone-test mode only.
    pub fn start(
        config: CaptureConfig,
        vad: Option<VadEngine>,
        notify: impl Fn(Result<CaptureEvent, String>) + Send + Sync + 'static,
    ) -> Result<Self, String> {
        gst::init().map_err(|e| e.to_string())?;
        let source = gst::ElementFactory::make("pipewiresrc")
            .name("microphone")
            .build()
            .map_err(|e| e.to_string())?;
        if !config.microphone.is_empty() {
            source.set_property("target-object", &config.microphone);
        }
        Self::with_source(config, vad, Arc::new(notify), source)
    }
    fn with_source(
        config: CaptureConfig,
        mut vad: Option<VadEngine>,
        notify: Notify,
        source: gst::Element,
    ) -> Result<Self, String> {
        if vad.as_mut().is_some_and(|vad| !vad.is_running()) {
            return Err("Speech detection must be prepared before opening the microphone".into());
        }
        let pipeline = gst::Pipeline::with_name("anduinos-voice-capture");
        let convert = gst::ElementFactory::make("audioconvert")
            .build()
            .map_err(|e| e.to_string())?;
        let resample = gst::ElementFactory::make("audioresample")
            .build()
            .map_err(|e| e.to_string())?;
        let caps = gst::ElementFactory::make("capsfilter")
            .property(
                "caps",
                gst::Caps::builder("audio/x-raw")
                    .field("format", "S16LE")
                    .field("layout", "interleaved")
                    .field("rate", 16000i32)
                    .field("channels", 1i32)
                    .build(),
            )
            .build()
            .map_err(|e| e.to_string())?;
        let dsp = gst::ElementFactory::make("webrtcdsp")
            .build()
            .map_err(|e| e.to_string())?;
        configure_processor(&dsp, config.noise_reduction);
        let sink = gstreamer_app::AppSink::builder()
            .sync(false)
            .max_buffers(32)
            .drop(true)
            .build();
        let state = Arc::new(Mutex::new(StreamState {
            vad,
            pending: Vec::new(),
            last_voiced: false,
            segmenter: Segmenter::new(
                config.silence,
                config.max_phrase,
                config.partial_interval,
                Duration::ZERO,
            ),
            started: Instant::now(),
            last_sample: Instant::now(),
        }));
        let callback_state = state.clone();
        let callback_notify = notify.clone();
        sink.set_callbacks(
            gstreamer_app::AppSinkCallbacks::builder()
                .new_sample(move |sink| {
                    let sample = sink.pull_sample().map_err(|_| gst::FlowError::Eos)?;
                    let buffer = sample.buffer().ok_or(gst::FlowError::Error)?;
                    let mapped = buffer.map_readable().map_err(|_| gst::FlowError::Error)?;
                    let result = callback_state.lock().unwrap().consume(mapped.as_slice());
                    match result {
                        Ok(events) => {
                            for event in events {
                                callback_notify(Ok(event));
                            }
                            Ok(gst::FlowSuccess::Ok)
                        }
                        Err(error) => {
                            callback_notify(Err(error));
                            Err(gst::FlowError::Error)
                        }
                    }
                })
                .build(),
        );
        let elements = [&source, &convert, &resample, &caps, &dsp, sink.upcast_ref()];
        pipeline.add_many(elements).map_err(|e| e.to_string())?;
        gst::Element::link_many(elements).map_err(|e| e.to_string())?;
        let mut capture = Self {
            pipeline,
            state,
            notify,
            stopped: false,
            error_reported: false,
        };
        if capture.pipeline.set_state(gst::State::Playing).is_err() {
            capture.stop(false);
            return Err("The selected microphone could not be opened".into());
        }
        Ok(capture)
    }
    /// Called periodically by the service main loop (including microphone test).
    pub fn poll(&mut self) {
        if self.stopped || self.error_reported {
            return;
        }
        let mut error = None;
        if let Some(bus) = self.pipeline.bus() {
            for message in bus.iter() {
                match message.view() {
                    gst::MessageView::Error(e) => {
                        error = Some(e.error().message().to_string());
                        break;
                    }
                    gst::MessageView::Eos(_) => {
                        error = Some("Microphone stream ended".into());
                        break;
                    }
                    _ => {}
                }
            }
        }
        if error.is_none()
            && self.state.lock().unwrap().last_sample.elapsed() > Duration::from_secs(5)
        {
            error = Some("Microphone stopped providing audio; check the selected input".into());
        }
        if let Some(error) = error {
            self.error_reported = true;
            (self.notify)(Err(error));
        }
    }
    pub fn stop(&mut self, flush: bool) {
        if self.stopped {
            return;
        }
        self.stopped = true;
        let _ = self.pipeline.set_state(gst::State::Null);
        let events = {
            let mut state = self.state.lock().unwrap();
            let mut events = Vec::new();
            let pending = std::mem::take(&mut state.pending);
            if flush && !pending.is_empty() {
                let voiced = state.last_voiced;
                let now = state.started.elapsed();
                events.extend(state.segmenter.consume(&pending, voiced, now));
            }
            if let Some(event) = state.segmenter.finish(flush) {
                events.push(event);
            }
            state.vad = None;
            events
        };
        for event in events {
            (self.notify)(Ok(event));
        }
    }
}
impl Drop for AudioCapture {
    fn drop(&mut self) {
        self.stop(false);
    }
}

fn configure_processor(dsp: &gst::Element, enabled: bool) {
    dsp.set_property("echo-cancel", false);
    if dsp.find_property("voice-detection").is_some() {
        dsp.set_property("voice-detection", false);
    }
    dsp.set_property("noise-suppression", enabled);
    dsp.set_property_from_str("noise-suppression-level", "moderate");
    dsp.set_property("gain-control", enabled);
    dsp.set_property("compression-gain-db", 6i32);
    dsp.set_property("limiter", true);
    dsp.set_property("high-pass-filter", true);
}

#[cfg(test)]
mod tests {
    use super::*;
    fn replay_native(stimulus: &[u8], noise_reduction: bool) -> Vec<CaptureEvent> {
        use crate::test_artifacts::{vad_model, worker};
        gst::init().unwrap();
        let source = gstreamer_app::AppSrc::builder()
            .format(gst::Format::Time)
            .block(true)
            .max_bytes(64000)
            .caps(
                &gst::Caps::builder("audio/x-raw")
                    .field("format", "S16LE")
                    .field("layout", "interleaved")
                    .field("rate", 16000i32)
                    .field("channels", 1i32)
                    .build(),
            )
            .build();
        let vad = VadEngine::start(&vad_model(), &worker(), &AtomicBool::new(false)).unwrap();
        let (send, recv) = std::sync::mpsc::channel();
        let mut capture = AudioCapture::with_source(
            CaptureConfig {
                noise_reduction,
                ..CaptureConfig::default()
            },
            Some(vad),
            Arc::new(move |event| {
                send.send(event).unwrap();
            }),
            source.clone().upcast(),
        )
        .unwrap();
        for (index, frame) in stimulus.chunks(320).enumerate() {
            let mut buffer = gst::Buffer::from_mut_slice(frame.to_vec());
            buffer
                .get_mut()
                .unwrap()
                .set_pts(gst::ClockTime::from_mseconds(index as u64 * 10));
            buffer
                .get_mut()
                .unwrap()
                .set_duration(gst::ClockTime::from_mseconds(10));
            source.push_buffer(buffer).unwrap();
        }
        source.end_of_stream().unwrap();
        let message = capture
            .pipeline
            .bus()
            .unwrap()
            .timed_pop_filtered(
                gst::ClockTime::from_seconds(20),
                &[gst::MessageType::Eos, gst::MessageType::Error],
            )
            .unwrap();
        assert!(
            matches!(message.view(), gst::MessageView::Eos(_)),
            "DSP pipeline failed: {message:?}"
        );
        assert!(
            capture
                .state
                .lock()
                .unwrap()
                .segmenter
                .finish(true)
                .is_none(),
            "Silence must finish phrases before EOS, not be rescued by flush"
        );
        capture.stop(false);
        recv.try_iter().map(Result::unwrap).collect()
    }
    #[test]
    #[ignore = "requires native worker/models; replays public audio, never a microphone"]
    fn native_capture_final_transcribes_public_fixture() {
        use crate::resident::{EngineConfig, ResidentEngine};
        use crate::test_artifacts::{model, worker};
        gst::init().unwrap();
        for language in ["en", "zh-Hans"] {
            let (pcm, _) =
                crate::calibration::load(std::path::Path::new("data/benchmark"), language).unwrap();
            let samples: Vec<f64> = pcm
                .chunks_exact(2)
                .map(|s| i16::from_le_bytes([s[0], s[1]]) as f64)
                .collect();
            let rms = (samples.iter().map(|s| s * s).sum::<f64>() / samples.len() as f64).sqrt();
            let gain = 32768.0 * 10.0f64.powf(-26.0 / 20.0) / rms;
            let mut stimulus = vec![0; 32000];
            stimulus.extend(samples.iter().flat_map(|s| {
                ((s * gain).round_ties_even().clamp(-32768.0, 32767.0) as i16).to_le_bytes()
            }));
            stimulus.extend(vec![0; 96000]);
            let events = replay_native(&stimulus, false);
            let mut engine = ResidentEngine::with_executable(
                EngineConfig::new(model(), language.into(), 4, "cpu".into()),
                &worker(),
            )
            .unwrap();
            let mut finals = 0;
            let mut text = String::new();
            for event in events {
                if let CaptureEvent::Final {
                    pcm,
                    reason,
                    endpoint_ms,
                } = event
                {
                    assert_eq!(reason, "silence");
                    assert!((799.9..=832.1).contains(&endpoint_ms));
                    text.push_str(&engine.transcribe(&pcm, &AtomicBool::new(false)).unwrap());
                    finals += 1;
                }
            }
            assert!(finals > 0);
            assert!(!text.is_empty());
            if language == "en" {
                assert!(text.to_lowercase().contains("festivals"));
                assert!(text.to_lowercase().contains("families"));
            }
            println!(
                "Native capture {language}: {finals} endpoint-completed phrases, nonempty transcript (withheld)"
            );
        }
    }
    #[test]
    #[ignore = "native noise replay requires worker/VAD; no microphone"]
    fn native_capture_noise_does_not_trigger_dictation() {
        // Reuse the established deterministic stimulus generator, not its Python
        // capture backend. All DSP/VAD/phrase decisions under test run in Rust.
        let script = r#"
import importlib.util,sys
spec=importlib.util.spec_from_file_location('noise','tests/benchmarks/benchmark-noise.py')
noise=importlib.util.module_from_spec(spec); spec.loader.exec_module(noise)
sys.stdout.buffer.write(noise.noise(sys.argv[1],int(sys.argv[2])))
"#;
        for shape in ["hum", "fan-like", "tapping"] {
            for level in [-50, -35, -20] {
                let generated = std::process::Command::new("python3")
                    .args(["-c", script, shape, &level.to_string()])
                    .env("PYTHONDONTWRITEBYTECODE", "1")
                    .output()
                    .unwrap();
                assert!(
                    generated.status.success(),
                    "{}",
                    String::from_utf8_lossy(&generated.stderr)
                );
                assert_eq!(generated.stdout.len(), 15 * 32000);
                for reduction in [false, true] {
                    let events = replay_native(&generated.stdout, reduction);
                    assert!(
                        !events.iter().any(|event| matches!(
                            event,
                            CaptureEvent::Final { .. } | CaptureEvent::Partial(_)
                        )),
                        "false trigger for {shape}, {level} dBFS, DSP={reduction}"
                    );
                }
            }
        }
    }

    #[test]
    fn processor_preserves_opt_in_noise_reduction_without_echo_reference() {
        gst::init().unwrap();
        let dsp = gst::ElementFactory::make("webrtcdsp").build().unwrap();
        for enabled in [false, true] {
            configure_processor(&dsp, enabled);
            assert_eq!(dsp.property::<bool>("noise-suppression"), enabled);
            assert_eq!(dsp.property::<bool>("gain-control"), enabled);
            assert!(!dsp.property::<bool>("echo-cancel"));
            assert!(dsp.property::<bool>("high-pass-filter"));
            assert!(dsp.property::<bool>("limiter"));
        }
    }
    #[test]
    fn stall_reports_once_and_stopped_capture_never_reports_stall() {
        gst::init().unwrap();
        // appsrc with no pushed buffers exercises an actual silent/stalled
        // pipeline without opening the microphone or sleeping for five seconds.
        let source = gst::ElementFactory::make("appsrc")
            .property("is-live", true)
            .build()
            .unwrap();
        let (send, recv) = std::sync::mpsc::channel();
        let mut capture = AudioCapture::with_source(
            CaptureConfig::default(),
            None,
            Arc::new(move |event| {
                let _ = send.send(event);
            }),
            source,
        )
        .unwrap();
        capture.state.lock().unwrap().last_sample = Instant::now() - Duration::from_secs(6);
        capture.poll();
        assert!(
            recv.recv_timeout(Duration::from_secs(1))
                .unwrap()
                .unwrap_err()
                .contains("stopped providing audio")
        );
        capture.poll();
        assert!(recv.try_recv().is_err());
        capture.stop(false);
        capture.error_reported = false;
        capture.poll();
        assert!(recv.try_recv().is_err());
    }
    #[test]
    fn synthetic_capture_meters_audio_and_stops_without_a_microphone() {
        gst::init().unwrap();
        let source = gst::ElementFactory::make("audiotestsrc")
            .property("is-live", true)
            .build()
            .unwrap();
        let (send, recv) = std::sync::mpsc::channel();
        let mut capture = AudioCapture::with_source(
            CaptureConfig::default(),
            None,
            Arc::new(move |event| {
                let _ = send.send(event);
            }),
            source,
        )
        .unwrap();
        let event = recv.recv_timeout(Duration::from_secs(3)).unwrap().unwrap();
        assert!(matches!(event, CaptureEvent::Level(level) if level > 0.0 && level <= 1.0));
        capture.stop(false);
        while recv.try_recv().is_ok() {}
        assert!(recv.recv_timeout(Duration::from_millis(100)).is_err());
        assert_eq!(capture.pipeline.current_state(), gst::State::Null);
    }
}
