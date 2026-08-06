use std::sync::mpsc;
use std::time::Duration;

use adw::prelude::*;
use gtk::prelude::*;
use libadwaita as adw;

use crate::dbus_client::WaypointHelperClient;
use crate::i18n::{tr, trf};
use waypoint_common::{ScheduleScope, SchedulesConfig, WaypointConfig};

pub fn create(parent: &adw::ApplicationWindow) -> gtk::Widget {
    let root = gtk::Box::new(gtk::Orientation::Vertical, 0);
    let scrolled = gtk::ScrolledWindow::new();
    scrolled.set_vexpand(true);
    let clamp = adw::Clamp::new();
    clamp.set_maximum_size(760);
    let content = gtk::Box::new(gtk::Orientation::Vertical, 18);
    content.set_margin_top(24);
    content.set_margin_bottom(24);
    content.set_margin_start(18);
    content.set_margin_end(18);

    let hero = adw::StatusPage::new();
    hero.set_icon_name(Some("folder-documents-symbolic"));
    hero.set_title(&tr("Your Personal Files"));
    hero.set_description(Some(&tr(
        "Save earlier versions of files in your Home folder and bring back something you changed or deleted.",
    )));
    hero.set_vexpand(false);
    content.append(&hero);

    let protection = adw::PreferencesGroup::new();
    protection.set_title(&tr("Protection"));
    let status = adw::ActionRow::new();
    status.set_title(&tr("Checking personal file history…"));
    status.set_subtitle(&tr("This does not affect System Recovery."));
    let status_icon = gtk::Image::from_icon_name("content-loading-symbolic");
    status.add_prefix(&status_icon);
    protection.add(&status);
    let automatic_switch = adw::SwitchRow::new();
    automatic_switch.set_title(&tr("Save personal files automatically"));
    automatic_switch.set_subtitle(&tr(
        "Save every hour and keep a useful mix of hourly, daily, weekly, and monthly versions",
    ));
    automatic_switch.set_active(personal_automatic_enabled());
    protection.add(&automatic_switch);
    content.append(&protection);

    let actions = adw::PreferencesGroup::new();
    actions.set_title(&tr("What would you like to do?"));
    let save = action_row(
        &tr("Save Personal Files Now"),
        &tr("Create a saved version of everything in your Home folder"),
        "document-save-symbolic",
    );
    save.add_css_class("accent");
    actions.add(&save);
    let recover = action_row(
        &tr("Find and Recover Files"),
        &tr("Browse older versions without overwriting your current files"),
        "edit-undo-symbolic",
    );
    actions.add(&recover);
    let all_versions = action_row(
        &tr("All Saved Home Versions"),
        &tr("See every Home snapshot in one chronological list"),
        "document-open-recent-symbolic",
    );
    actions.add(&all_versions);
    let automatic = action_row(
        &tr("Advanced Protection Settings"),
        &tr("Change timing, retention, and notifications"),
        "preferences-system-time-symbolic",
    );
    actions.add(&automatic);
    let backup = action_row(
        &tr("Back Up to Another Drive"),
        &tr("Protect against disk failure, loss, or theft"),
        "drive-removable-media-symbolic",
    );
    actions.add(&backup);
    content.append(&actions);

    let note = adw::Banner::new(&tr(
        "Saved versions stay on this computer. Use another drive for protection if this disk fails.",
    ));
    note.set_revealed(true);
    content.append(&note);
    clamp.set_child(Some(&content));
    scrolled.set_child(Some(&clamp));
    root.append(&scrolled);

    refresh_status(&status, &status_icon);
    let parent_save = parent.clone();
    let status_save = status.clone();
    let icon_save = status_icon.clone();
    save.connect_activated(move |_| save_now(&parent_save, &status_save, &icon_save));
    let parent_recover = parent.clone();
    recover.connect_activated(move |_| choose_what_to_recover(&parent_recover));
    let parent_all_versions = parent.clone();
    all_versions.connect_activated(move |_| super::personal_history::show(&parent_all_versions));
    let parent_automatic = parent.clone();
    let status_automatic = status.clone();
    let icon_automatic = status_icon.clone();
    let changing_automatic = std::rc::Rc::new(std::cell::Cell::new(false));
    automatic_switch.connect_active_notify(move |switch| {
        if changing_automatic.replace(true) {
            return;
        }
        let enabled = switch.is_active();
        switch.set_sensitive(false);
        status_automatic.set_title(&tr("Applying automatic protection…"));
        status_automatic.set_subtitle(&tr("This may ask for your password."));
        icon_automatic.set_icon_name(Some("content-loading-symbolic"));

        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let _ = sender.send(set_personal_automatic(enabled));
        });
        let switch = switch.clone();
        let parent = parent_automatic.clone();
        let status = status_automatic.clone();
        let icon = icon_automatic.clone();
        let changing = changing_automatic.clone();
        glib::timeout_add_local(Duration::from_millis(80), move || {
            match receiver.try_recv() {
                Ok(Ok(())) => {
                    status.set_title(&if enabled {
                        tr("Automatic protection is on")
                    } else {
                        tr("Automatic protection is off")
                    });
                    status.set_subtitle(&if enabled {
                        tr("Personal files are saved every hour.")
                    } else {
                        tr("Use Save Personal Files Now whenever you want a saved version.")
                    });
                    icon.set_icon_name(Some(if enabled {
                        "emblem-ok-symbolic"
                    } else {
                        "dialog-warning-symbolic"
                    }));
                    switch.set_sensitive(true);
                    changing.set(false);
                    glib::ControlFlow::Break
                }
                Ok(Err(error)) => {
                    show_error(&parent, &error.to_string());
                    switch.set_active(!enabled);
                    switch.set_sensitive(true);
                    changing.set(false);
                    refresh_status(&status, &icon);
                    glib::ControlFlow::Break
                }
                Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                Err(mpsc::TryRecvError::Disconnected) => {
                    switch.set_sensitive(true);
                    changing.set(false);
                    refresh_status(&status, &icon);
                    glib::ControlFlow::Break
                }
            }
        });
    });
    let parent_advanced = parent.clone();
    automatic.connect_activated(move |_| {
        super::preferences_window::show_preferences_window(&parent_advanced)
    });
    let parent_backup = parent.clone();
    backup.connect_activated(move |_| super::external_backups::show(&parent_backup));
    root.upcast()
}

fn choose_what_to_recover(parent: &adw::ApplicationWindow) {
    let dialog = adw::MessageDialog::new(
        Some(parent),
        Some(&tr("Find an older version")),
        Some(&tr(
            "Choose a file, choose the folder where a deleted file used to be, or browse every saved version.",
        )),
    );
    dialog.add_response("cancel", &tr("Cancel"));
    dialog.add_response("all", &tr("Browse All"));
    dialog.add_response("folder", &tr("Choose Folder"));
    dialog.add_response("file", &tr("Choose File"));
    dialog.set_default_response(Some("file"));
    let parent_response = parent.clone();
    dialog.connect_response(None, move |_, response| match response {
        "file" => choose_target(&parent_response, "file"),
        "folder" => choose_target(&parent_response, "folder"),
        "all" => super::personal_history::show(&parent_response),
        _ => {}
    });
    dialog.present();
}

fn choose_target(parent: &adw::ApplicationWindow, mode: &'static str) {
    let picker = gtk::FileDialog::new();
    let title = if mode == "file" {
        tr("Choose a File to Recover")
    } else {
        tr("Choose a Folder to Browse in History")
    };
    picker.set_title(&title);
    let parent_result = parent.clone();
    let handle = move |result: Result<gtk::gio::File, glib::Error>| {
        let Ok(file) = result else { return };
        match crate::file_history_request::resolve_history_request(mode, file.uri().as_str()) {
            Ok(target) => {
                if let Some(app) = parent_result.application() {
                    super::personal_history::show_target(&app, target);
                }
            }
            Err(error) => show_error(&parent_result, &error.to_string()),
        }
    };
    if mode == "file" {
        picker.open(Some(parent), None::<&gtk::gio::Cancellable>, handle);
    } else {
        picker.select_folder(Some(parent), None::<&gtk::gio::Cancellable>, handle);
    }
}

fn personal_automatic_enabled() -> bool {
    let config = WaypointConfig::new();
    SchedulesConfig::load_from_file(&config.schedules_config)
        .unwrap_or_else(|_| SchedulesConfig::default())
        .schedules
        .iter()
        .any(|schedule| schedule.scope == ScheduleScope::Personal && schedule.enabled)
}

fn set_personal_automatic(enabled: bool) -> anyhow::Result<()> {
    let config = WaypointConfig::new();
    let mut schedules = SchedulesConfig::load_from_file(&config.schedules_config)
        .unwrap_or_else(|_| SchedulesConfig::default());
    set_personal_schedule_enabled(&mut schedules, enabled)?;
    let content = toml::to_string_pretty(&schedules)?;
    let client = WaypointHelperClient::new()?;
    let saved = client.save_schedules_config(content)?;
    if !saved.0 {
        anyhow::bail!(saved.1);
    }
    let restarted = client.restart_scheduler()?;
    if !restarted.0 {
        anyhow::bail!(restarted.1);
    }
    Ok(())
}

fn set_personal_schedule_enabled(
    schedules: &mut SchedulesConfig,
    enabled: bool,
) -> anyhow::Result<()> {
    let schedule = schedules
        .schedules
        .iter_mut()
        .find(|schedule| schedule.scope == ScheduleScope::Personal)
        .ok_or_else(|| anyhow::anyhow!("Personal file protection schedule is missing"))?;
    schedule.enabled = enabled;
    Ok(())
}

fn show_error(parent: &adw::ApplicationWindow, message: &str) {
    let dialog = adw::MessageDialog::new(
        Some(parent),
        Some(&tr("Could not change automatic protection")),
        Some(message),
    );
    dialog.add_response("close", &tr("Close"));
    dialog.present();
}

fn action_row(title: &str, subtitle: &str, icon: &str) -> adw::ActionRow {
    let row = adw::ActionRow::new();
    row.set_title(title);
    row.set_subtitle(subtitle);
    row.set_activatable(true);
    row.add_prefix(&gtk::Image::from_icon_name(icon));
    row.add_suffix(&gtk::Image::from_icon_name("go-next-symbolic"));
    row
}

fn refresh_status(row: &adw::ActionRow, icon: &gtk::Image) {
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let result = WaypointHelperClient::new().and_then(|client| {
            let engine = client.recovery_engine_status()?;
            let config = WaypointConfig::new();
            let schedules = SchedulesConfig::load_from_file(&config.schedules_config)
                .unwrap_or_else(|_| SchedulesConfig::default());
            let automatic = schedules
                .schedules
                .iter()
                .any(|schedule| schedule.scope == ScheduleScope::Personal && schedule.enabled);
            Ok((engine, automatic))
        });
        let _ = sender.send(result);
    });
    let row = row.clone();
    let icon = icon.clone();
    glib::timeout_add_local(Duration::from_millis(80), move || {
        match receiver.try_recv() {
            Ok(Ok((engine, automatic))) => {
                let mut ready: Vec<_> = engine
                    .personal_snapshots
                    .iter()
                    .filter(|snapshot| snapshot.state == "ready")
                    .collect();
                ready.sort_by_key(|snapshot| snapshot.created_at);
                if let Some(latest) = ready.last() {
                    let title = if automatic {
                        tr("Automatic protection is on")
                    } else {
                        tr("Automatic protection is off")
                    };
                    row.set_title(&title);
                    row.set_subtitle(&trf(
                        "{0} saved version(s) · Last saved {1}",
                        &[
                            &ready.len().to_string(),
                            &latest
                                .created_at
                                .with_timezone(&chrono::Local)
                                .format("%Y-%m-%d %H:%M")
                                .to_string(),
                        ],
                    ));
                    icon.set_icon_name(Some("emblem-ok-symbolic"));
                    if automatic {
                        icon.add_css_class("success");
                    }
                } else {
                    row.set_title(&tr("Personal files have not been saved yet"));
                    row.set_subtitle(&tr("Save them now or turn on automatic protection."));
                    icon.set_icon_name(Some("dialog-warning-symbolic"));
                }
                glib::ControlFlow::Break
            }
            Ok(Err(error)) => {
                row.set_title(&tr("Personal file history is unavailable"));
                row.set_subtitle(&error.to_string());
                icon.set_icon_name(Some("dialog-error-symbolic"));
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => glib::ControlFlow::Break,
        }
    });
}

fn save_now(parent: &adw::ApplicationWindow, status: &adw::ActionRow, icon: &gtk::Image) {
    status.set_title(&tr("Saving personal files…"));
    status.set_subtitle(&tr("You can continue using the computer."));
    icon.set_icon_name(Some("content-loading-symbolic"));
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let result = (|| -> anyhow::Result<()> {
            let client = WaypointHelperClient::new()?;
            let now = chrono::Local::now();
            client.create_personal_snapshot(
                format!("Personal Files · {}", now.format("%Y-%m-%d %H:%M")),
                "Saved manually".into(),
                false,
            )?;
            Ok(())
        })();
        let _ = sender.send(result);
    });
    let parent = parent.clone();
    let status = status.clone();
    let icon = icon.clone();
    glib::timeout_add_local(Duration::from_millis(80), move || {
        match receiver.try_recv() {
            Ok(Ok(())) => {
                refresh_status(&status, &icon);
                if let Some(overlay) = parent
                    .content()
                    .and_then(|widget| widget.downcast::<adw::ToastOverlay>().ok())
                {
                    overlay.add_toast(adw::Toast::new(&tr("Personal files saved")));
                }
                glib::ControlFlow::Break
            }
            Ok(Err(error)) => {
                status.set_title(&tr("Could not save personal files"));
                status.set_subtitle(&error.to_string());
                icon.set_icon_name(Some("dialog-error-symbolic"));
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => glib::ControlFlow::Break,
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn simple_switch_only_changes_personal_schedule() {
        let mut schedules = SchedulesConfig::default();
        let system_before: Vec<_> = schedules
            .schedules
            .iter()
            .filter(|schedule| schedule.scope == ScheduleScope::System)
            .map(|schedule| schedule.enabled)
            .collect();

        set_personal_schedule_enabled(&mut schedules, false).unwrap();
        assert!(
            schedules
                .schedules
                .iter()
                .filter(|schedule| schedule.scope == ScheduleScope::Personal)
                .all(|schedule| !schedule.enabled)
        );
        assert_eq!(
            system_before,
            schedules
                .schedules
                .iter()
                .filter(|schedule| schedule.scope == ScheduleScope::System)
                .map(|schedule| schedule.enabled)
                .collect::<Vec<_>>()
        );

        set_personal_schedule_enabled(&mut schedules, true).unwrap();
        assert!(
            schedules
                .schedules
                .iter()
                .any(|schedule| schedule.scope == ScheduleScope::Personal && schedule.enabled)
        );
    }
}
