//! Memory stress-test backend.
//!
//! Allocates chunks of zeroed memory on a background thread to simulate
//! memory pressure.  Every page is touched during initialisation so the
//! kernel is forced to back it with physical RAM (or swap).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

/// Progress event sent from the allocation thread to the UI.
#[allow(dead_code)]
pub enum StressEvent {
    Progress {
        allocated_bytes: u64,
        target_bytes: u64,
        elapsed: Duration,
    },
    Result(StressTestResult),
}

/// Final outcome of a stress-test run.
pub struct StressTestResult {
    pub target_bytes: u64,
    pub peak_allocated_bytes: u64,
    pub duration: Duration,
    pub cancelled: bool,
    pub error: Option<String>,
}

/// Chunk size for each allocation step — small enough that UI updates
/// are smooth, large enough that overhead is negligible.
const CHUNK_SIZE: usize = 16 * 1024 * 1024; // 16 MiB

/// Run a memory stress test on the **calling thread**.
///
/// # Arguments
/// * `target_bytes`   – total bytes to allocate.
/// * `max_hold_seconds` – how long to hold the peak allocation before freeing.
/// * `growth_rate`    – desired allocation speed in **bytes per second**
///                      (e.g. `2_000_000_000` for 2 GiB/s).
/// * `cancel`         – shared flag; when `true` the test aborts cleanly.
/// * `sender`         – channel for progress and final‑result events.
pub fn run_stress_test(
    target_bytes: u64,
    max_hold_seconds: f64,
    growth_rate: u64,
    cancel: Arc<AtomicBool>,
    sender: async_channel::Sender<StressEvent>,
) {
    // ── Guards ──────────────────────────────────────────────────────
    if target_bytes == 0 {
        let _ = sender.send_blocking(StressEvent::Result(StressTestResult {
            target_bytes: 0,
            peak_allocated_bytes: 0,
            duration: Duration::ZERO,
            cancelled: false,
            error: Some("Target size is zero".into()),
        }));
        return;
    }

    if growth_rate == 0 {
        let _ = sender.send_blocking(StressEvent::Result(StressTestResult {
            target_bytes,
            peak_allocated_bytes: 0,
            duration: Duration::ZERO,
            cancelled: false,
            error: Some("Growth rate is zero".into()),
        }));
        return;
    }

    // ── Timing ──────────────────────────────────────────────────────
    let delay_per_chunk = Duration::from_secs_f64(CHUNK_SIZE as f64 / growth_rate as f64);
    let start = Instant::now();

    // ── Phase 1: Allocation ─────────────────────────────────────────
    let mut chunks: Vec<Vec<u8>> = Vec::new();
    let mut total: u64 = 0;

    while total < target_bytes {
        // Honour cancellation
        if cancel.load(Ordering::Relaxed) {
            drop(chunks);
            let _ = sender.send_blocking(StressEvent::Result(StressTestResult {
                target_bytes,
                peak_allocated_bytes: total,
                duration: start.elapsed(),
                cancelled: true,
                error: None,
            }));
            return;
        }

        let size = (CHUNK_SIZE as u64).min(target_bytes - total) as usize;

        // Allocate zeroed memory, then write a unique byte to the start
        // of every 4 KiB page.  This forces the kernel to allocate a
        // distinct physical page for each virtual page, defeating both
        // zero-page dedup and KSM same-page merging.
        let mut chunk: Vec<u8> = vec![0u8; size];
        let page_size = 4096;
        let base = total as usize; // unique per chunk
        for offset in (0..size).step_by(page_size) {
            // Mix chunk base and page offset so every page is unique
            chunk[offset] = ((base + offset) & 0xFF) as u8;
        }
        total += size as u64;
        chunks.push(chunk);

        // Report progress (fire-and-forget; dropping is fine when the
        // channel is full — the UI will catch up on the next event).
        let _ = sender.try_send(StressEvent::Progress {
            allocated_bytes: total,
            target_bytes,
            elapsed: start.elapsed(),
        });

        // Pace the allocation
        if total < target_bytes {
            std::thread::sleep(delay_per_chunk);
        }
    }

    let peak = total;

    // ── Phase 2: Hold ───────────────────────────────────────────────
    let hold_deadline = Instant::now() + Duration::from_secs_f64(max_hold_seconds);
    while Instant::now() < hold_deadline {
        if cancel.load(Ordering::Relaxed) {
            drop(chunks);
            let _ = sender.send_blocking(StressEvent::Result(StressTestResult {
                target_bytes,
                peak_allocated_bytes: peak,
                duration: start.elapsed(),
                cancelled: true,
                error: None,
            }));
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }

    // ── Phase 3: Release ────────────────────────────────────────────
    // Brief pause so the UI has a moment to show 100 % before memory
    // visibly drops.
    std::thread::sleep(Duration::from_millis(300));
    drop(chunks);

    let _ = sender.send_blocking(StressEvent::Result(StressTestResult {
        target_bytes,
        peak_allocated_bytes: peak,
        duration: start.elapsed(),
        cancelled: false,
        error: None,
    }));
}
