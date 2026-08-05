//! Unprivileged desktop-session listener for automatic recovery notifications.
//!
//! The privileged helper emits privacy-preserving events on the system bus.
//! This process owns no recovery capability: it only translates those events
//! and calls the current user's notification service on the session bus.

use std::collections::HashMap;
use std::time::Duration;

use anyhow::{Context, Result};
use futures_util::StreamExt;
use gettextrs::{TextDomain, gettext};
use waypoint_common::{DBUS_INTERFACE_NAME, DBUS_SERVICE_NAME};
use zbus::zvariant::{OwnedValue, Str};
use zbus::{MatchRule, MessageStream, Proxy};

const DOMAIN: &str = "anduinos-waypoint-gtk";
const NOTIFIER_BUS_NAME: &str = "org.anduinos.Waypoint.Notifier";
const NOTIFICATIONS_SERVICE: &str = "org.freedesktop.Notifications";
const NOTIFICATIONS_PATH: &str = "/org/freedesktop/Notifications";
const NOTIFICATIONS_INTERFACE: &str = "org.freedesktop.Notifications";
const APPLICATION_NAME: &str = "AnduinOS Waypoint";
const APPLICATION_ICON: &str = "org.anduinos.Waypoint";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RecoveryScope {
    System,
    Personal,
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
        .context("Could not connect the Waypoint notifier to the desktop session")?;
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
        .context("Could not connect the Waypoint notifier to the system bus")?;
    let rule = MatchRule::builder()
        .msg_type(zbus::message::Type::Signal)
        .sender(DBUS_SERVICE_NAME)?
        .interface(DBUS_INTERFACE_NAME)?
        .build();
    let mut messages = MessageStream::for_match_rule(rule, &system, Some(16)).await?;
    log::info!("AnduinOS Waypoint desktop notification listener started");

    while let Some(message) = messages.next().await {
        let message = match message {
            Ok(message) => message,
            Err(error) => {
                log::warn!("Could not receive a Waypoint notification event: {error}");
                continue;
            }
        };
        let header = message.header();
        let Some(member) = header.member().map(|value| value.as_str()) else {
            continue;
        };
        let rendered = match member {
            "AutomaticSnapshotCreated" => {
                let Ok((scope,)) = message.body().deserialize::<(String,)>() else {
                    log::warn!("Ignored a malformed automatic creation event");
                    continue;
                };
                RecoveryScope::parse(&scope).map(creation_notification)
            }
            "AutomaticSnapshotsDeleted" => {
                let Ok((system_deleted, personal_deleted)) =
                    message.body().deserialize::<(u64, u64)>()
                else {
                    log::warn!("Ignored a malformed automatic retention event");
                    continue;
                };
                retention_notification(system_deleted, personal_deleted)
            }
            _ => None,
        };
        let Some((title, body)) = rendered else {
            continue;
        };
        if let Err(error) = send_notification(&notifications, &title, &body).await {
            log::warn!("Could not display a Waypoint desktop notification: {error}");
        }
    }
    anyhow::bail!("The system D-Bus notification stream ended unexpectedly")
}

fn creation_notification(scope: RecoveryScope) -> (String, String) {
    match scope {
        RecoveryScope::System => (
            tr("Automatic System Recovery Point Created"),
            tr("A scheduled system recovery point was created successfully."),
        ),
        RecoveryScope::Personal => (
            tr("Personal Files History Saved"),
            tr("A scheduled Personal Files history point was created successfully."),
        ),
    }
}

fn retention_notification(system_deleted: u64, personal_deleted: u64) -> Option<(String, String)> {
    let title = tr("Automatic Recovery Cleanup");
    let body = match (system_deleted, personal_deleted) {
        (0, 0) => return None,
        (system, 0) => trf(
            "Removed {0} old automatic system recovery point(s).",
            &[&system.to_string()],
        ),
        (0, personal) => trf(
            "Removed {0} old Personal Files history point(s).",
            &[&personal.to_string()],
        ),
        (system, personal) => trf(
            "Removed {0} old system recovery point(s) and {1} old Personal Files history point(s).",
            &[&system.to_string(), &personal.to_string()],
        ),
    };
    Some((title, body))
}

async fn send_notification(proxy: &Proxy<'_>, title: &str, body: &str) -> Result<()> {
    let actions = Vec::<String>::new();
    let mut hints = HashMap::<String, OwnedValue>::new();
    hints.insert(
        "desktop-entry".into(),
        Str::from("org.anduinos.Waypoint").into(),
    );
    hints.insert("urgency".into(), 0u8.into());
    let payload = (
        APPLICATION_NAME,
        0u32,
        APPLICATION_ICON,
        title,
        body,
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

fn trf(template: &str, values: &[&str]) -> String {
    let mut translated = tr(template);
    for (index, value) in values.iter().enumerate() {
        translated = translated.replace(&format!("{{{index}}}"), value);
    }
    translated
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
    fn retention_notifications_are_aggregated_and_skip_noop_cleanup() {
        assert!(retention_notification(0, 0).is_none());
        assert!(retention_notification(3, 0).unwrap().1.contains('3'));
        let both = retention_notification(2, 4).unwrap().1;
        assert!(both.contains('2'));
        assert!(both.contains('4'));
    }
}
