//! Analytics dialog showing snapshot statistics and insights

use adw::prelude::*;
use chrono::Utc;
use gtk::prelude::*;
use gtk::{Label, Orientation};
use libadwaita as adw;

use crate::btrfs;
use crate::i18n::{tr, trf};
use crate::snapshot::{Snapshot, format_bytes};

/// Create empty state when no snapshots exist
fn create_empty_state() -> adw::StatusPage {
    let status_page = adw::StatusPage::new();
    status_page.set_title(&tr("No Recovery Points Yet"));
    status_page.set_description(Some(&tr(
        "Create your first recovery point to see system recovery statistics and insights.",
    )));
    status_page.set_icon_name(Some("folder-symbolic"));
    status_page.set_vexpand(true);
    status_page
}

/// Calculate all snapshot sizes once and store in a map
/// Uses parallel processing for significant speedup
fn calculate_all_sizes(snapshots: &[Snapshot]) -> std::collections::HashMap<String, u64> {
    use std::collections::HashMap;

    // First, check which snapshots already have cached sizes
    let mut sizes = HashMap::new();
    let mut deployments_to_calculate = Vec::new();

    for snapshot in snapshots {
        if let Some(cached_size) = snapshot.size_bytes {
            // Use cached size from metadata
            sizes.insert(snapshot.id.clone(), cached_size);
        } else {
            // Need to calculate this one
            deployments_to_calculate.push(snapshot.id.clone());
        }
    }

    // Calculate remaining sizes in parallel using the optimized bulk function
    if !deployments_to_calculate.is_empty() {
        let calculated_sizes = btrfs::get_all_snapshot_sizes(&deployments_to_calculate);

        // Map paths back to snapshot names
        for snapshot in snapshots {
            if !sizes.contains_key(&snapshot.id)
                && let Some(&size) = calculated_sizes.get(&snapshot.id)
            {
                sizes.insert(snapshot.id.clone(), size);
            }
        }
    }

    sizes
}

/// Show analytics dialog with snapshot statistics
pub fn show_analytics_dialog(
    parent: &adw::ApplicationWindow,
    snapshots: &[Snapshot],
    _snapshot_manager: &std::rc::Rc<std::cell::RefCell<crate::snapshot::SnapshotManager>>,
) {
    let dialog = adw::Window::new();
    dialog.set_title(Some(&tr("Analytics")));
    dialog.set_default_size(700, 650);
    dialog.set_modal(true);
    dialog.set_transient_for(Some(parent));

    let content = gtk::Box::new(Orientation::Vertical, 0);

    // Header
    let header = adw::HeaderBar::new();
    header.set_title_widget(Some(&adw::WindowTitle::new(&tr("Analytics"), "")));
    content.append(&header);

    // Check for empty state
    if snapshots.is_empty() {
        content.append(&create_empty_state());
        dialog.set_content(Some(&content));
        dialog.present();
        return;
    }

    // Scrolled window for content
    let scrolled = gtk::ScrolledWindow::new();
    scrolled.set_vexpand(true);
    scrolled.set_hexpand(true);

    let clamp = adw::Clamp::new();
    clamp.set_maximum_size(800);
    clamp.set_tightening_threshold(600);

    // Calculate all snapshot sizes once (this is the optimization)
    let snapshot_sizes = calculate_all_sizes(snapshots);

    // Calculate statistics using the pre-calculated sizes
    let stats = calculate_statistics_with_sizes(snapshots, &snapshot_sizes);

    // Build UI with all sections
    let main_box = gtk::Box::new(Orientation::Vertical, 0);
    main_box.set_margin_start(12);
    main_box.set_margin_end(12);
    main_box.set_margin_top(24);
    main_box.set_margin_bottom(24);

    // Overview section
    main_box.append(&create_overview_section(&stats));

    // Space usage section
    main_box.append(&create_space_section(&stats));

    // Insights and recommendations
    main_box.append(&create_insights_section(&stats, snapshots, &snapshot_sizes));

    // Largest snapshots section
    main_box.append(&create_largest_snapshots_section(
        snapshots,
        &snapshot_sizes,
        stats.total_size,
    ));

    clamp.set_child(Some(&main_box));
    scrolled.set_child(Some(&clamp));
    content.append(&scrolled);

    dialog.set_content(Some(&content));
    dialog.present();
}

/// Statistics calculated from snapshots
struct SnapshotStats {
    total_count: usize,
    total_size: u64,
    oldest_age_days: Option<i64>,
    newest_age_hours: Option<i64>,
    average_size: u64,
}

/// Calculate statistics using pre-calculated sizes (optimized - no redundant btrfs calls)
fn calculate_statistics_with_sizes(
    snapshots: &[Snapshot],
    sizes: &std::collections::HashMap<String, u64>,
) -> SnapshotStats {
    let total_count = snapshots.len();

    // Calculate total size from pre-calculated map
    let total_size: u64 = sizes.values().sum();
    let counted = sizes.len();

    let average_size = if counted > 0 {
        total_size / counted as u64
    } else {
        0
    };

    // Find oldest and newest snapshots
    let now = Utc::now();
    let oldest_age_days = snapshots
        .iter()
        .map(|s| (now - s.timestamp).num_days())
        .max();

    let newest_age_hours = snapshots
        .iter()
        .map(|s| (now - s.timestamp).num_hours())
        .min();

    SnapshotStats {
        total_count,
        total_size,
        oldest_age_days,
        newest_age_hours,
        average_size,
    }
}

/// Create overview section with basic stats
fn create_overview_section(stats: &SnapshotStats) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::new();
    group.set_title(&tr("Overview"));
    group.set_margin_bottom(18);

    // Total recovery points
    let total_row = adw::ActionRow::new();
    total_row.set_title(&tr("Total Recovery Points"));
    total_row.add_suffix(&create_stat_label(&stats.total_count.to_string()));
    group.add(&total_row);

    // Oldest recovery point
    if let Some(days) = stats.oldest_age_days {
        let oldest_row = adw::ActionRow::new();
        oldest_row.set_title(&tr("Oldest Recovery Point"));
        let age_text = if days == 0 {
            tr("Today")
        } else if days == 1 {
            tr("1 day ago")
        } else if days < 30 {
            trf("{0} days ago", &[&days.to_string()])
        } else if days < 365 {
            trf("{0} months ago", &[&(days / 30).to_string()])
        } else {
            trf("{0} years ago", &[&(days / 365).to_string()])
        };
        oldest_row.add_suffix(&create_stat_label(&age_text));
        group.add(&oldest_row);
    }

    // Newest recovery point
    if let Some(hours) = stats.newest_age_hours {
        let newest_row = adw::ActionRow::new();
        newest_row.set_title(&tr("Newest Recovery Point"));
        let age_text = if hours == 0 {
            tr("Just now")
        } else if hours < 24 {
            if hours == 1 {
                tr("1 hour ago")
            } else {
                trf("{0} hours ago", &[&hours.to_string()])
            }
        } else {
            let days = hours / 24;
            if days == 1 {
                tr("1 day ago")
            } else {
                trf("{0} days ago", &[&days.to_string()])
            }
        };
        newest_row.add_suffix(&create_stat_label(&age_text));
        group.add(&newest_row);
    }

    // Average frequency
    if let Some(oldest_days) = stats.oldest_age_days
        && oldest_days > 0
        && stats.total_count > 1
    {
        let freq_row = adw::ActionRow::new();
        freq_row.set_title(&tr("Recovery Point Frequency"));
        let per_day = stats.total_count as f64 / oldest_days as f64;
        let freq_text = if per_day >= 1.0 {
            trf("{0} per day", &[&format!("{per_day:.1}")])
        } else {
            trf("1 per {0} days", &[&format!("{:.0}", 1.0 / per_day)])
        };
        freq_row.add_suffix(&create_stat_label(&freq_text));
        group.add(&freq_row);
    }

    group
}

/// Create space usage section
fn create_space_section(stats: &SnapshotStats) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::new();
    group.set_title(&tr("Estimated Reclaimable Space"));
    group.set_description(Some(&tr(
        "Btrfs exclusive bytes are the best available deletion estimate, not a guarantee.",
    )));
    group.set_margin_bottom(18);

    // Total space used
    let total_row = adw::ActionRow::new();
    total_row.set_title(&tr("Exclusive Across Recovery Points"));
    total_row.add_suffix(&create_stat_label(&format_bytes(stats.total_size)));
    group.add(&total_row);

    // Average snapshot size
    let avg_row = adw::ActionRow::new();
    avg_row.set_title(&tr("Average Exclusive Space"));
    avg_row.add_suffix(&create_stat_label(&format_bytes(stats.average_size)));
    group.add(&avg_row);

    group
}

/// Create largest snapshots section with visual size indicators (optimized)
fn create_largest_snapshots_section(
    snapshots: &[Snapshot],
    sizes: &std::collections::HashMap<String, u64>,
    total_size: u64,
) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::new();
    group.set_title(&tr("Most Reclaimable Recovery Points"));
    group.set_description(Some(&tr("Top 5 estimates from level-zero Btrfs qgroups")));

    // Build list with sizes from pre-calculated map
    let mut snapshots_with_sizes: Vec<(&Snapshot, u64)> = snapshots
        .iter()
        .filter_map(|s| {
            let size = sizes.get(&s.id).copied()?;
            Some((s, size))
        })
        .collect();

    // Sort by size and take top 5
    snapshots_with_sizes.sort_by_key(|snapshot| std::cmp::Reverse(snapshot.1));
    let top_5: Vec<_> = snapshots_with_sizes.iter().take(5).collect();

    if top_5.is_empty() {
        return group;
    }

    for (idx, (snapshot, size)) in top_5.iter().enumerate() {
        // Create ActionRow with custom content
        let row = adw::ActionRow::new();

        // Build title with rank
        let title_text = format!("#{} {}", idx + 1, snapshot.name);
        row.set_title(&title_text);

        // Build subtitle
        let timestamp = snapshot.format_timestamp();
        let package_count = snapshot.package_count.unwrap_or(0).to_string();
        let subtitle = trf("{0} • {1} packages", &[&timestamp, &package_count]);
        row.set_subtitle(&subtitle);

        // Size and percentage in a box
        let size_box = gtk::Box::new(Orientation::Vertical, 2);

        let size_label = Label::new(Some(&format_bytes(*size)));
        size_label.set_halign(gtk::Align::End);
        size_box.append(&size_label);

        // Add percentage of total
        let percentage = if total_size > 0 {
            (*size as f64 / total_size as f64 * 100.0) as u32
        } else {
            0
        };
        let pct_label = Label::new(Some(&format!("{percentage}%")));
        pct_label.add_css_class("caption");
        pct_label.add_css_class("dim-label");
        pct_label.set_halign(gtk::Align::End);
        size_box.append(&pct_label);

        row.add_suffix(&size_box);

        // Add progress bar as a separate widget below the row
        let container = gtk::Box::new(Orientation::Vertical, 0);

        // The row itself
        let row_container = gtk::Box::new(Orientation::Vertical, 6);
        row_container.append(&row);

        // Progress bar - shows size relative to total storage (matches percentage label)
        let progress_bar = gtk::ProgressBar::new();
        let fraction = if total_size > 0 {
            (*size as f64) / (total_size as f64)
        } else {
            0.0
        };
        progress_bar.set_fraction(fraction);
        progress_bar.set_show_text(false);
        progress_bar.set_margin_start(12);
        progress_bar.set_margin_end(12);
        progress_bar.set_margin_bottom(6);
        row_container.append(&progress_bar);

        container.append(&row_container);

        group.add(&container);
    }

    group
}

/// Create insights and recommendations section
fn create_insights_section(
    stats: &SnapshotStats,
    _snapshots: &[Snapshot],
    sizes: &std::collections::HashMap<String, u64>,
) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::new();
    group.set_title(&tr("Insights and Recommendations"));
    group.set_margin_bottom(18);

    let mut insights: Vec<(String, String, &'static str)> = Vec::new();

    // Snapshot count management
    if stats.total_count > 50 {
        insights.push((
            tr("Many recovery points"),
            trf("You have {0} recovery points. Consider adjusting the retention policy to clean up older automatic recovery points.", &[&stats.total_count.to_string()]),
            "warning"
        ));
    } else if stats.total_count > 20 && stats.total_count <= 50 {
        insights.push((
            tr("Recovery point retention looks healthy"),
            trf(
                "{0} recovery points are stored. The retention policy appears to be working well.",
                &[&stats.total_count.to_string()],
            ),
            "info",
        ));
    } else if stats.total_count <= 5 {
        insights.push((
            tr("Few recovery points"),
            trf(
                "Only {0} recovery points are stored. Consider enabling an automatic recovery schedule.",
                &[&stats.total_count.to_string()],
            ),
            "info",
        ));
    }

    // Exclusive-space distribution
    let largest_size = sizes.values().copied().max().unwrap_or(0);

    if largest_size > 0 && stats.average_size > 0 {
        let ratio = largest_size as f64 / stats.average_size as f64;
        if ratio > 3.0 {
            insights.push((
                tr("Uneven reclaimable space"),
                trf("Some recovery points hold about {0} times more exclusive data than average. Check the estimates below before choosing one to delete.", &[&(ratio as u32).to_string()]),
                "info"
            ));
        }
    }

    // Snapshot frequency
    if let Some(oldest_days) = stats.oldest_age_days
        && oldest_days > 7
        && stats.total_count > 1
    {
        let per_day = stats.total_count as f64 / oldest_days as f64;
        if per_day < 0.2 {
            insights.push((
                    tr("Infrequent recovery points"),
                    tr("Recovery points are being created less than once per week. Enable an automatic schedule for better system protection."),
                    "info"
                ));
        } else if per_day > 3.0 {
            insights.push((
                    tr("Frequent recovery points"),
                    trf("Recovery points are being created {0} times per day. Make sure this frequency matches your recovery policy.", &[&format!("{per_day:.1}")]),
                    "info"
                ));
        }
    }

    // Overall health status (only if no other insights)
    if insights.is_empty() {
        insights.push((
            tr("Everything looks good"),
            tr("Recovery point management is healthy. No issues were detected."),
            "success",
        ));
    }

    // Add all insights to the group
    for (title, description, _level) in insights {
        let row = adw::ActionRow::new();
        row.set_title(&title);
        row.set_subtitle(&description);
        row.set_title_lines(2);
        row.set_subtitle_lines(3);
        group.add(&row);
    }

    group
}

/// Create a styled stat label
fn create_stat_label(text: &str) -> Label {
    let label = Label::new(Some(text));
    label.set_selectable(true);
    label
}
