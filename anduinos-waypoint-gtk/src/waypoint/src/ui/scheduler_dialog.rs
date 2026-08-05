use super::dialogs;
use super::schedule_card::ScheduleCard;
use super::schedule_edit_dialog;
use crate::dbus_client::WaypointHelperClient;
use crate::i18n::{tr, trf};
use adw::prelude::*;
use gtk::prelude::*;
use gtk::{Box, Label, Orientation};
use libadwaita as adw;
use std::cell::RefCell;
use std::rc::Rc;
use waypoint_common::{Schedule, ScheduleScope, ScheduleType, SchedulesConfig};

/// Create scheduler content with lazy loading option
pub fn create_scheduler_content_lazy(parent: &adw::ApplicationWindow) -> Box {
    create_scheduler_content_with_options(parent, true)
}

fn create_scheduler_content_with_options(parent: &adw::ApplicationWindow, lazy_load: bool) -> Box {
    let content_box = Box::new(Orientation::Vertical, 24);

    // Banner for applying the persisted schedule configuration to systemd.
    let info_bar = adw::Banner::new("");
    info_bar.set_title(&tr(
        "Schedules updated. Apply the service state to keep changes after restart.",
    ));
    info_bar.set_button_label(Some(&tr("Apply Now")));
    info_bar.set_revealed(false);
    content_box.append(&info_bar);

    // Service status group
    let status_group = adw::PreferencesGroup::new();
    status_group.set_title(&tr("Service Status"));
    content_box.append(&status_group);

    let status_row = adw::ActionRow::new();
    status_row.set_title(&tr("Scheduler Service"));

    // Create box to hold icon and text
    let status_box = gtk::Box::new(gtk::Orientation::Horizontal, 6);

    let status_icon = gtk::Image::new();
    status_icon.set_pixel_size(16);
    status_icon.set_valign(gtk::Align::Center);
    status_box.append(&status_icon);

    let initial_status = if lazy_load {
        tr("Not loaded")
    } else {
        tr("Checking…")
    };
    let status_label = Label::new(Some(&initial_status));
    status_label.add_css_class("dim-label");
    status_label.set_valign(gtk::Align::Center);
    status_box.append(&status_label);

    status_row.add_suffix(&status_box);
    status_group.add(&status_row);

    // Last snapshot row
    let last_snapshot_row = adw::ActionRow::new();
    last_snapshot_row.set_title(&tr("Last Automatic Recovery Point"));
    let initial_last = if lazy_load {
        tr("Not loaded")
    } else {
        tr("Checking…")
    };
    let last_snapshot_label = Label::new(Some(&initial_last));
    last_snapshot_label.add_css_class("dim-label");
    last_snapshot_label.set_valign(gtk::Align::Center);
    last_snapshot_row.add_suffix(&last_snapshot_label);
    status_group.add(&last_snapshot_row);

    // Load current config
    let schedules_config = load_schedules_config();

    // Schedules section (using PreferencesGroup like Service Status)
    let schedules_group = adw::PreferencesGroup::new();
    schedules_group.set_title(&tr("Recovery Point Schedules"));
    schedules_group.set_description(Some(&tr(
        "Enable multiple schedules with independent retention policies",
    )));
    content_box.append(&schedules_group);

    // Container for schedule cards
    let cards_box = Box::new(Orientation::Vertical, 0);
    schedules_group.add(&cards_box);

    // Store schedule cards for later access
    let schedule_cards: Rc<RefCell<Vec<Rc<RefCell<ScheduleCard>>>>> =
        Rc::new(RefCell::new(Vec::new()));

    // Each scope has an independent schedule namespace. In particular this
    // permits both hourly System recovery and hourly Personal Files history.
    for schedule in schedules_config.schedules.clone() {
        let card = Rc::new(RefCell::new(ScheduleCard::new(schedule.clone())));
        cards_box.append(card.borrow().widget());

        // Wire up edit button
        let card_clone = card.clone();
        let info_bar_clone = info_bar.clone();
        let schedule_cards_for_edit = schedule_cards.clone();
        let parent_for_edit = parent.clone();

        card.borrow().edit_button().connect_clicked(move |_| {
            let dialog = schedule_edit_dialog::create_schedule_edit_dialog(
                &parent_for_edit,
                card_clone.borrow().schedule().clone(),
            );

            let card_for_close = card_clone.clone();
            let info_bar_for_close = info_bar_clone.clone();
            let schedule_cards_for_save = schedule_cards_for_edit.clone();
            let parent_for_save = parent_for_edit.clone();

            dialog.connect_close_request(move |dialog| {
                // Extract edited schedule from dialog
                if let Some(mut edited_schedule) =
                    schedule_edit_dialog::extract_schedule_from_dialog(dialog)
                {
                    // Preserve the enabled state from the switch
                    let enabled = card_for_close.borrow().schedule().enabled;
                    edited_schedule.enabled = enabled;

                    // Update the card
                    card_for_close.borrow_mut().set_schedule(edited_schedule);

                    // Auto-save all schedules and show InfoBar
                    save_all_schedules_from_cards(&parent_for_save, &schedule_cards_for_save);
                    info_bar_for_close.set_revealed(true);
                }
                gtk::glib::Propagation::Proceed
            });

            dialog.present();
        });

        // Wire up enable switch
        let card_clone = card.clone();
        let schedule_cards_clone = schedule_cards.clone();
        let info_bar_clone2 = info_bar.clone();
        let parent_for_toggle = parent.clone();

        card.borrow()
            .enable_switch()
            .connect_state_set(move |_, state| {
                let mut card_ref = card_clone.borrow_mut();
                let mut schedule = card_ref.schedule().clone();
                schedule.enabled = state;
                card_ref.set_schedule(schedule);

                // If enabled, update the data for this card
                if state {
                    let cards_for_update = schedule_cards_clone.clone();
                    update_schedule_cards_data(&cards_for_update);
                }

                // Auto-save when toggling schedules
                drop(card_ref); // Release the borrow before saving
                save_all_schedules_from_cards(&parent_for_toggle, &schedule_cards_clone);
                info_bar_clone2.set_revealed(true);

                gtk::glib::Propagation::Proceed
            });

        schedule_cards.borrow_mut().push(card);
    }

    // Wire up the apply button.
    let parent_for_restart = parent.clone();
    let info_bar_for_restart = info_bar.clone();
    let status_label_for_restart = status_label.clone();
    let status_icon_for_restart = status_icon.clone();

    info_bar.connect_button_clicked(move |_| {
        apply_scheduler_service(
            &parent_for_restart,
            &status_label_for_restart,
            &status_icon_for_restart,
        );

        // Hide the info bar after restart
        info_bar_for_restart.set_revealed(false);
    });

    // Update status in thread to avoid blocking UI (only if not lazy loading)
    if !lazy_load {
        update_service_status(&status_label, &status_icon);
        update_last_snapshot(&last_snapshot_label);

        // Initial update of card data
        update_schedule_cards_data(&schedule_cards);

        // Set up periodic refresh every 60 seconds
        let schedule_cards_for_refresh = schedule_cards.clone();
        gtk::glib::timeout_add_local(std::time::Duration::from_secs(60), move || {
            update_schedule_cards_data(&schedule_cards_for_refresh);
            gtk::glib::ControlFlow::Continue
        });
    } else {
        // Store labels, icon, and cards in content_box data for later lazy loading
        unsafe {
            content_box.set_data("status_label", status_label.clone());
            content_box.set_data("status_icon", status_icon.clone());
            content_box.set_data("last_snapshot_label", last_snapshot_label.clone());
            content_box.set_data("schedule_cards", schedule_cards.clone());
        }
    }

    content_box
}

/// Calculate next run time for a schedule
fn calculate_next_run(schedule: &Schedule) -> String {
    use chrono::{Datelike, Duration, Local, Timelike};

    if !schedule.enabled {
        return tr("Schedule disabled");
    }

    let now = Local::now();

    match schedule.schedule_type {
        ScheduleType::Hourly => {
            let next = now + Duration::hours(1);
            let next = next.with_minute(0).unwrap().with_second(0).unwrap();
            format_relative_time(&next.with_timezone(&chrono::Utc))
        }
        ScheduleType::Daily | ScheduleType::Weekly | ScheduleType::Monthly => {
            if let Some(ref time_str) = schedule.time {
                let parts: Vec<&str> = time_str.split(':').collect();
                if parts.len() == 2
                    && let (Ok(hour), Ok(minute)) =
                        (parts[0].parse::<u32>(), parts[1].parse::<u32>())
                {
                    let mut next = now;

                    match schedule.schedule_type {
                        ScheduleType::Daily => {
                            // Set to today at the specified time
                            next = next
                                .with_hour(hour)
                                .unwrap()
                                .with_minute(minute)
                                .unwrap()
                                .with_second(0)
                                .unwrap();

                            // If that time has passed, move to tomorrow
                            if next <= now {
                                next += Duration::days(1);
                            }
                        }
                        ScheduleType::Weekly => {
                            if let Some(target_day) = schedule.day_of_week {
                                let current_day = now.weekday().num_days_from_sunday();
                                let target_day = target_day as u32;

                                let mut days_until = (target_day + 7 - current_day) % 7;
                                if days_until == 0 {
                                    // Today is the target day - check if time has passed
                                    let target_time = now
                                        .with_hour(hour)
                                        .unwrap()
                                        .with_minute(minute)
                                        .unwrap()
                                        .with_second(0)
                                        .unwrap();
                                    if target_time <= now {
                                        days_until = 7; // Next week
                                    }
                                }

                                next = now + Duration::days(days_until as i64);
                                next = next
                                    .with_hour(hour)
                                    .unwrap()
                                    .with_minute(minute)
                                    .unwrap()
                                    .with_second(0)
                                    .unwrap();
                            }
                        }
                        ScheduleType::Monthly => {
                            if let Some(target_day) = schedule.day_of_month {
                                next = next
                                    .with_day(target_day as u32)
                                    .unwrap_or(now)
                                    .with_hour(hour)
                                    .unwrap()
                                    .with_minute(minute)
                                    .unwrap()
                                    .with_second(0)
                                    .unwrap();

                                // If that time has passed, move to next month
                                if next <= now {
                                    // Add a month
                                    if next.month() == 12 {
                                        next = next
                                            .with_year(next.year() + 1)
                                            .unwrap()
                                            .with_month(1)
                                            .unwrap();
                                    } else {
                                        next = next.with_month(next.month() + 1).unwrap();
                                    }
                                }
                            }
                        }
                        _ => {}
                    }

                    return format_relative_time(&next.with_timezone(&chrono::Utc));
                }
            }
            tr("Configuration error")
        }
    }
}

/// Format a future time as relative string
fn format_relative_time(time: &chrono::DateTime<chrono::Utc>) -> String {
    let now = chrono::Utc::now();
    let duration = time.signed_duration_since(now);

    if duration.num_days() > 1 {
        trf("in {0} days", &[&duration.num_days().to_string()])
    } else if duration.num_days() == 1 {
        let local_time = time.with_timezone(&chrono::Local);
        trf(
            "tomorrow at {0}",
            &[&local_time.format("%H:%M").to_string()],
        )
    } else if duration.num_hours() > 0 {
        if duration.num_hours() == 1 && duration.num_minutes() % 60 < 30 {
            trf("in {0} hour", &[&duration.num_hours().to_string()])
        } else {
            trf("in {0} hours", &[&duration.num_hours().to_string()])
        }
    } else if duration.num_minutes() > 0 {
        trf("in {0} minutes", &[&duration.num_minutes().to_string()])
    } else {
        tr("very soon")
    }
}

/// Find last snapshot for a given schedule
fn find_last_snapshot(
    deployments: &[crate::dbus_client::RecoveryDeployment],
    schedule: &Schedule,
) -> Option<chrono::DateTime<chrono::Utc>> {
    let prefix = if schedule.prefix.is_empty() {
        match schedule.schedule_type {
            ScheduleType::Hourly => "hourly",
            ScheduleType::Daily => "daily",
            ScheduleType::Weekly => "weekly",
            ScheduleType::Monthly => "monthly",
        }
    } else {
        &schedule.prefix
    };

    let matching: Vec<_> = deployments
        .iter()
        .filter(|deployment| {
            deployment.kind == "automatic" && deployment.schedule_id.as_deref() == Some(prefix)
        })
        .collect();

    log::debug!(
        "Looking for snapshots with prefix '{}': found {} matches",
        prefix,
        matching.len()
    );
    if !matching.is_empty() {
        log::debug!(
            "Sample recovery-point titles: {:?}",
            matching
                .iter()
                .take(3)
                .map(|deployment| &deployment.title)
                .collect::<Vec<_>>()
        );
    }

    matching
        .iter()
        .map(|deployment| deployment.created_at)
        .max()
}

/// Build sparkline data from snapshot history
fn build_sparkline_data(
    deployments: &[crate::dbus_client::RecoveryDeployment],
    schedule: &Schedule,
    max_runs: usize,
) -> Vec<bool> {
    let prefix = if schedule.prefix.is_empty() {
        match schedule.schedule_type {
            ScheduleType::Hourly => "hourly",
            ScheduleType::Daily => "daily",
            ScheduleType::Weekly => "weekly",
            ScheduleType::Monthly => "monthly",
        }
    } else {
        &schedule.prefix
    };

    // Filter recovery points by their validated schedule identity. Titles are
    // presentation data and must never drive retention or history ownership.
    let mut scheduled_deployments: Vec<_> = deployments
        .iter()
        .filter(|deployment| {
            deployment.kind == "automatic" && deployment.schedule_id.as_deref() == Some(prefix)
        })
        .collect();

    log::debug!(
        "Building sparkline for prefix '{}': found {} matching snapshots out of {} total",
        prefix,
        scheduled_deployments.len(),
        deployments.len()
    );

    if scheduled_deployments.is_empty() {
        // No snapshots yet, return empty
        return Vec::new();
    }

    // Sort by timestamp (newest first)
    scheduled_deployments.sort_by_key(|deployment| std::cmp::Reverse(deployment.created_at));

    // Take the last N snapshots (actual history, not expected slots)
    // All snapshots that exist are considered "successful" (green dots)
    // We just show the most recent N snapshots
    let runs: Vec<bool> = scheduled_deployments
        .iter()
        .take(max_runs)
        .map(|_| true) // All existing snapshots are successes
        .collect();

    log::debug!(
        "Sparkline for '{}': showing {} recent snapshots",
        prefix,
        runs.len()
    );

    runs
}

fn find_last_personal_snapshot(
    snapshots: &[crate::dbus_client::PersonalSnapshot],
    schedule: &Schedule,
) -> Option<chrono::DateTime<chrono::Utc>> {
    snapshots
        .iter()
        .filter(|snapshot| {
            snapshot.kind == "automatic"
                && snapshot.schedule_id.as_deref() == Some(schedule.prefix.as_str())
        })
        .map(|snapshot| snapshot.created_at)
        .max()
}

fn build_personal_sparkline_data(
    snapshots: &[crate::dbus_client::PersonalSnapshot],
    schedule: &Schedule,
    max_runs: usize,
) -> Vec<bool> {
    let mut matching = snapshots
        .iter()
        .filter(|snapshot| {
            snapshot.kind == "automatic"
                && snapshot.schedule_id.as_deref() == Some(schedule.prefix.as_str())
        })
        .collect::<Vec<_>>();
    matching.sort_by_key(|snapshot| std::cmp::Reverse(snapshot.created_at));
    matching.into_iter().take(max_runs).map(|_| true).collect()
}

/// Update schedule cards with live data (next run, last run, sparklines)
fn update_schedule_cards_data(schedule_cards: &Rc<RefCell<Vec<Rc<RefCell<ScheduleCard>>>>>) {
    let schedule_cards_clone = schedule_cards.clone();
    let (tx, rx) = std::sync::mpsc::channel();

    // Fetch snapshots in background thread
    std::thread::spawn(move || {
        let status = match WaypointHelperClient::new() {
            Ok(client) => match client.recovery_engine_status() {
                Ok(status) => Some(status),
                Err(e) => {
                    log::error!("Failed to list recovery deployments: {e}");
                    None
                }
            },
            Err(e) => {
                log::error!("Failed to connect to helper: {e}");
                None
            }
        };

        let _ = tx.send(status);
    });

    // Poll for result and update UI from main thread
    gtk::glib::timeout_add_local(std::time::Duration::from_millis(50), move || {
        match rx.try_recv() {
            Ok(Some(status)) => {
                log::debug!(
                    "Received {} system and {} personal history points for card update",
                    status.deployments.len(),
                    status.personal_snapshots.len()
                );

                for card_rc in schedule_cards_clone.borrow().iter() {
                    let mut card = card_rc.borrow_mut();
                    let schedule = card.schedule().clone();

                    log::debug!(
                        "Updating card for {} schedule (enabled: {})",
                        schedule.schedule_type.as_str(),
                        schedule.enabled
                    );

                    // Only update data for enabled schedules
                    if !schedule.enabled {
                        continue;
                    }

                    // Update next run time
                    let next_run = calculate_next_run(&schedule);
                    card.set_next_run(&next_run);

                    // Update last run time
                    let last_time = match schedule.scope {
                        ScheduleScope::System => find_last_snapshot(&status.deployments, &schedule),
                        ScheduleScope::Personal => {
                            find_last_personal_snapshot(&status.personal_snapshots, &schedule)
                        }
                    };
                    if let Some(last_time) = last_time {
                        let duration = chrono::Utc::now().signed_duration_since(last_time);
                        let time_str = if duration.num_days() > 0 {
                            trf("{0} days ago", &[&duration.num_days().to_string()])
                        } else if duration.num_hours() > 0 {
                            trf("{0} hours ago", &[&duration.num_hours().to_string()])
                        } else if duration.num_minutes() > 0 {
                            trf("{0} minutes ago", &[&duration.num_minutes().to_string()])
                        } else {
                            tr("Just now")
                        };
                        card.set_last_run(&time_str, true);
                    } else {
                        card.set_last_run(&tr("Never"), false);
                    }

                    // Build and populate sparkline data
                    let max_runs = match schedule.schedule_type {
                        ScheduleType::Hourly => 24,
                        ScheduleType::Daily => 30,
                        ScheduleType::Weekly => 12,
                        ScheduleType::Monthly => 12,
                    };

                    let sparkline_runs = match schedule.scope {
                        ScheduleScope::System => {
                            build_sparkline_data(&status.deployments, &schedule, max_runs)
                        }
                        ScheduleScope::Personal => build_personal_sparkline_data(
                            &status.personal_snapshots,
                            &schedule,
                            max_runs,
                        ),
                    };
                    for success in sparkline_runs {
                        card.add_sparkline_run(success);
                    }
                }
                gtk::glib::ControlFlow::Break
            }
            Ok(None) => gtk::glib::ControlFlow::Break,
            Err(std::sync::mpsc::TryRecvError::Empty) => {
                // Still waiting for data
                gtk::glib::ControlFlow::Continue
            }
            Err(std::sync::mpsc::TryRecvError::Disconnected) => {
                log::error!("Channel disconnected while waiting for snapshots");
                gtk::glib::ControlFlow::Break
            }
        }
    });
}

/// Load the status data for a lazily-created scheduler content box
pub fn load_scheduler_status(content_box: &Box) {
    unsafe {
        if let Some(status_label) = content_box.data::<Label>("status_label")
            && let Some(status_icon) = content_box.data::<gtk::Image>("status_icon")
        {
            let status_label_ref = status_label.as_ref();
            let status_icon_ref = status_icon.as_ref();
            status_label_ref.set_text(&tr("Checking…"));
            update_service_status(status_label_ref, status_icon_ref);
        }
        if let Some(last_snapshot_label) = content_box.data::<Label>("last_snapshot_label") {
            let last_snapshot_label_ref = last_snapshot_label.as_ref();
            last_snapshot_label_ref.set_text(&tr("Checking…"));
            update_last_snapshot(last_snapshot_label_ref);
        }

        if let Some(cards_ptr) =
            content_box.data::<Rc<RefCell<Vec<Rc<RefCell<ScheduleCard>>>>>>("schedule_cards")
        {
            let cards = cards_ptr.as_ref().clone();
            update_schedule_cards_data(&cards);

            let refresh_started = content_box
                .data::<bool>("schedule_refresh_initialized")
                .map(|flag| *flag.as_ref())
                .unwrap_or(false);

            if !refresh_started {
                let cards_for_refresh = cards.clone();
                gtk::glib::timeout_add_local(std::time::Duration::from_secs(60), move || {
                    update_schedule_cards_data(&cards_for_refresh);
                    gtk::glib::ControlFlow::Continue
                });

                content_box.set_data("schedule_refresh_initialized", true);
            }
        }
    }
}

/// Load schedules configuration from file
fn load_schedules_config() -> SchedulesConfig {
    use waypoint_common::WaypointConfig;

    let config = WaypointConfig::new();

    if config.schedules_config.exists() {
        let mut loaded =
            SchedulesConfig::load_from_file(&config.schedules_config).unwrap_or_default();
        // Existing installations predate Personal Files history and may also
        // omit one of the optional System intervals. Surface missing defaults
        // without silently enabling or rewriting anything.
        for default in SchedulesConfig::default().schedules {
            if !loaded.schedules.iter().any(|schedule| {
                schedule.scope == default.scope && schedule.schedule_type == default.schedule_type
            }) {
                loaded.schedules.push(default);
            }
        }
        loaded
    } else {
        SchedulesConfig::default()
    }
}

/// Save all schedules configuration from cards
fn save_all_schedules_from_cards(
    parent: &adw::ApplicationWindow,
    schedule_cards: &Rc<RefCell<Vec<Rc<RefCell<ScheduleCard>>>>>,
) {
    let mut schedules = Vec::new();

    // Extract all schedules from the cards
    for card_rc in schedule_cards.borrow().iter() {
        let card = card_rc.borrow();
        schedules.push(card.schedule().clone());
    }

    let schedules_config = SchedulesConfig { schedules };

    // Serialize to TOML
    let config_content = match toml::to_string_pretty(&schedules_config) {
        Ok(content) => {
            // Add header comment
            format!(
                "# AnduinOS Waypoint Automatic Recovery Configuration\n# Multiple schedules can run concurrently with different retention policies.\n\n{content}"
            )
        }
        Err(e) => {
            dialogs::show_error(
                parent,
                &tr("Configuration Error"),
                &trf("Failed to serialize configuration: {0}", &[&e.to_string()]),
            );
            return;
        }
    };

    // Save configuration via D-Bus (run in thread to avoid blocking UI)
    let parent_clone = parent.clone();
    let (tx, rx) = std::sync::mpsc::channel();

    std::thread::spawn(move || {
        let result = (|| -> anyhow::Result<()> {
            let client = WaypointHelperClient::new()?;
            let (success, message) = client.save_schedules_config(config_content)?;
            if !success {
                return Err(anyhow::anyhow!(message));
            }

            // Applying systemd state is intentionally a separate, visible action.
            Ok(())
        })();

        let _ = tx.send(result);
    });

    // Wait for result in main thread
    gtk::glib::timeout_add_local(std::time::Duration::from_millis(100), move || {
        if let Ok(result) = rx.try_recv() {
            match result {
                Ok(_) => {
                    // Success - config saved (the banner will prompt to apply it).
                    log::info!("Scheduler configuration saved successfully");
                }
                Err(e) => {
                    dialogs::show_error(
                        &parent_clone,
                        &tr("Save Failed"),
                        &trf(
                            "Failed to save scheduler configuration: {0}",
                            &[&e.to_string()],
                        ),
                    );
                }
            }
            gtk::glib::ControlFlow::Break
        } else {
            gtk::glib::ControlFlow::Continue
        }
    });
}

/// Enable and start the scheduler when any schedule is active, or disable and
/// stop it when all schedules are inactive.
fn apply_scheduler_service(
    parent: &adw::ApplicationWindow,
    status_label: &Label,
    status_icon: &gtk::Image,
) {
    let parent_clone = parent.clone();
    let status_label_clone = status_label.clone();
    let status_icon_clone = status_icon.clone();
    let (tx, rx) = std::sync::mpsc::channel();

    std::thread::spawn(move || {
        let result = (|| -> anyhow::Result<()> {
            let client = WaypointHelperClient::new()?;
            let (success, message) = client.restart_scheduler()?;
            if !success {
                return Err(anyhow::anyhow!(message));
            }
            Ok(())
        })();

        let _ = tx.send(result);
    });

    // Wait for result in main thread
    gtk::glib::timeout_add_local(std::time::Duration::from_millis(100), move || {
        if let Ok(result) = rx.try_recv() {
            match result {
                Ok(_) => {
                    dialogs::show_toast(&parent_clone, &tr("Automatic recovery schedule applied"));
                    update_service_status(&status_label_clone, &status_icon_clone);
                }
                Err(e) => {
                    dialogs::show_error(
                        &parent_clone,
                        &tr("Apply Failed"),
                        &trf(
                            "Failed to apply automatic recovery schedule: {0}",
                            &[&e.to_string()],
                        ),
                    );
                }
            }
            gtk::glib::ControlFlow::Break
        } else {
            gtk::glib::ControlFlow::Continue
        }
    });
}

/// Update the service status label and icon
fn update_service_status(status_label: &Label, status_icon: &gtk::Image) {
    let status_label_clone = status_label.clone();
    let status_icon_clone = status_icon.clone();
    let (tx, rx) = std::sync::mpsc::channel();

    std::thread::spawn(move || {
        let status_text = match WaypointHelperClient::new() {
            Ok(client) => match client.get_scheduler_status() {
                Ok(message) => message,
                Err(e) => format!("error:{e}"),
            },
            Err(_) => "unavailable".to_string(),
        };

        let _ = tx.send(status_text);
    });

    // Update UI from main thread
    gtk::glib::idle_add_local_once(move || {
        if let Ok(status_text) = rx.recv() {
            let display_text = match status_text.as_str() {
                "running" => tr("Running"),
                "stopped" => tr("Stopped"),
                "disabled" => tr("Disabled"),
                "unknown" => tr("Unknown"),
                "unavailable" => tr("Recovery service unavailable"),
                value if value.starts_with("error:") => {
                    trf("Error: {0}", &[value.trim_start_matches("error:")])
                }
                _ => tr("Unknown"),
            };
            status_label_clone.set_text(&display_text);

            // Set icon based on status (case-insensitive matching)
            let status_lower = status_text.to_lowercase();
            if status_lower.contains("running") || status_lower.contains("active") {
                status_icon_clone.set_icon_name(Some("media-record-symbolic"));
                status_icon_clone.add_css_class("success");
                status_icon_clone.remove_css_class("error");
                status_icon_clone.remove_css_class("warning");
            } else if status_lower.contains("stopped")
                || status_lower.contains("inactive")
                || status_lower.contains("error")
                || status_lower.contains("unavailable")
            {
                status_icon_clone.set_icon_name(Some("media-record-symbolic"));
                status_icon_clone.add_css_class("error");
                status_icon_clone.remove_css_class("success");
                status_icon_clone.remove_css_class("warning");
            } else if status_lower.contains("disabled") {
                status_icon_clone.set_icon_name(Some("media-record-symbolic"));
                status_icon_clone.add_css_class("warning");
                status_icon_clone.remove_css_class("success");
                status_icon_clone.remove_css_class("error");
            } else {
                // For "Checking..." or other states, use a neutral icon
                status_icon_clone.set_icon_name(Some("emblem-system-symbolic"));
                status_icon_clone.remove_css_class("success");
                status_icon_clone.remove_css_class("error");
                status_icon_clone.remove_css_class("warning");
            }
        }
    });
}

/// Update the last snapshot label
fn update_last_snapshot(last_snapshot_label: &Label) {
    let label_clone = last_snapshot_label.clone();
    let (tx, rx) = std::sync::mpsc::channel();

    std::thread::spawn(move || {
        let text = match WaypointHelperClient::new() {
            Ok(client) => match client.recovery_engine_status() {
                Ok(status) => {
                    let automatic: Vec<_> = status
                        .deployments
                        .iter()
                        .filter(|deployment| deployment.kind == "automatic")
                        .collect();

                    if let Some(latest) = automatic
                        .iter()
                        .max_by_key(|deployment| deployment.created_at)
                    {
                        let now = chrono::Utc::now();
                        let duration = now.signed_duration_since(latest.created_at);

                        if duration.num_days() > 0 {
                            trf("{0} days ago", &[&duration.num_days().to_string()])
                        } else if duration.num_hours() > 0 {
                            trf("{0} hours ago", &[&duration.num_hours().to_string()])
                        } else if duration.num_minutes() > 0 {
                            trf("{0} minutes ago", &[&duration.num_minutes().to_string()])
                        } else {
                            tr("Just now")
                        }
                    } else {
                        tr("No automatic recovery points yet")
                    }
                }
                Err(e) => trf("Error: {0}", &[&e.to_string()]),
            },
            Err(_) => tr("Recovery service unavailable"),
        };

        let _ = tx.send(text);
    });

    // Update UI from main thread
    gtk::glib::idle_add_local_once(move || {
        if let Ok(text) = rx.recv() {
            label_clone.set_text(&text);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn deployment(
        kind: &str,
        schedule_id: Option<&str>,
        title: &str,
        created_at: chrono::DateTime<chrono::Utc>,
    ) -> crate::dbus_client::RecoveryDeployment {
        crate::dbus_client::RecoveryDeployment {
            id: "00000000-0000-4000-8000-000000000001".to_string(),
            kind: kind.to_string(),
            state: "ready".to_string(),
            created_at,
            title: title.to_string(),
            reason: "test".to_string(),
            schedule_id: schedule_id.map(str::to_string),
            kernel_release: None,
            pinned: false,
        }
    }

    #[test]
    fn schedule_history_uses_typed_schedule_identity() {
        let now = chrono::Utc::now();
        let schedule = Schedule::default_daily();
        let prefix = schedule.prefix.clone();
        let deployments = vec![
            deployment(
                "automatic",
                Some(&prefix),
                &format!("{prefix}-older"),
                now - chrono::Duration::days(2),
            ),
            deployment(
                "manual",
                None,
                &format!("{prefix}-manual"),
                now - chrono::Duration::hours(1),
            ),
            deployment(
                "automatic",
                Some(&prefix),
                &format!("{prefix}-newer"),
                now - chrono::Duration::days(1),
            ),
            deployment(
                "automatic",
                Some("weekly"),
                &format!("{prefix}-collision"),
                now,
            ),
        ];

        assert_eq!(
            find_last_snapshot(&deployments, &schedule),
            Some(now - chrono::Duration::days(1))
        );
        assert_eq!(build_sparkline_data(&deployments, &schedule, 10).len(), 2);
    }
}
