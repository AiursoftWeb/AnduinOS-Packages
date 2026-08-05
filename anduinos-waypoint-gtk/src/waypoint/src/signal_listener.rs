// D-Bus signal listener for snapshot events

use anyhow::Result;
use futures_util::StreamExt;
use gtk::Application;
use gtk::glib;
use waypoint_common::*;
use zbus::{Connection, MatchRule};

use crate::ui::notifications;

#[derive(Clone, Debug)]
pub struct SnapshotCreatedEvent {
    pub snapshot_name: String,
    pub created_by: String,
}

/// Start listening for waypoint-helper D-Bus signals
///
/// This function spawns an async task that listens for D-Bus signals and
/// sends desktop notifications when snapshots are created by the scheduler.
///
pub fn start_signal_listener(app: Application) -> std::sync::mpsc::Receiver<SnapshotCreatedEvent> {
    // Create channels for thread-safe communication
    let (event_sender, event_receiver) = std::sync::mpsc::channel();
    let (snapshot_sender, snapshot_receiver) = std::sync::mpsc::channel();

    // Spawn a separate thread for async D-Bus signal listening
    std::thread::spawn(move || {
        // Run the async listener
        let runtime = tokio::runtime::Runtime::new().unwrap();
        runtime.block_on(async {
            if let Err(e) = listen_for_signals(event_sender).await {
                log::error!("Signal listener error: {e}");
            }
        });
    });

    // Set up receiver on main GTK thread
    let snapshot_sender_clone = snapshot_sender.clone();
    glib::spawn_future_local(async move {
        loop {
            if let Ok(event) = event_receiver.try_recv() {
                let evt = event;
                log::debug!("Main thread received SnapshotCreated: {evt:?}");

                if evt.created_by == "scheduler" {
                    notifications::notify_scheduled_snapshot(&app, &evt.snapshot_name);
                }

                if let Err(e) = snapshot_sender_clone.send(evt) {
                    log::error!("Failed to forward recovery-point creation event: {e}");
                }
            }

            // Sleep briefly to avoid busy waiting
            glib::timeout_future(std::time::Duration::from_millis(100)).await;
        }
    });

    snapshot_receiver
}

/// Async function to listen for waypoint-helper signals
async fn listen_for_signals(sender: std::sync::mpsc::Sender<SnapshotCreatedEvent>) -> Result<()> {
    // Connect to system bus
    let connection = Connection::system().await?;

    // Create a match rule for the SnapshotCreated signal
    let rule = MatchRule::builder()
        .msg_type(zbus::message::Type::Signal)
        .interface(DBUS_INTERFACE_NAME)?
        .member("SnapshotCreated")?
        .build();

    // Add match rule
    let proxy = zbus::Proxy::new(
        &connection,
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
    )
    .await?;

    let _: () = proxy.call("AddMatch", &(rule.to_string(),)).await?;

    log::debug!("Signal listener started for SnapshotCreated signals");

    // Create a message stream
    let mut stream = zbus::MessageStream::from(&connection);

    // Listen for messages
    while let Some(msg) = stream.next().await {
        if let Ok(msg) = msg
            && msg.message_type() == zbus::message::Type::Signal
            && let Some(member_name) = msg.header().member()
            && member_name.as_str() == "SnapshotCreated"
            && let Ok((snapshot_name, created_by)) = msg.body().deserialize::<(String, String)>()
        {
            log::debug!("Received SnapshotCreated signal: {snapshot_name} (by {created_by})");
            if let Err(e) = sender.send(SnapshotCreatedEvent {
                snapshot_name,
                created_by,
            }) {
                log::error!("Failed to forward SnapshotCreated signal: {e}");
            }
        }
    }

    Ok(())
}
