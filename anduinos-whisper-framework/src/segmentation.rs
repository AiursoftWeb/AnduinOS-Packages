//! Phrase boundaries use captured audio time, not callback scheduling latency.
//! The capture owner serializes calls; emitted PCM stays in memory.
use std::collections::VecDeque;
use std::time::Duration;

const BYTES_PER_SECOND: usize = 32000;
#[derive(Debug, PartialEq)]
pub enum CaptureEvent {
    Level(f64),
    Final {
        pcm: Vec<u8>,
        endpoint_ms: f64,
        reason: &'static str,
    },
    Partial(Vec<u8>),
    NoSpeech,
}
pub struct Segmenter {
    silence_seconds: f64,
    max_phrase_bytes: usize,
    partial_interval: Duration,
    phrase: Vec<u8>,
    pre_roll: VecDeque<Vec<u8>>,
    pre_roll_size: usize,
    speaking: bool,
    onset_seconds: f64,
    audio_seconds: f64,
    last_voice: f64,
    last_partial: Duration,
    last_notice: Duration,
    last_level: Duration,
}
impl Segmenter {
    pub fn new(
        silence: Duration,
        max_phrase: Duration,
        partial_interval: Duration,
        now: Duration,
    ) -> Self {
        Self {
            silence_seconds: silence.as_secs_f64(),
            max_phrase_bytes: (max_phrase.as_secs_f64() * BYTES_PER_SECOND as f64) as usize,
            partial_interval,
            phrase: Vec::new(),
            pre_roll: VecDeque::new(),
            pre_roll_size: 0,
            speaking: false,
            onset_seconds: 0.0,
            audio_seconds: 0.0,
            last_voice: 0.0,
            last_partial: Duration::ZERO,
            last_notice: now,
            last_level: Duration::ZERO,
        }
    }
    pub fn consume(&mut self, pcm: &[u8], voiced: bool, now: Duration) -> Vec<CaptureEvent> {
        if pcm.is_empty() {
            return Vec::new();
        }
        assert_eq!(pcm.len() % 2, 0, "capture must deliver S16LE samples");
        let mut events = Vec::new();
        if now.saturating_sub(self.last_level) >= Duration::from_millis(80) {
            let stride = (pcm.len() / 2 / 256).max(1);
            let (sum, count) =
                pcm.chunks_exact(2)
                    .step_by(stride)
                    .fold((0.0, 0), |(sum, count), sample| {
                        let value = i16::from_le_bytes([sample[0], sample[1]]) as f64;
                        (sum + value * value, count + 1)
                    });
            let db = 20.0 * ((sum / count as f64).sqrt().max(1.0) / 32768.0).log10();
            events.push(CaptureEvent::Level(((db + 60.0) / 55.0).clamp(0.0, 1.0)));
            self.last_level = now;
        }
        let duration = pcm.len() as f64 / BYTES_PER_SECOND as f64;
        self.audio_seconds += duration;
        if !self.speaking {
            self.pre_roll.push_back(pcm.to_vec());
            self.pre_roll_size += pcm.len();
            while self.pre_roll_size > 11200 {
                self.pre_roll_size -= self.pre_roll.pop_front().unwrap().len();
            }
        }
        self.onset_seconds = if voiced {
            self.onset_seconds + duration
        } else {
            0.0
        };
        if voiced && (self.speaking || self.onset_seconds >= 0.12) {
            if !self.speaking {
                self.speaking = true;
                self.last_partial = now;
                for frame in self.pre_roll.drain(..) {
                    self.phrase.extend(frame);
                }
                self.pre_roll_size = 0;
            } else {
                self.phrase.extend_from_slice(pcm);
            }
            self.last_voice = self.audio_seconds;
            self.last_notice = now;
        } else if self.speaking {
            self.phrase.extend_from_slice(pcm);
        }
        let silence = self.audio_seconds - self.last_voice;
        if self.speaking
            && (silence >= self.silence_seconds || self.phrase.len() >= self.max_phrase_bytes)
        {
            let reason = if self.phrase.len() >= self.max_phrase_bytes {
                "max-duration"
            } else {
                "silence"
            };
            events.push(CaptureEvent::Final {
                pcm: std::mem::take(&mut self.phrase),
                endpoint_ms: silence.max(0.0) * 1000.0,
                reason,
            });
            self.reset();
        } else if self.speaking
            && now.saturating_sub(self.last_partial) >= self.partial_interval
            && self.phrase.len() >= BYTES_PER_SECOND / 4
        {
            events.push(CaptureEvent::Partial(self.phrase.clone()));
            self.last_partial = now;
        } else if !self.speaking && now.saturating_sub(self.last_notice) >= Duration::from_secs(8) {
            events.push(CaptureEvent::NoSpeech);
            self.last_notice = now;
        }
        events
    }
    pub fn finish(&mut self, flush: bool) -> Option<CaptureEvent> {
        let result = if flush && self.speaking && !self.phrase.is_empty() {
            let mut pcm = std::mem::take(&mut self.phrase);
            pcm.resize(pcm.len().max(BYTES_PER_SECOND / 2), 0);
            Some(CaptureEvent::Final {
                pcm,
                endpoint_ms: (self.audio_seconds - self.last_voice).max(0.0) * 1000.0,
                reason: "finish",
            })
        } else {
            None
        };
        self.reset();
        result
    }
    fn reset(&mut self) {
        self.phrase.clear();
        self.pre_roll.clear();
        self.pre_roll_size = 0;
        self.speaking = false;
        self.onset_seconds = 0.0;
        self.last_voice = 0.0;
        self.last_partial = Duration::ZERO;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn segmenter() -> Segmenter {
        Segmenter::new(
            Duration::from_millis(800),
            Duration::from_secs(12),
            Duration::from_millis(800),
            Duration::ZERO,
        )
    }
    #[test]
    fn requires_sustained_onset_and_finish_pads_short_phrase() {
        let mut s = segmenter();
        s.consume(&[1; 1024], true, Duration::ZERO);
        assert!(s.finish(true).is_none());
        for _ in 0..4 {
            s.consume(&[1; 1024], true, Duration::ZERO);
        }
        match s.finish(true).unwrap() {
            CaptureEvent::Final { pcm, reason, .. } => {
                assert_eq!(pcm.len(), 16000);
                assert_eq!(reason, "finish");
                assert_eq!(&pcm[..4096], &[1; 4096]);
            }
            _ => panic!("expected final"),
        }
        assert!(s.finish(true).is_none());
    }
    #[test]
    fn endpoint_uses_audio_time_even_when_callbacks_are_delayed() {
        let mut s = segmenter();
        let mut events = Vec::new();
        for _ in 0..4 {
            s.consume(&[1; 1024], true, Duration::ZERO);
        }
        for _ in 0..26 {
            events.extend(s.consume(&[0; 1024], false, Duration::from_secs(50)));
        }
        let finals: Vec<_> = events
            .iter()
            .filter(|e| matches!(e, CaptureEvent::Final { .. }))
            .collect();
        assert_eq!(finals.len(), 1);
        match finals[0] {
            CaptureEvent::Final {
                endpoint_ms,
                reason,
                ..
            } => {
                assert!((800.0..=832.1).contains(endpoint_ms));
                assert_eq!(*reason, "silence");
            }
            _ => unreachable!(),
        }
    }
    #[test]
    fn cancel_discards_audio_and_silence_notification_is_throttled() {
        let mut s = segmenter();
        for _ in 0..4 {
            s.consume(&[1; 1024], true, Duration::ZERO);
        }
        assert!(s.finish(false).is_none());
        assert!(s.finish(true).is_none());
        assert!(
            s.consume(&[0; 1024], false, Duration::from_secs(8))
                .contains(&CaptureEvent::NoSpeech)
        );
        assert!(
            !s.consume(&[0; 1024], false, Duration::from_secs(9))
                .contains(&CaptureEvent::NoSpeech)
        );
    }
}
