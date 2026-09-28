//! Pinned public calibration audio, never microphone capture. Noise matches the
//! previous Python Random(42).gauss fixture so migration does not alter scoring.
use crate::transport::WorkerError;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::Path;

pub fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn read_bounded(path: &Path, limit: usize) -> std::io::Result<Vec<u8>> {
    let mut bytes = Vec::new();
    std::fs::File::open(path)?
        .take(limit as u64 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > limit {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "File exceeds size limit",
        ));
    }
    Ok(bytes)
}
pub fn load(directory: &Path, language: &str) -> Result<(Vec<u8>, String), WorkerError> {
    let invalid = || WorkerError::Protocol("Invalid benchmark fixture");
    let manifest: Value = serde_json::from_slice(
        &read_bounded(&directory.join("manifest.json"), 65536).map_err(|_| invalid())?,
    )
    .map_err(|_| invalid())?;
    let name = if language.starts_with("zh") {
        "zh-short.wav"
    } else {
        "en-short.wav"
    };
    let sample = manifest["samples"]
        .as_array()
        .and_then(|s| s.iter().find(|s| s["file"] == name))
        .ok_or_else(invalid)?;
    let data = read_bounded(&directory.join(name), 1024 * 1024).map_err(|_| invalid())?;
    let checksum = digest(&data);
    if sample["sha256"] != checksum {
        return Err(WorkerError::Protocol("Benchmark checksum mismatch"));
    }
    let mut reader = hound::WavReader::new(std::io::Cursor::new(data)).map_err(|_| invalid())?;
    let spec = reader.spec();
    if spec.channels != 1
        || spec.sample_rate != 16000
        || spec.bits_per_sample != 16
        || spec.sample_format != hound::SampleFormat::Int
    {
        return Err(invalid());
    }
    let samples: Result<Vec<i16>, _> = reader.samples().collect();
    let pcm = samples
        .map_err(|_| invalid())?
        .into_iter()
        .flat_map(i16::to_le_bytes)
        .collect();
    Ok((pcm, checksum))
}

// Fixed-seed MT19937 and Box–Muller compatibility, not a security RNG. Keep this
// private: its sole purpose is preserving existing benchmark PCM byte-for-byte.
struct FixtureNoise {
    state: [u32; 624],
    index: usize,
    spare: Option<f64>,
}
impl FixtureNoise {
    fn new() -> Self {
        let mut s = Self {
            state: [0; 624],
            index: 624,
            spare: None,
        };
        s.state[0] = 19650218;
        for i in 1..624 {
            s.state[i] = 1812433253u32
                .wrapping_mul(s.state[i - 1] ^ (s.state[i - 1] >> 30))
                .wrapping_add(i as u32);
        }
        let mut i = 1;
        for _ in 0..624 {
            s.state[i] = (s.state[i]
                ^ (s.state[i - 1] ^ (s.state[i - 1] >> 30)).wrapping_mul(1664525))
            .wrapping_add(42);
            i += 1;
            if i == 624 {
                s.state[0] = s.state[623];
                i = 1;
            }
        }
        for _ in 0..623 {
            s.state[i] = (s.state[i]
                ^ (s.state[i - 1] ^ (s.state[i - 1] >> 30)).wrapping_mul(1566083941))
            .wrapping_sub(i as u32);
            i += 1;
            if i == 624 {
                s.state[0] = s.state[623];
                i = 1;
            }
        }
        s.state[0] = 0x80000000;
        s
    }
    fn word(&mut self) -> u32 {
        if self.index == 624 {
            for i in 0..624 {
                let y = (self.state[i] & 0x80000000) | (self.state[(i + 1) % 624] & 0x7fffffff);
                self.state[i] = self.state[(i + 397) % 624]
                    ^ (y >> 1)
                    ^ if y & 1 != 0 { 0x9908b0df } else { 0 };
            }
            self.index = 0;
        }
        let mut y = self.state[self.index];
        self.index += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c5680;
        y ^= (y << 15) & 0xefc60000;
        y ^ (y >> 18)
    }
    fn uniform(&mut self) -> f64 {
        let a = self.word() >> 5;
        let b = self.word() >> 6;
        ((a as u64 * 67108864 + b as u64) as f64) / 9007199254740992.0
    }
    fn gaussian(&mut self) -> f64 {
        if let Some(value) = self.spare.take() {
            return value;
        }
        let angle = self.uniform() * std::f64::consts::TAU;
        let radius = (-2.0 * (1.0 - self.uniform()).ln()).sqrt();
        self.spare = Some(angle.sin() * radius);
        angle.cos() * radius
    }
}
pub fn noisy(pcm: &[u8]) -> Result<Vec<u8>, WorkerError> {
    if pcm.is_empty() || pcm.len() % 2 != 0 {
        return Err(WorkerError::Protocol("Expected nonempty S16LE PCM"));
    }
    let samples: Vec<_> = pcm
        .chunks_exact(2)
        .map(|p| i16::from_le_bytes([p[0], p[1]]))
        .collect();
    let rms =
        (samples.iter().map(|s| (*s as f64).powi(2)).sum::<f64>() / samples.len() as f64).sqrt();
    let mut generator = FixtureNoise::new();
    Ok(samples
        .into_iter()
        .flat_map(|s| {
            ((s as f64 + generator.gaussian() * rms / 10.0)
                .round_ties_even()
                .clamp(-32768.0, 32767.0) as i16)
                .to_le_bytes()
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pinned_samples_and_noise_match_python_golden_pcm() {
        for (language, expected) in [
            (
                "en",
                "592783b95a0c3274d10947cee55e7cdd249dcd1603fc2c75b766dfcb1d03ea82",
            ),
            (
                "zh-Hans",
                "d2bfabb1b706976dca893685be64370169d97e2c414f96c071f1be4905cf710f",
            ),
        ] {
            let (pcm, _) = load(Path::new("data/benchmark"), language).unwrap();
            assert_eq!(digest(&noisy(&pcm).unwrap()), expected);
        }
        assert!(noisy(&[]).is_err());
        assert!(noisy(&[1]).is_err());
    }
    #[test]
    fn corrupted_fixture_is_rejected_before_decoding() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::copy(
            "data/benchmark/manifest.json",
            dir.path().join("manifest.json"),
        )
        .unwrap();
        std::fs::write(dir.path().join("en-short.wav"), b"corrupted").unwrap();
        assert_eq!(
            load(dir.path(), "en"),
            Err(WorkerError::Protocol("Benchmark checksum mismatch"))
        );
    }
}
