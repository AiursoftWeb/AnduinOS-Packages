//! Unprivileged desktop-session listener for automatic recovery notifications.
//!
//! The privileged helper emits privacy-preserving events on the system bus.
//! This process owns no recovery capability: it only translates those events
//! and calls the current user's notification service on the session bus.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use futures_util::StreamExt;
use gettextrs::{TextDomain, gettext};
use snapshots_manager_common::{DBUS_INTERFACE_NAME, DBUS_SERVICE_NAME};
use zbus::zvariant::{OwnedValue, Str};
use zbus::{MatchRule, MessageStream, Proxy};

const DOMAIN: &str = "anduinos-btrfs-snapshots-manager";
const NOTIFIER_BUS_NAME: &str = "org.anduinos.BtrfsSnapshotsManager.Notifier";
const NOTIFICATIONS_SERVICE: &str = "org.freedesktop.Notifications";
const NOTIFICATIONS_PATH: &str = "/org/freedesktop/Notifications";
const NOTIFICATIONS_INTERFACE: &str = "org.freedesktop.Notifications";
const APPLICATION_NAME: &str = "Disk Snapshots Manager";
const APPLICATION_ICON: &str = "org.anduinos.BtrfsSnapshotsManager";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RecoveryScope {
    System,
    Personal,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
enum NotificationUrgency {
    Low = 0,
    Normal = 1,
    Critical = 2,
}

#[derive(Debug, Eq, PartialEq)]
struct RenderedNotification {
    title: String,
    body: String,
    urgency: NotificationUrgency,
}

impl RenderedNotification {
    fn new(title: String, body: String, urgency: NotificationUrgency) -> Self {
        Self {
            title,
            body,
            urgency,
        }
    }
}

impl RecoveryScope {
    fn parse(value: &str) -> Option<Self> {
        match value {
            "system" => Some(Self::System),
            "personal" => Some(Self::Personal),
            _ => None,
        }
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    if let Err(error) = TextDomain::new(DOMAIN).codeset("UTF-8").init() {
        log::warn!("Could not initialize the {DOMAIN} translation domain: {error}");
    }

    // Owning a session name makes accidental duplicate autostart instances
    // fail closed instead of displaying every notification twice.
    let session = zbus::connection::Builder::session()?
        .name(NOTIFIER_BUS_NAME)?
        .build()
        .await
        .context("Could not connect the Disk Snapshots Manager notifier to the desktop session")?;
    let notifications = Proxy::new(
        &session,
        NOTIFICATIONS_SERVICE,
        NOTIFICATIONS_PATH,
        NOTIFICATIONS_INTERFACE,
    )
    .await
    .context("Could not create the desktop notification proxy")?;

    let system = zbus::Connection::system()
        .await
        .context("Could not connect the Disk Snapshots Manager notifier to the system bus")?;
    let rule = MatchRule::builder()
        .msg_type(zbus::message::Type::Signal)
        .sender(DBUS_SERVICE_NAME)?
        .interface(DBUS_INTERFACE_NAME)?
        .build();
    let mut messages = MessageStream::for_match_rule(rule, &system, Some(16)).await?;
    let mut last_cleanup_notification = None::<Instant>;
    log::info!("Disk Snapshots Manager desktop notification listener started");

    while let Some(message) = messages.next().await {
        let message = match message {
            Ok(message) => message,
            Err(error) => {
                log::warn!(
                    "Could not receive a Disk Snapshots Manager notification event: {error}"
                );
                continue;
            }
        };
        let header = message.header();
        let Some(member) = header.member().map(|value| value.as_str()) else {
            continue;
        };
        let rendered = match member {
            "AutomaticSnapshotStarting" => {
                let Ok((scope,)) = message.body().deserialize::<(String,)>() else {
                    continue;
                };
                RecoveryScope::parse(&scope).map(starting_notification)
            }
            "SnapshotCreationSucceeded" => {
                let Ok((scope, automatic)) = message.body().deserialize::<(String, bool)>() else {
                    log::warn!("Ignored a malformed snapshot creation event");
                    continue;
                };
                RecoveryScope::parse(&scope).map(|scope| creation_notification(scope, automatic))
            }
            "AutomaticSnapshotFailed" => {
                let Ok((scope,)) = message.body().deserialize::<(String,)>() else {
                    continue;
                };
                RecoveryScope::parse(&scope).map(failure_notification)
            }
            "AutomaticCleanupSucceeded" => {
                let Ok((system_deleted, personal_deleted)) =
                    message.body().deserialize::<(u64, u64)>()
                else {
                    continue;
                };
                if !allow_cleanup_notification(&mut last_cleanup_notification, Instant::now()) {
                    None
                } else {
                    cleanup_notification(system_deleted, personal_deleted)
                }
            }
            _ => None,
        };
        let Some(notification) = rendered else {
            continue;
        };
        if let Err(error) = send_notification(&notifications, &notification).await {
            log::warn!("Could not display a Disk Snapshots Manager desktop notification: {error}");
        }
    }
    anyhow::bail!("The system D-Bus notification stream ended unexpectedly")
}

fn starting_notification(scope: RecoveryScope) -> RenderedNotification {
    let body = match scope {
        RecoveryScope::System => tr("A scheduled system snapshot will start in 10 seconds."),
        RecoveryScope::Personal => tr("A scheduled Home snapshot will start in 10 seconds."),
    };
    RenderedNotification::new(
        tr("Automatic Snapshot Starting"),
        body,
        NotificationUrgency::Normal,
    )
}

fn failure_notification(scope: RecoveryScope) -> RenderedNotification {
    let body = match scope {
        RecoveryScope::System => tr(
            "The scheduled system snapshot could not be created. Check Disk Snapshots Manager for details.",
        ),
        RecoveryScope::Personal => tr(
            "The scheduled Home snapshot could not be created. Check Disk Snapshots Manager for details.",
        ),
    };
    RenderedNotification::new(
        tr("Automatic Snapshot Failed"),
        body,
        NotificationUrgency::Critical,
    )
}

fn creation_notification(scope: RecoveryScope, automatic: bool) -> RenderedNotification {
    let (title, body) = match (scope, automatic) {
        (RecoveryScope::System, true) => (
            tr("Automatic System Snapshot Created"),
            tr("The scheduled system snapshot was created successfully."),
        ),
        (RecoveryScope::Personal, true) => (
            tr("Home Snapshot Created"),
            tr("The scheduled Home snapshot was created successfully."),
        ),
        (RecoveryScope::System, false) => (
            tr("System Snapshot Created"),
            tr("The system snapshot was created successfully."),
        ),
        (RecoveryScope::Personal, false) => (
            tr("Home Snapshot Created"),
            tr("The Home snapshot was created successfully."),
        ),
    };
    RenderedNotification::new(title, body, NotificationUrgency::Normal)
}

fn cleanup_notification(
    system_deleted: u64,
    personal_deleted: u64,
) -> Option<RenderedNotification> {
    let body = match (system_deleted, personal_deleted) {
        (0, 0) => return None,
        (system, 0) => format!("{} {}", system, tr("old system snapshot(s) were removed.")),
        (0, personal) => format!("{} {}", personal, tr("old Home snapshot(s) were removed.")),
        (system, personal) => format!(
            "{} {} {} {}",
            system,
            tr("old system snapshot(s) and"),
            personal,
            tr("old Home snapshot(s) were removed.")
        ),
    };
    Some(RenderedNotification::new(
        tr("Automatic Cleanup Complete"),
        body,
        NotificationUrgency::Low,
    ))
}

fn allow_cleanup_notification(last: &mut Option<Instant>, now: Instant) -> bool {
    if last
        .is_some_and(|previous| now.saturating_duration_since(previous) < Duration::from_secs(60))
    {
        return false;
    }
    *last = Some(now);
    true
}

async fn send_notification(proxy: &Proxy<'_>, notification: &RenderedNotification) -> Result<()> {
    let actions = Vec::<String>::new();
    let mut hints = HashMap::<String, OwnedValue>::new();
    hints.insert(
        "desktop-entry".into(),
        Str::from("org.anduinos.BtrfsSnapshotsManager").into(),
    );
    hints.insert("urgency".into(), (notification.urgency as u8).into());
    let payload = (
        APPLICATION_NAME,
        0u32,
        APPLICATION_ICON,
        notification.title.as_str(),
        notification.body.as_str(),
        actions,
        hints,
        8_000i32,
    );
    let call = proxy.call::<_, _, u32>("Notify", &payload);
    tokio::time::timeout(Duration::from_secs(5), call)
        .await
        .context("Desktop notification service timed out")??;
    Ok(())
}

fn tr(message: &str) -> String {
    gettext(message)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn notification_scope_is_closed_and_typed() {
        assert_eq!(RecoveryScope::parse("system"), Some(RecoveryScope::System));
        assert_eq!(
            RecoveryScope::parse("personal"),
            Some(RecoveryScope::Personal)
        );
        assert_eq!(RecoveryScope::parse("other"), None);
    }

    #[test]
    fn cleanup_notifications_are_limited_to_one_per_minute() {
        let start = Instant::now();
        let mut last = None;
        assert!(allow_cleanup_notification(&mut last, start));
        assert!(!allow_cleanup_notification(
            &mut last,
            start + Duration::from_secs(59)
        ));
        assert!(allow_cleanup_notification(
            &mut last,
            start + Duration::from_secs(60)
        ));
    }

    #[test]
    fn urgency_matches_the_user_visible_event() {
        assert_eq!(
            starting_notification(RecoveryScope::System).urgency,
            NotificationUrgency::Normal
        );
        assert_eq!(
            creation_notification(RecoveryScope::Personal, true).urgency,
            NotificationUrgency::Normal
        );
        assert_eq!(
            failure_notification(RecoveryScope::System).urgency,
            NotificationUrgency::Critical
        );
        assert_eq!(
            cleanup_notification(1, 0)
                .expect("one deletion produces a notification")
                .urgency,
            NotificationUrgency::Low
        );
    }

    #[test]
    fn creation_notifications_describe_snapshots_without_save_metaphors() {
        let system = creation_notification(RecoveryScope::System, false);
        assert_eq!(system.title, "System Snapshot Created");
        assert_eq!(system.body, "The system snapshot was created successfully.");

        let personal = creation_notification(RecoveryScope::Personal, false);
        assert_eq!(personal.title, "Home Snapshot Created");
        assert_eq!(personal.body, "The Home snapshot was created successfully.");
    }
}
