use adw::prelude::*;
use gtk::{gio, glib};
use libadwaita as adw;
use snapshots_manager_common::RetentionPolicy;

use crate::dbus_client::SnapshotsManagerHelperClient;
use crate::i18n::tr;

use super::SnapshotScope;

pub fn show(parent: &adw::ApplicationWindow, scope: SnapshotScope) {
    let window = adw::PreferencesWindow::new();
    window.set_transient_for(Some(parent));
    window.set_modal(true);
    window.set_title(Some(&match scope {
        SnapshotScope::System => tr("Automatic System Snapshots"),
        SnapshotScope::Home => tr("Automatic Home Snapshots"),
    }));
    window.set_default_size(560, 650);
    let page = adw::PreferencesPage::new();

    let automatic = adw::PreferencesGroup::new();
    automatic.set_title(&tr("Automatic Snapshots"));
    let enabled = adw::SwitchRow::new();
    enabled.set_title(&tr("Create snapshots automatically"));
    let interval = spin_row(&tr("Every"), &tr("hour(s)"), 1, 24);
    let cleanup = adw::SwitchRow::new();
    cleanup.set_title(&tr("Smart Cleanup"));
    cleanup.set_subtitle(&tr(
        "Keep useful hourly, daily, weekly, monthly, and yearly versions",
    ));
    automatic.add(&enabled);
    automatic.add(&interval);
    automatic.add(&cleanup);
    enabled
        .bind_property("active", &interval, "visible")
        .sync_create()
        .build();
    page.add(&automatic);

    let retention = adw::PreferencesGroup::new();
    retention.set_title(&tr("Keep Versions"));
    retention.set_description(Some(&tr("Newer snapshots are kept more densely.")));
    let all = spin_row(&tr("Keep every snapshot"), &tr("hours"), 1, 168);
    let daily = spin_row(&tr("Keep one per day"), &tr("days"), 1, 90);
    let weekly = spin_row(&tr("Keep one per week"), &tr("days"), 7, 365);
    let monthly = spin_row(&tr("Keep one per month"), &tr("days"), 30, 3650);
    retention.add(&all);
    retention.add(&daily);
    retention.add(&weekly);
    retention.add(&monthly);
    let yearly = adw::SwitchRow::new();
    yearly.set_title(&tr("Keep one per year forever"));
    retention.add(&yearly);
    page.add(&retention);
    cleanup
        .bind_property("active", &retention, "visible")
        .sync_create()
        .build();

    let save_group = adw::PreferencesGroup::new();
    let save = gtk::Button::with_label(&tr("Save Automatic Snapshot Settings"));
    save.add_css_class("suggested-action");
    save.set_halign(gtk::Align::End);
    save.set_sensitive(false);
    save_group.add(&save);
    page.add(&save_group);
    window.add(&page);

    set_controls_sensitive(
        &[&enabled, &cleanup, &yearly],
        &[&all, &daily, &weekly, &monthly],
        false,
    );
    interval.set_sensitive(false);
    let window_load = window.downgrade();
    let enabled_load = enabled.clone();
    let cleanup_load = cleanup.clone();
    let interval_load = interval.clone();
    let yearly_load = yearly.clone();
    let all_load = all.clone();
    let daily_load = daily.clone();
    let weekly_load = weekly.clone();
    let monthly_load = monthly.clone();
    let save_load = save.clone();
    glib::spawn_future_local(async move {
        let result = gio::spawn_blocking(|| {
            SnapshotsManagerHelperClient::new().and_then(|client| client.get_automation_config())
        })
        .await
        .map_err(|_| anyhow::anyhow!("The settings query stopped unexpectedly"))
        .and_then(|result| result);
        let Some(window_load) = window_load.upgrade() else {
            return;
        };
        match result {
            Ok(config) => {
                let policy = match scope {
                    SnapshotScope::System => config.system,
                    SnapshotScope::Home => config.home,
                };
                enabled_load.set_active(policy.is_auto_snapshot_enabled);
                interval_load.set_value(f64::from(policy.snapshot_interval_hours));
                cleanup_load.set_active(policy.is_auto_cleanup_enabled);
                all_load.set_value(f64::from(policy.keep_all_hours));
                daily_load.set_value(f64::from(policy.keep_daily_days));
                weekly_load.set_value(f64::from(policy.keep_weekly_days));
                monthly_load.set_value(f64::from(policy.keep_monthly_days));
                yearly_load.set_active(policy.keep_yearly);
                set_controls_sensitive(
                    &[&enabled_load, &cleanup_load, &yearly_load],
                    &[&all_load, &daily_load, &weekly_load, &monthly_load],
                    true,
                );
                save_load.set_sensitive(true);
                interval_load.set_sensitive(true);
            }
            Err(problem) => {
                show_error(&window_load, &problem.to_string());
            }
        }
    });

    let window_save = window.clone();
    save.connect_clicked(move |button| {
        let policy = RetentionPolicy {
            is_auto_snapshot_enabled: enabled.is_active(),
            snapshot_interval_hours: interval.value().round() as u32,
            is_auto_cleanup_enabled: cleanup.is_active(),
            keep_all_hours: all.value().round() as u32,
            keep_daily_days: daily.value().round() as u32,
            keep_weekly_days: weekly.value().round() as u32,
            keep_monthly_days: monthly.value().round() as u32,
            keep_yearly: yearly.is_active(),
        };
        if let Err(problem) = policy.validate() {
            show_error(&window_save, &problem.to_string());
            return;
        }
        button.set_sensitive(false);
        button.set_label(&tr("Saving…"));
        let weak_window = window_save.downgrade();
        let weak_button = button.downgrade();
        glib::spawn_future_local(async move {
            let result = gio::spawn_blocking(move || -> anyhow::Result<()> {
                let client = SnapshotsManagerHelperClient::new()?;
                let mut config = client.get_automation_config()?;
                match scope {
                    SnapshotScope::System => config.system = policy,
                    SnapshotScope::Home => config.home = policy,
                }
                let saved = client.save_automation_config(&config)?;
                if !saved.0 {
                    anyhow::bail!(saved.1);
                }
                let scheduler = client.restart_scheduler()?;
                if !scheduler.0 {
                    anyhow::bail!(scheduler.1);
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
                        button.set_label(&tr("Save Automatic Snapshot Settings"));
                        button.set_sensitive(true);
                    }
                    show_error(&window, &problem.to_string());
                }
            }
        });
    });
    window.present();
}

fn spin_row(title: &str, suffix: &str, min: u32, max: u32) -> adw::SpinRow {
    let adjustment = gtk::Adjustment::new(
        f64::from(min),
        f64::from(min),
        f64::from(max),
        1.0,
        10.0,
        0.0,
    );
    let row = adw::SpinRow::new(Some(&adjustment), 1.0, 0);
    row.set_title(title);
    row.set_subtitle(suffix);
    row
}

fn set_controls_sensitive(switches: &[&adw::SwitchRow], spins: &[&adw::SpinRow], sensitive: bool) {
    for row in switches {
        row.set_sensitive(sensitive);
    }
    for row in spins {
        row.set_sensitive(sensitive);
    }
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
