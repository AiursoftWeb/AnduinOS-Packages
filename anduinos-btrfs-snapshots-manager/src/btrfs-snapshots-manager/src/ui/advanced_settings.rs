use adw::prelude::*;
use gtk::{gio, glib};
use libadwaita as adw;

use crate::dbus_client::SnapshotsManagerHelperClient;
use crate::i18n::tr;

pub fn show(parent: &adw::ApplicationWindow) {
    let window = adw::PreferencesWindow::new();
    window.set_title(Some(&tr("Advanced Settings")));
    window.set_transient_for(Some(parent));
    window.set_modal(true);
    window.set_default_size(580, 620);
    let page = adw::PreferencesPage::new();

    let packages = adw::PreferencesGroup::new();
    packages.set_title(&tr("Package Changes"));
    let before = adw::SwitchRow::new();
    before.set_title(&tr("Create a system snapshot before changes"));
    let after = adw::SwitchRow::new();
    after.set_title(&tr("Create a system snapshot after successful changes"));
    packages.add(&before);
    packages.add(&after);
    page.add(&packages);

    let notifications = adw::PreferencesGroup::new();
    notifications.set_title(&tr("Notifications"));
    let before_scheduled = adw::SwitchRow::new();
    before_scheduled.set_title(&tr("Notify before a scheduled snapshot"));
    let after_success = adw::SwitchRow::new();
    after_success.set_title(&tr("Notify after any snapshot succeeds"));
    let after_cleanup = adw::SwitchRow::new();
    after_cleanup.set_title(&tr("Notify after Smart Cleanup removes snapshots"));
    after_cleanup.set_subtitle(&tr("Limited to one notification per minute"));
    notifications.add(&before_scheduled);
    notifications.add(&after_success);
    notifications.add(&after_cleanup);
    let failures = adw::ActionRow::new();
    failures.set_title(&tr("Failure notifications"));
    failures.set_subtitle(&tr("Always on"));
    failures.add_suffix(&gtk::Image::from_icon_name("emblem-ok-symbolic"));
    notifications.add(&failures);
    page.add(&notifications);

    let service = adw::PreferencesGroup::new();
    service.set_title(&tr("Background Service"));
    let status = adw::ActionRow::new();
    status.set_title(&tr("Checking…"));
    status.set_subtitle(&tr(
        "The automatic snapshot infrastructure cannot be disabled here.",
    ));
    status.add_prefix(&gtk::Image::from_icon_name("content-loading-symbolic"));
    service.add(&status);
    page.add(&service);

    let save_group = adw::PreferencesGroup::new();
    let save = gtk::Button::with_label(&tr("Save Advanced Settings"));
    save.add_css_class("suggested-action");
    save.set_halign(gtk::Align::End);
    save_group.add(&save);
    page.add(&save_group);
    window.add(&page);

    for row in [
        &before,
        &after,
        &before_scheduled,
        &after_success,
        &after_cleanup,
    ] {
        row.set_sensitive(false);
    }
    save.set_sensitive(false);
    let window_load = window.downgrade();
    let status_load = status.clone();
    let before_load = before.clone();
    let after_load = after.clone();
    let before_scheduled_load = before_scheduled.clone();
    let after_success_load = after_success.clone();
    let after_cleanup_load = after_cleanup.clone();
    let save_load = save.clone();
    glib::spawn_future_local(async move {
        let result = gio::spawn_blocking(|| {
            SnapshotsManagerHelperClient::new().and_then(|client| {
                Ok((
                    client.get_apt_snapshot_policy()?,
                    client.get_automation_config()?,
                    client.get_scheduler_status()?,
                ))
            })
        })
        .await
        .map_err(|_| anyhow::anyhow!("The settings query stopped unexpectedly"))
        .and_then(|result| result);
        if window_load.upgrade().is_none() {
            return;
        }
        match result {
            Ok(((before_value, after_value), automation, scheduler)) => {
                before_load.set_active(before_value);
                after_load.set_active(after_value);
                before_scheduled_load.set_active(automation.notifications.notify_before_scheduled);
                after_success_load.set_active(automation.notifications.notify_after_success);
                after_cleanup_load.set_active(automation.notifications.notify_after_cleanup);
                for row in [
                    &before_load,
                    &after_load,
                    &before_scheduled_load,
                    &after_success_load,
                    &after_cleanup_load,
                ] {
                    row.set_sensitive(true);
                }
                save_load.set_sensitive(true);
                status_load.set_title(&tr("Automatic snapshot service is available"));
                status_load.set_subtitle(&scheduler);
            }
            Err(problem) => {
                status_load.set_title(&tr("Automatic snapshot service needs attention"));
                status_load.set_subtitle(&problem.to_string());
            }
        }
    });

    let window_save = window.clone();
    save.connect_clicked(move |button| {
        button.set_sensitive(false);
        let apt_before = before.is_active();
        let apt_after = after.is_active();
        let notify_before = before_scheduled.is_active();
        let notify_after = after_success.is_active();
        let notify_cleanup = after_cleanup.is_active();
        button.set_label(&tr("Saving…"));
        let weak_window = window_save.downgrade();
        let weak_button = button.downgrade();
        glib::spawn_future_local(async move {
            let result = gio::spawn_blocking(move || -> anyhow::Result<()> {
                let client = SnapshotsManagerHelperClient::new()?;
                let apt = client.save_apt_snapshot_policy(apt_before, apt_after)?;
                if !apt.0 {
                    anyhow::bail!(apt.1);
                }
                let mut automation = client.get_automation_config()?;
                automation.notifications.notify_before_scheduled = notify_before;
                automation.notifications.notify_after_success = notify_after;
                automation.notifications.notify_after_cleanup = notify_cleanup;
                let saved = client.save_automation_config(&automation)?;
                if !saved.0 {
                    anyhow::bail!(saved.1);
                }
                let timer = client.restart_scheduler()?;
                if !timer.0 {
                    anyhow::bail!(timer.1);
                }
                Ok(())
            })
            .await
            .map_err(|_| anyhow::anyhow!("The settings update stopped unexpectedly"))
            .and_then(|result| result);
            let Some(window) = weak_window.upgrade() else {
                return;
            };
            match result {
                Ok(()) => {
                    window.close();
                }
                Err(problem) => {
                    if let Some(button) = weak_button.upgrade() {
                        button.set_label(&tr("Save Advanced Settings"));
                        button.set_sensitive(true);
                    }
                    show_error(&window, &problem.to_string());
                }
            }
        });
    });
    window.present();
}

fn show_error(parent: &adw::PreferencesWindow, message: &str) {
    let dialog = adw::MessageDialog::new(
        Some(parent),
        Some(&tr("Could Not Save Settings")),
        Some(message),
    );
    dialog.add_response("close", &tr("Close"));
    dialog.present();
}
