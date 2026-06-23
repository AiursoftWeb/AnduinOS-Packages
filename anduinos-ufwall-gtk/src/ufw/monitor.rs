use notify::{Watcher, RecursiveMode, EventKind};
use std::path::Path;
use std::time::Duration;
use gtk::glib;

pub struct UfwMonitor;

impl UfwMonitor {
    pub fn start<F>(callback: F)
    where
        F: Fn() + 'static,
    {
        let (tx, rx) = async_channel::unbounded();

        std::thread::spawn(move || {
            let (notify_tx, notify_rx) = std::sync::mpsc::channel();
            let mut watcher = notify::recommended_watcher(notify_tx).unwrap();
            
            let _ = watcher.watch(Path::new("/etc/ufw"), RecursiveMode::Recursive);

            let mut last_event = std::time::Instant::now();
            let debounce = Duration::from_millis(250);

            for res in notify_rx {
                match res {
                    Ok(event) => {
                        if matches!(event.kind, EventKind::Modify(_) | EventKind::Create(_) | EventKind::Remove(_)) {
                            if last_event.elapsed() > debounce {
                                last_event = std::time::Instant::now();
                                let _ = tx.send_blocking(());
                            }
                        }
                    }
                    Err(e) => println!("watch error: {:?}", e),
                }
            }
        });

        glib::spawn_future_local(async move {
            while let Ok(_) = rx.recv().await {
                callback();
            }
        });
    }
}
