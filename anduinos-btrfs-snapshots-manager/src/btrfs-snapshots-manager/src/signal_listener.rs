use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use anyhow::Result;
use futures_util::StreamExt;
use snapshots_manager_common::{DBUS_INTERFACE_NAME, DBUS_OBJECT_PATH};
use zbus::{Connection, MatchRule};

#[derive(Clone, Default)]
pub struct SnapshotSignalMonitor {
    system_generation: Arc<AtomicU64>,
    home_generation: Arc<AtomicU64>,
}

impl SnapshotSignalMonitor {
    pub fn start() -> Self {
        let monitor = Self::default();
        let worker = monitor.clone();
        std::thread::Builder::new()
            .name("btrfs-snapshots-manager-snapshot-signals".into())
            .spawn(move || {
                let runtime = match tokio::runtime::Runtime::new() {
                    Ok(runtime) => runtime,
                    Err(error) => {
                        log::error!("Could not start snapshot signal runtime: {error}");
                        return;
                    }
                };
                loop {
                    if let Err(error) = runtime.block_on(listen_for_signals(worker.clone())) {
                        log::warn!("Snapshot signal listener disconnected: {error}");
                    }
                    std::thread::sleep(Duration::from_secs(2));
                }
            })
            .expect("could not start snapshot signal listener thread");
        monitor
    }

    pub fn system_generation(&self) -> u64 {
        self.system_generation.load(Ordering::Acquire)
    }

    pub fn home_generation(&self) -> u64 {
        self.home_generation.load(Ordering::Acquire)
    }

    fn mark_system_changed(&self) {
        self.system_generation.fetch_add(1, Ordering::AcqRel);
    }

    fn mark_home_changed(&self) {
        self.home_generation.fetch_add(1, Ordering::AcqRel);
    }
}

async fn listen_for_signals(monitor: SnapshotSignalMonitor) -> Result<()> {
    let connection = Connection::system().await?;
    let rule = MatchRule::builder()
        .msg_type(zbus::message::Type::Signal)
        .interface(DBUS_INTERFACE_NAME)?
        .path(DBUS_OBJECT_PATH)?
        .build();
    let proxy = zbus::Proxy::new(
        &connection,
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
    )
    .await?;
    let _: () = proxy.call("AddMatch", &(rule.to_string(),)).await?;
    log::debug!("Snapshot signal listener connected");

    let mut stream = zbus::MessageStream::from(&connection);
    while let Some(message) = stream.next().await {
        let Ok(message) = message else {
            continue;
        };
        let header = message.header();
        let Some(member) = header.member() else {
            continue;
        };
        match member.as_str() {
            "SnapshotCreated" => monitor.mark_system_changed(),
            "PersonalSnapshotCreated" => monitor.mark_home_changed(),
            _ => {}
        }
    }
    anyhow::bail!("system bus signal stream ended")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scope_generations_are_independent() {
        let monitor = SnapshotSignalMonitor::default();
        monitor.mark_system_changed();
        assert_eq!(monitor.system_generation(), 1);
        assert_eq!(monitor.home_generation(), 0);
        monitor.mark_home_changed();
        assert_eq!(monitor.system_generation(), 1);
        assert_eq!(monitor.home_generation(), 1);
    }
}
