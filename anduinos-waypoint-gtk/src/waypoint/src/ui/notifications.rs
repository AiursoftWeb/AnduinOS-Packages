use gio::prelude::*;
use gtk::Application;

use crate::i18n::{tr, trf};

/// Priority levels for notifications
#[derive(Debug, Clone, Copy)]
pub enum NotificationPriority {
    Low,
    Normal,
    Urgent,
}

impl NotificationPriority {
    fn to_gio_priority(self) -> gio::NotificationPriority {
        match self {
            NotificationPriority::Low => gio::NotificationPriority::Low,
            NotificationPriority::Normal => gio::NotificationPriority::Normal,
            NotificationPriority::Urgent => gio::NotificationPriority::Urgent,
        }
    }
}

/// Send a desktop notification
///
/// # Arguments
/// * `app` - The GTK application instance
/// * `title` - Notification title
/// * `body` - Notification body text
/// * `priority` - Notification priority level
pub fn send_notification(
    app: &Application,
    title: &str,
    body: &str,
    priority: NotificationPriority,
) {
    let notification = gio::Notification::new(title);
    notification.set_body(Some(body));
    notification.set_priority(priority.to_gio_priority());

    // Use application icon
    let icon = gio::ThemedIcon::new("org.anduinos.Waypoint");
    notification.set_icon(&icon);

    app.send_notification(None, &notification);
}

/// Send a notification about successful snapshot creation
pub fn notify_snapshot_created(app: &Application, snapshot_name: &str) {
    send_notification(
        app,
        &tr("Recovery Point Created"),
        &trf(
            "Recovery point “{0}” was created successfully.",
            &[snapshot_name],
        ),
        NotificationPriority::Normal,
    );
}

/// Send a notification about successful snapshot deletion
pub fn notify_snapshot_deleted(app: &Application, snapshot_name: &str) {
    send_notification(
        app,
        &tr("Recovery Point Deleted"),
        &trf("Recovery point “{0}” was deleted.", &[snapshot_name]),
        NotificationPriority::Normal,
    );
}

/// Send a notification about successful snapshot restoration
pub fn notify_snapshot_restored(app: &Application, snapshot_name: &str) {
    send_notification(
        app,
        &tr("System Restore Scheduled"),
        &trf(
            "Recovery point “{0}” will be applied at the next restart. Waypoint will keep a known-good fallback until the restored system boots successfully.",
            &[snapshot_name],
        ),
        NotificationPriority::Urgent,
    );
}

/// Send a notification about scheduled snapshot creation
pub fn notify_scheduled_snapshot(app: &Application, snapshot_name: &str) {
    send_notification(
        app,
        &tr("Automatic Recovery Point Created"),
        &trf(
            "Automatic recovery point “{0}” was created successfully.",
            &[snapshot_name],
        ),
        NotificationPriority::Low,
    );
}
