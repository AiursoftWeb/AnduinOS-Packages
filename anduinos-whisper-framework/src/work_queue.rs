//! Bounded accepted final work and one replaceable preview. Shutdown wins.
use std::collections::VecDeque;
use std::sync::{Condvar, Mutex};
use std::time::Duration;

#[derive(Debug)]
pub enum Work<T> {
    Final(T),
    Partial(T),
    Quit,
}

struct Pending<T> {
    finals: VecDeque<T>,
    partial: Option<T>,
    quit: bool,
}

pub struct RecognitionQueue<T> {
    pending: Mutex<Pending<T>>,
    ready: Condvar,
    max_finals: usize,
}

impl<T> RecognitionQueue<T> {
    pub fn new(max_finals: usize) -> Self {
        Self {
            pending: Mutex::new(Pending {
                finals: VecDeque::new(),
                partial: None,
                quit: false,
            }),
            ready: Condvar::new(),
            max_finals,
        }
    }

    /// A rejected final is returned to the caller, never silently discarded.
    pub fn put(&self, work: Work<T>) -> Result<(), Work<T>> {
        let mut pending = self.pending.lock().expect("work queue poisoned");
        match work {
            Work::Quit => pending.quit = true,
            Work::Final(value) => {
                if pending.finals.len() >= self.max_finals {
                    return Err(Work::Final(value));
                }
                pending.partial = None;
                pending.finals.push_back(value);
            }
            Work::Partial(value) => {
                if !pending.finals.is_empty() {
                    return Err(Work::Partial(value));
                }
                pending.partial = Some(value);
            }
        }
        self.ready.notify_one();
        Ok(())
    }

    pub fn get(&self, timeout: Duration) -> Option<Work<T>> {
        let pending = self.pending.lock().expect("work queue poisoned");
        let (mut pending, _) = self
            .ready
            .wait_timeout_while(pending, timeout, |p| {
                !p.quit && p.finals.is_empty() && p.partial.is_none()
            })
            .expect("work queue poisoned");
        if pending.quit {
            Some(Work::Quit)
        } else if let Some(value) = pending.finals.pop_front() {
            Some(Work::Final(value))
        } else {
            pending.partial.take().map(Work::Partial)
        }
    }

    pub fn clear(&self, partial_only: bool) {
        let mut pending = self.pending.lock().expect("work queue poisoned");
        pending.partial = None;
        if !partial_only {
            pending.finals.clear();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const NOW: Duration = Duration::ZERO;

    #[test]
    fn final_backpressure_and_preview_replacement() {
        let queue = RecognitionQueue::new(2);
        queue.put(Work::Partial(1)).unwrap();
        queue.put(Work::Partial(2)).unwrap();
        assert!(matches!(queue.get(NOW), Some(Work::Partial(2))));
        queue.put(Work::Partial(3)).unwrap();
        queue.put(Work::Final(4)).unwrap();
        queue.put(Work::Final(5)).unwrap();
        assert!(matches!(queue.put(Work::Final(6)), Err(Work::Final(6))));
        assert!(queue.put(Work::Partial(7)).is_err());
        queue.clear(true);
        assert!(matches!(queue.get(NOW), Some(Work::Final(4))));
        assert!(matches!(queue.get(NOW), Some(Work::Final(5))));
        assert!(queue.get(NOW).is_none());
    }

    #[test]
    fn cancellation_does_not_clear_shutdown() {
        let queue = RecognitionQueue::new(8);
        queue.put(Work::Final(1)).unwrap();
        queue.put(Work::Quit).unwrap();
        queue.clear(false);
        assert!(matches!(queue.get(NOW), Some(Work::Quit)));
    }

    #[test]
    fn shutdown_wakes_waiting_worker() {
        let queue = std::sync::Arc::new(RecognitionQueue::<()>::new(8));
        let consumer = queue.clone();
        let thread = std::thread::spawn(move || consumer.get(Duration::from_secs(2)));
        queue.put(Work::Quit).unwrap();
        assert!(matches!(thread.join().unwrap(), Some(Work::Quit)));
    }
}
