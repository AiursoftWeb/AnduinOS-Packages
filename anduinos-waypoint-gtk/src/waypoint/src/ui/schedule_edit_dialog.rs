use adw::prelude::*;
use gtk::prelude::*;
use gtk::{Box, Label, Orientation, SpinButton};
use libadwaita as adw;
use waypoint_common::{Schedule, ScheduleScope, ScheduleType};

use crate::i18n::{tr, trf};

/// Create a modal dialog for editing a schedule
pub fn create_schedule_edit_dialog(
    parent: &adw::ApplicationWindow,
    schedule: Schedule,
) -> adw::PreferencesWindow {
    let dialog = adw::PreferencesWindow::new();
    let title = trf(
        "Edit {0} Schedule",
        &[&get_schedule_name(&schedule.schedule_type)],
    );
    dialog.set_title(Some(&title));
    dialog.set_modal(true);
    dialog.set_transient_for(Some(parent));

    // Create preferences page
    let page = adw::PreferencesPage::new();

    // Schedule configuration group
    let config_group = adw::PreferencesGroup::new();
    config_group.set_title(&tr("Schedule"));
    page.add(&config_group);

    // Time setting (for all except hourly)
    let time_row_opt = if schedule.schedule_type != ScheduleType::Hourly {
        let time_row = create_time_row(&schedule);
        config_group.add(&time_row);
        Some(time_row)
    } else {
        None
    };

    // Day of week selector (for weekly)
    let day_of_week_row_opt = if schedule.schedule_type == ScheduleType::Weekly {
        let day_row = create_day_of_week_row(&schedule);
        config_group.add(&day_row);
        Some(day_row)
    } else {
        None
    };

    // Day of month selector (for monthly)
    let day_of_month_row_opt = if schedule.schedule_type == ScheduleType::Monthly {
        let day_row = create_day_of_month_row(&schedule);
        config_group.add(&day_row);
        Some(day_row)
    } else {
        None
    };

    // Naming group
    let naming_group = adw::PreferencesGroup::new();
    naming_group.set_title(&tr("Naming"));
    page.add(&naming_group);

    let prefix_row = create_prefix_row(&schedule);
    naming_group.add(&prefix_row);

    // Add preview label
    let preview_label = Label::new(None);
    preview_label.set_halign(gtk::Align::Start);
    preview_label.add_css_class("dim-label");
    preview_label.add_css_class("caption");
    preview_label.set_margin_top(6);
    preview_label.set_margin_bottom(12);
    preview_label.set_margin_start(12);
    preview_label.set_margin_end(12);
    update_preview_label(&preview_label, &schedule.prefix);

    // Get the prefix entry and connect to update preview
    if let Some(entry_row) = prefix_row.downcast_ref::<adw::EntryRow>() {
        let preview_clone = preview_label.clone();
        entry_row.connect_changed(move |row| {
            let text = row.text();
            update_preview_label(&preview_clone, text.as_str());
        });
    }

    naming_group.add(&preview_label);

    let scope_group = adw::PreferencesGroup::new();
    scope_group.set_title(&tr("Recovery Scope"));
    scope_group.set_description(Some(&tr(
        "System recovery and Personal Files history are independent. Personal history can recover files without rolling back the operating system.",
    )));
    let scope_row = adw::ComboRow::new();
    scope_row.set_title(&tr("History"));
    scope_row.set_subtitle(&tr(
        "Choose the independent data protected by this schedule",
    ));
    scope_row.set_model(Some(&gtk::StringList::new(&[
        &tr("System · @root"),
        &tr("Personal Files · @home"),
    ])));
    scope_row.set_selected(match schedule.scope {
        ScheduleScope::System => 0,
        ScheduleScope::Personal => 1,
    });
    scope_group.add(&scope_row);
    page.add(&scope_group);

    let notifications_group = adw::PreferencesGroup::new();
    notifications_group.set_title(&tr("Notifications"));
    let notify_row = adw::ActionRow::new();
    notify_row.set_title(&tr("Notify when created"));
    notify_row.set_subtitle(&tr(
        "Show a desktop notification after this automatic recovery point succeeds",
    ));
    let notify_check = gtk::CheckButton::new();
    notify_check.set_active(schedule.notify_on_create);
    notify_check.set_valign(gtk::Align::Center);
    notify_row.add_suffix(&notify_check);
    notify_row.set_activatable_widget(Some(&notify_check));
    notifications_group.add(&notify_row);
    page.add(&notifications_group);

    // Retention group with timeline-based retention
    let retention_group = adw::PreferencesGroup::new();
    retention_group.set_title(&tr("Retention Policy"));
    retention_group.set_description(Some(&tr(
        "Timeline-based retention keeps the most recent recovery point in each time period",
    )));
    page.add(&retention_group);

    // Add timeline retention expander
    let timeline_expander = create_timeline_retention_expander(&schedule);
    retention_group.add(&timeline_expander);

    dialog.add(&page);

    // Store widget references for later data extraction
    unsafe {
        dialog.set_data("schedule_type", schedule.schedule_type as u32);
        dialog.set_data("scope_row", scope_row);
        dialog.set_data("notify_check", notify_check);
        dialog.set_data("schedule_description", schedule.description.clone());

        if let Some(time_row) = time_row_opt {
            dialog.set_data("time_row", time_row);
        }
        if let Some(day_row) = day_of_week_row_opt {
            dialog.set_data("day_of_week_row", day_row);
        }
        if let Some(day_row) = day_of_month_row_opt {
            dialog.set_data("day_of_month_row", day_row);
        }
        dialog.set_data("prefix_row", prefix_row.clone());
        dialog.set_data("timeline_expander", timeline_expander.clone());
    }

    dialog
}

/// Get the display name for a schedule type
fn get_schedule_name(schedule_type: &ScheduleType) -> String {
    match schedule_type {
        ScheduleType::Hourly => tr("Hourly"),
        ScheduleType::Daily => tr("Daily"),
        ScheduleType::Weekly => tr("Weekly"),
        ScheduleType::Monthly => tr("Monthly"),
    }
}

/// Create time selection row
fn create_time_row(schedule: &Schedule) -> adw::ActionRow {
    let row = adw::ActionRow::new();
    row.set_title(&tr("Time"));
    row.set_subtitle(&tr(
        "Time of day to create a recovery point (24-hour format)",
    ));

    let default_time = "02:00".to_string();
    let time = schedule.time.as_ref().unwrap_or(&default_time);
    let time_parts: Vec<&str> = time.split(':').collect();
    let hour = time_parts
        .first()
        .and_then(|h| h.parse::<f64>().ok())
        .unwrap_or(2.0);
    let minute = time_parts
        .get(1)
        .and_then(|m| m.parse::<f64>().ok())
        .unwrap_or(0.0);

    let time_box = Box::new(Orientation::Horizontal, 6);

    let hour_spin = SpinButton::with_range(0.0, 23.0, 1.0);
    hour_spin.set_value(hour);
    hour_spin.set_width_chars(3);
    hour_spin.set_valign(gtk::Align::Center);
    time_box.append(&hour_spin);

    let colon_label = Label::new(Some(":"));
    colon_label.set_valign(gtk::Align::Center);
    time_box.append(&colon_label);

    let minute_spin = SpinButton::with_range(0.0, 59.0, 1.0);
    minute_spin.set_value(minute);
    minute_spin.set_width_chars(3);
    minute_spin.set_valign(gtk::Align::Center);
    time_box.append(&minute_spin);

    row.add_suffix(&time_box);

    // Store for later retrieval
    unsafe {
        row.set_data("hour_spin", hour_spin);
        row.set_data("minute_spin", minute_spin);
    }

    row
}

/// Create day of week selection row
fn create_day_of_week_row(schedule: &Schedule) -> adw::ComboRow {
    let row = adw::ComboRow::new();
    row.set_title(&tr("Day of Week"));
    row.set_subtitle(&tr("Which day to create weekly recovery points"));

    let translated_days = [
        tr("Sunday"),
        tr("Monday"),
        tr("Tuesday"),
        tr("Wednesday"),
        tr("Thursday"),
        tr("Friday"),
        tr("Saturday"),
    ];
    let day_items = gtk::StringList::new(
        &translated_days
            .iter()
            .map(String::as_str)
            .collect::<Vec<_>>(),
    );
    row.set_model(Some(&day_items));
    row.set_selected(schedule.day_of_week.unwrap_or(0) as u32);

    row
}

/// Create day of month selection row
fn create_day_of_month_row(schedule: &Schedule) -> adw::ActionRow {
    let row = adw::ActionRow::new();
    row.set_title(&tr("Day of Month"));
    row.set_subtitle(&tr("Which day of the month to create recovery points"));

    let day_spin = SpinButton::with_range(1.0, 31.0, 1.0);
    day_spin.set_value(schedule.day_of_month.unwrap_or(1) as f64);
    day_spin.set_width_chars(3);
    day_spin.set_valign(gtk::Align::Center);
    row.add_suffix(&day_spin);

    // Store for later retrieval
    unsafe {
        row.set_data("day_spin", day_spin);
    }

    row
}

/// Create prefix entry row
fn create_prefix_row(schedule: &Schedule) -> adw::EntryRow {
    let row = adw::EntryRow::new();
    row.set_title(&tr("Prefix"));
    row.set_text(&schedule.prefix);
    row
}

/// Create timeline retention expander with all time buckets
fn create_timeline_retention_expander(schedule: &Schedule) -> adw::ExpanderRow {
    use waypoint_common::TimelineRetention;

    let expander = adw::ExpanderRow::new();
    expander.set_title(&tr("Timeline Retention"));
    expander.set_subtitle(&tr(
        "Keep the most recent recovery point in each time period",
    ));

    // Get current timeline retention or create default
    let timeline = schedule
        .timeline_retention
        .as_ref()
        .cloned()
        .unwrap_or_else(|| match schedule.schedule_type {
            waypoint_common::ScheduleType::Hourly => TimelineRetention::for_hourly(),
            waypoint_common::ScheduleType::Daily => TimelineRetention::for_daily(),
            waypoint_common::ScheduleType::Weekly => TimelineRetention::for_weekly(),
            waypoint_common::ScheduleType::Monthly => TimelineRetention::for_monthly(),
        });

    // Hourly retention row
    let hourly_row = create_timeline_bucket_row(
        &tr("Hourly"),
        &tr("Keep the last N hours (0 = disabled)"),
        timeline.hourly_limit,
    );
    expander.add_row(&hourly_row);

    // Daily retention row
    let daily_row = create_timeline_bucket_row(
        &tr("Daily"),
        &tr("Keep the last N days (0 = disabled)"),
        timeline.daily_limit,
    );
    expander.add_row(&daily_row);

    // Weekly retention row
    let weekly_row = create_timeline_bucket_row(
        &tr("Weekly"),
        &tr("Keep the last N weeks (0 = disabled)"),
        timeline.weekly_limit,
    );
    expander.add_row(&weekly_row);

    // Monthly retention row
    let monthly_row = create_timeline_bucket_row(
        &tr("Monthly"),
        &tr("Keep the last N months (0 = disabled)"),
        timeline.monthly_limit,
    );
    expander.add_row(&monthly_row);

    // Yearly retention row
    let yearly_row = create_timeline_bucket_row(
        &tr("Yearly"),
        &tr("Keep the last N years (0 = disabled)"),
        timeline.yearly_limit,
    );
    expander.add_row(&yearly_row);

    // Store rows for later retrieval
    unsafe {
        expander.set_data("hourly_row", hourly_row);
        expander.set_data("daily_row", daily_row);
        expander.set_data("weekly_row", weekly_row);
        expander.set_data("monthly_row", monthly_row);
        expander.set_data("yearly_row", yearly_row);
    }

    expander
}

/// Create a single timeline bucket row
fn create_timeline_bucket_row(title: &str, subtitle: &str, initial_value: u32) -> adw::ActionRow {
    let row = adw::ActionRow::new();
    row.set_title(title);
    row.set_subtitle(subtitle);

    let spin = SpinButton::with_range(0.0, 365.0, 1.0);
    spin.set_value(initial_value as f64);
    spin.set_width_chars(5);
    spin.set_valign(gtk::Align::Center);
    row.add_suffix(&spin);

    // Store for later retrieval
    unsafe {
        row.set_data("limit_spin", spin);
    }

    row
}

/// Update the preview label with current prefix
fn update_preview_label(label: &Label, prefix: &str) {
    let now = chrono::Local::now();
    let timestamp = now.format("%Y-%m-%d_%H%M").to_string();
    let preview = trf("Preview: {0}-{1}", &[prefix, &timestamp]);
    label.set_text(&preview);
}

/// Extract schedule data from the edit dialog
pub fn extract_schedule_from_dialog(dialog: &adw::PreferencesWindow) -> Option<Schedule> {
    unsafe {
        let schedule_type_ptr = dialog.data::<u32>("schedule_type")?;
        let schedule_type = match schedule_type_ptr.as_ref() {
            0 => ScheduleType::Hourly,
            1 => ScheduleType::Daily,
            2 => ScheduleType::Weekly,
            3 => ScheduleType::Monthly,
            _ => return None,
        };

        let mut schedule = Schedule {
            enabled: true, // Will be set by the card's switch
            scope: dialog
                .data::<adw::ComboRow>("scope_row")
                .map(|row| match row.as_ref().selected() {
                    1 => ScheduleScope::Personal,
                    _ => ScheduleScope::System,
                })
                .unwrap_or_default(),
            notify_on_create: dialog
                .data::<gtk::CheckButton>("notify_check")
                .is_none_or(|check| check.as_ref().is_active()),
            schedule_type,
            time: None,
            day_of_week: None,
            day_of_month: None,
            prefix: String::new(),
            description: dialog
                .data::<String>("schedule_description")
                .map(|value| value.as_ref().clone())
                .unwrap_or_else(|| "Automatic recovery history point".to_string()),
            keep_count: 0,
            keep_days: 0,
            timeline_retention: None, // Will be populated if using timeline retention
        };

        // Extract prefix
        if let Some(prefix_row) = dialog.data::<adw::EntryRow>("prefix_row") {
            schedule.prefix = prefix_row.as_ref().text().to_string();
        }

        // Extract timeline retention
        if let Some(timeline_expander) = dialog.data::<adw::ExpanderRow>("timeline_expander") {
            use waypoint_common::TimelineRetention;

            let mut timeline = TimelineRetention::default();

            // Extract hourly limit
            if let Some(hourly_row) = timeline_expander
                .as_ref()
                .data::<adw::ActionRow>("hourly_row")
                && let Some(spin) = hourly_row.as_ref().data::<SpinButton>("limit_spin")
            {
                timeline.hourly_limit = spin.as_ref().value() as u32;
            }

            // Extract daily limit
            if let Some(daily_row) = timeline_expander
                .as_ref()
                .data::<adw::ActionRow>("daily_row")
                && let Some(spin) = daily_row.as_ref().data::<SpinButton>("limit_spin")
            {
                timeline.daily_limit = spin.as_ref().value() as u32;
            }

            // Extract weekly limit
            if let Some(weekly_row) = timeline_expander
                .as_ref()
                .data::<adw::ActionRow>("weekly_row")
                && let Some(spin) = weekly_row.as_ref().data::<SpinButton>("limit_spin")
            {
                timeline.weekly_limit = spin.as_ref().value() as u32;
            }

            // Extract monthly limit
            if let Some(monthly_row) = timeline_expander
                .as_ref()
                .data::<adw::ActionRow>("monthly_row")
                && let Some(spin) = monthly_row.as_ref().data::<SpinButton>("limit_spin")
            {
                timeline.monthly_limit = spin.as_ref().value() as u32;
            }

            // Extract yearly limit
            if let Some(yearly_row) = timeline_expander
                .as_ref()
                .data::<adw::ActionRow>("yearly_row")
                && let Some(spin) = yearly_row.as_ref().data::<SpinButton>("limit_spin")
            {
                timeline.yearly_limit = spin.as_ref().value() as u32;
            }

            schedule.timeline_retention = Some(timeline);
        }

        // Extract time (for non-hourly)
        if let Some(time_row) = dialog.data::<adw::ActionRow>("time_row")
            && let Some(hour_spin) = time_row.as_ref().data::<SpinButton>("hour_spin")
            && let Some(minute_spin) = time_row.as_ref().data::<SpinButton>("minute_spin")
        {
            let hour = hour_spin.as_ref().value() as u32;
            let minute = minute_spin.as_ref().value() as u32;
            schedule.time = Some(format!("{hour:02}:{minute:02}"));
        }

        // Extract day of week (for weekly)
        if let Some(day_row) = dialog.data::<adw::ComboRow>("day_of_week_row") {
            schedule.day_of_week = Some(day_row.as_ref().selected() as u8);
        }

        // Extract day of month (for monthly)
        if let Some(day_row) = dialog.data::<adw::ActionRow>("day_of_month_row")
            && let Some(day_spin) = day_row.as_ref().data::<SpinButton>("day_spin")
        {
            schedule.day_of_month = Some(day_spin.as_ref().value() as u8);
        }

        Some(schedule)
    }
}
