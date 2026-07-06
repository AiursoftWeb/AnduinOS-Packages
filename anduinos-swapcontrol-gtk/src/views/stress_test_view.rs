use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;
use std::cell::RefCell;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use crate::i18n::i18n;
use crate::swap::{stress, sysctl};

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct StressTestView {
        // Configuration
        pub pressure_scale: RefCell<Option<gtk::Scale>>,
        pub pressure_label: RefCell<Option<gtk::Label>>,
        pub hold_spin: RefCell<Option<gtk::SpinButton>>,
        pub growth_scale: RefCell<Option<gtk::Scale>>,
        pub growth_row: RefCell<Option<adw::ActionRow>>,

        // Buttons
        pub run_button: RefCell<Option<gtk::Button>>,
        pub cancel_button: RefCell<Option<gtk::Button>>,

        // Running UI
        pub progress_bar: RefCell<Option<gtk::ProgressBar>>,
        pub progress_label: RefCell<Option<gtk::Label>>,
        pub mem_stats_label: RefCell<Option<gtk::Label>>,

        // Results
        pub results_label: RefCell<Option<gtk::Label>>,
        pub run_again_button: RefCell<Option<gtk::Button>>,

        // Stack
        pub status_stack: RefCell<Option<gtk::Stack>>,

        // Config rows (to make insensitive during test)
        pub config_rows: RefCell<Vec<gtk::Widget>>,

        // Runtime state
        pub test_running: RefCell<bool>,
        pub cancel_flag: RefCell<Option<Arc<AtomicBool>>>,
        pub stats_timer: RefCell<Option<glib::SourceId>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for StressTestView {
        const NAME: &'static str = "StressTestView";
        type Type = super::StressTestView;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for StressTestView {
        fn constructed(&self) {
            self.parent_constructed();
            self.obj().setup_ui();
        }
    }
    impl WidgetImpl for StressTestView {}
    impl BoxImpl for StressTestView {}
}

glib::wrapper! {
    pub struct StressTestView(ObjectSubclass<imp::StressTestView>)
        @extends gtk::Widget, gtk::Box,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

impl StressTestView {
    pub fn new() -> Self {
        glib::Object::builder().build()
    }

    // ─── UI Construction ────────────────────────────────────────────

    fn setup_ui(&self) {
        let imp = self.imp();
        self.set_orientation(gtk::Orientation::Vertical);
        self.set_spacing(0);
        self.set_vexpand(true);

        let scroll = gtk::ScrolledWindow::builder().vexpand(true).build();
        let inner = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(18)
            .margin_start(24)
            .margin_end(24)
            .margin_top(24)
            .margin_bottom(24)
            .build();
        scroll.set_child(Some(&inner));

        // Title
        inner.append(
            &gtk::Label::builder()
                .label(&i18n("Stress Test"))
                .css_classes(["title-1"])
                .halign(gtk::Align::Start)
                .build(),
        );
        inner.append(
            &gtk::Label::builder()
                .label(&i18n(
                    "Simulate memory pressure to test swap and OOM behavior",
                ))
                .css_classes(["caption"])
                .halign(gtk::Align::Start)
                .margin_start(2)
                .build(),
        );

        // ── Red Warning Banner ───────────────────────────────────────
        let banner = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .css_classes(["card"])
            .spacing(0)
            .build();

        let accent = gtk::DrawingArea::builder()
            .content_width(4)
            .vexpand(true)
            .halign(gtk::Align::Fill)
            .valign(gtk::Align::Fill)
            .build();
        accent.set_draw_func(|_, ctx, w, h| {
            ctx.set_source_rgb(0.93, 0.20, 0.20);
            ctx.rectangle(0.0, 0.0, w as f64, h as f64);
            let _ = ctx.fill();
        });
        banner.append(&accent);

        let banner_inner = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(6)
            .hexpand(true)
            .margin_start(10)
            .margin_end(10)
            .margin_top(6)
            .margin_bottom(6)
            .build();

        banner_inner.append(
            &gtk::Image::builder()
                .icon_name("dialog-warning-symbolic")
                .pixel_size(24)
                .build(),
        );

        let banner_text = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(2)
            .hexpand(true)
            .build();
        banner_text.append(
            &gtk::Label::builder()
                .label(&i18n(
                    "Warning: This will consume system memory and may cause instability",
                ))
                .css_classes(["heading"])
                .halign(gtk::Align::Start)
                .wrap(true)
                .build(),
        );
        banner_text.append(
            &gtk::Label::builder()
                .label(&i18n(
                    "Ensure important work is saved before running this test.",
                ))
                .css_classes(["caption"])
                .halign(gtk::Align::Start)
                .build(),
        );
        banner_inner.append(&banner_text);
        banner.append(&banner_inner);
        inner.append(&banner);

        // ── Target Pressure ──────────────────────────────────────────
        let total_ram = sysctl::read_total_ram().unwrap_or(32 * 1024 * 1024 * 1024);
        let ram_gb = total_ram as f64 / (1024.0 * 1024.0 * 1024.0);
        let default_pct = 75.0;
        let default_bytes = (total_ram as f64 * default_pct / 100.0) as u64;
        let default_gb = default_bytes as f64 / (1024.0 * 1024.0 * 1024.0);

        let config_group = adw::PreferencesGroup::builder().build();

        let pressure_scale = gtk::Scale::builder()
            .orientation(gtk::Orientation::Horizontal)
            .adjustment(&gtk::Adjustment::new(75.0, 25.0, 200.0, 1.0, 5.0, 0.0))
            .draw_value(true)
            .value_pos(gtk::PositionType::Right)
            .hexpand(true)
            .width_request(200)
            .valign(gtk::Align::Center)
            .build();
        let pressure_label = gtk::Label::builder()
            .label(&format!("{:.1} GiB ({:.0}%)", default_gb, default_pct))
            .css_classes(["caption"])
            .halign(gtk::Align::Start)
            .build();
        let pressure_row = adw::ActionRow::builder()
            .title(&i18n("Target Pressure"))
            .subtitle(&format!(
                "{} {:.0} GiB · {} 25%–200% · {} 75%",
                i18n("RAM"),
                ram_gb,
                i18n("range"),
                i18n("default"),
            ))
            .build();
        pressure_row.add_suffix(&pressure_label);
        pressure_row.add_suffix(&pressure_scale);
        config_group.add(&pressure_row);
        inner.append(&config_group);
        *imp.pressure_scale.borrow_mut() = Some(pressure_scale);
        *imp.pressure_label.borrow_mut() = Some(pressure_label);

        // ── Advanced Settings ────────────────────────────────────────
        let adv_group = adw::PreferencesGroup::builder().build();
        let expander = adw::ExpanderRow::builder()
            .title(&i18n("Advanced settings"))
            .build();

        // Hold time
        let hold_spin = gtk::SpinButton::with_range(1.0, 60.0, 1.0);
        hold_spin.set_value(3.0);
        let hold_row = adw::ActionRow::builder()
            .title(&i18n("Maximum hold time"))
            .subtitle(&i18n("seconds"))
            .build();
        hold_row.add_suffix(&hold_spin);
        expander.add_row(&hold_row);
        *imp.hold_spin.borrow_mut() = Some(hold_spin);

        // Growth speed
        let growth_scale = gtk::Scale::builder()
            .orientation(gtk::Orientation::Horizontal)
            .adjustment(&gtk::Adjustment::new(2.0, 0.5, 8.0, 0.5, 1.0, 0.0))
            .draw_value(true)
            .value_pos(gtk::PositionType::Right)
            .hexpand(true)
            .width_request(200)
            .valign(gtk::Align::Center)
            .build();
        let growth_row = adw::ActionRow::builder()
            .title(&i18n("Growth speed"))
            .subtitle(&format!("{}: {:.1} GB/s", i18n("Growth speed"), 2.0))
            .build();
        growth_row.add_suffix(&growth_scale);
        expander.add_row(&growth_row);
        *imp.growth_scale.borrow_mut() = Some(growth_scale);
        *imp.growth_row.borrow_mut() = Some(growth_row);

        adv_group.add(&expander);
        inner.append(&adv_group);

        // Collect config rows for sensitivity toggling
        *imp.config_rows.borrow_mut() = vec![
            config_group.upcast::<gtk::Widget>(),
            adv_group.upcast::<gtk::Widget>(),
        ];

        // ── Status Stack ─────────────────────────────────────────────
        let status_stack = gtk::Stack::builder()
            .transition_type(gtk::StackTransitionType::Crossfade)
            .build();

        // Page "idle" — wrap button in centered box so it doesn't stretch
        let idle_box = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .halign(gtk::Align::Center)
            .margin_top(18)
            .build();
        let run_button = gtk::Button::builder()
            .label(&i18n("Run Stress Test"))
            .css_classes(["destructive-action", "pill"])
            .build();
        idle_box.append(&run_button);
        status_stack.add_named(&idle_box, Some("idle"));
        *imp.run_button.borrow_mut() = Some(run_button);

        // Page "running"
        let running_box = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(12)
            .margin_top(18)
            .build();

        let progress_bar = gtk::ProgressBar::builder()
            .hexpand(true)
            .show_text(false)
            .build();
        running_box.append(&progress_bar);
        *imp.progress_bar.borrow_mut() = Some(progress_bar);

        let progress_label = gtk::Label::builder()
            .label("")
            .css_classes(["title-4"])
            .halign(gtk::Align::Center)
            .build();
        running_box.append(&progress_label);
        *imp.progress_label.borrow_mut() = Some(progress_label);

        let mem_stats_label = gtk::Label::builder()
            .label("")
            .css_classes(["caption"])
            .halign(gtk::Align::Center)
            .wrap(true)
            .build();
        running_box.append(&mem_stats_label);
        *imp.mem_stats_label.borrow_mut() = Some(mem_stats_label);

        let cancel_button = gtk::Button::builder()
            .label(&i18n("Cancel Test"))
            .halign(gtk::Align::Center)
            .margin_top(12)
            .build();
        running_box.append(&cancel_button);
        *imp.cancel_button.borrow_mut() = Some(cancel_button);

        status_stack.add_named(&running_box, Some("running"));

        // Page "results"
        let results_box = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(8)
            .css_classes(["card"])
            .margin_top(18)
            .build();
        let results_inner = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(8)
            .margin_start(16)
            .margin_end(16)
            .margin_top(16)
            .margin_bottom(16)
            .build();

        results_inner.append(
            &gtk::Label::builder()
                .label(&i18n("Test Complete"))
                .css_classes(["heading"])
                .halign(gtk::Align::Center)
                .build(),
        );
        let results_label = gtk::Label::builder()
            .label("")
            .css_classes(["caption"])
            .halign(gtk::Align::Center)
            .wrap(true)
            .build();
        results_inner.append(&results_label);
        *imp.results_label.borrow_mut() = Some(results_label);

        let run_again_button = gtk::Button::builder()
            .label(&i18n("Run Again"))
            .halign(gtk::Align::Center)
            .css_classes(["pill"])
            .margin_top(8)
            .build();
        results_inner.append(&run_again_button);
        *imp.run_again_button.borrow_mut() = Some(run_again_button);

        results_box.append(&results_inner);
        status_stack.add_named(&results_box, Some("results"));

        status_stack.set_visible_child_name("idle");
        inner.append(&status_stack);
        *imp.status_stack.borrow_mut() = Some(status_stack);

        self.append(&scroll);
        self.connect_signals();
    }

    // ── Signal Wiring ───────────────────────────────────────────────

    fn connect_signals(&self) {
        let imp = self.imp();

        // Pressure scale → update label
        if let Some(scale) = imp.pressure_scale.borrow().as_ref() {
            let weak = self.downgrade();
            scale.connect_value_changed(move |s| {
                if let Some(v) = weak.upgrade() {
                    v.update_pressure_label(s.value());
                }
            });
        }

        // Growth scale → update row subtitle
        if let Some(scale) = imp.growth_scale.borrow().as_ref() {
            let weak = self.downgrade();
            scale.connect_value_changed(move |s| {
                if let Some(v) = weak.upgrade() {
                    if let Some(row) = v.imp().growth_row.borrow().as_ref() {
                        let gb_s = s.value();
                        row.set_subtitle(&format!("{}: {:.1} GB/s", i18n("Growth speed"), gb_s));
                    }
                }
            });
        }

        // Run button
        if let Some(btn) = imp.run_button.borrow().as_ref() {
            let weak = self.downgrade();
            btn.connect_clicked(move |_| {
                if let Some(v) = weak.upgrade() {
                    v.start_test();
                }
            });
        }

        // Cancel button
        if let Some(btn) = imp.cancel_button.borrow().as_ref() {
            let weak = self.downgrade();
            btn.connect_clicked(move |_| {
                if let Some(v) = weak.upgrade() {
                    v.cancel_test();
                }
            });
        }

        // Run Again button
        if let Some(btn) = imp.run_again_button.borrow().as_ref() {
            let weak = self.downgrade();
            btn.connect_clicked(move |_| {
                if let Some(v) = weak.upgrade() {
                    if let Some(stack) = v.imp().status_stack.borrow().as_ref() {
                        stack.set_visible_child_name("idle");
                    }
                }
            });
        }
    }

    // ── Public API ──────────────────────────────────────────────────

    pub fn refresh_data(&self) {
        // Update pressure label to reflect current RAM
        if let Some(scale) = self.imp().pressure_scale.borrow().as_ref() {
            self.update_pressure_label(scale.value());
        }
    }

    // ── Private Helpers ─────────────────────────────────────────────

    fn update_pressure_label(&self, pct: f64) {
        let total_ram = sysctl::read_total_ram().unwrap_or(32 * 1024 * 1024 * 1024);
        let target_bytes = (total_ram as f64 * pct / 100.0) as u64;
        let target_gb = target_bytes as f64 / (1024.0 * 1024.0 * 1024.0);
        if let Some(l) = self.imp().pressure_label.borrow().as_ref() {
            l.set_text(&format!("{:.1} GiB ({:.0}%)", target_gb, pct));
        }
    }

    fn set_config_sensitive(&self, sensitive: bool) {
        for row in self.imp().config_rows.borrow().iter() {
            row.set_sensitive(sensitive);
        }
    }

    fn start_test(&self) {
        let imp = self.imp();
        if *imp.test_running.borrow() {
            return;
        }

        // Read configuration
        let total_ram = sysctl::read_total_ram().unwrap_or(32 * 1024 * 1024 * 1024);
        let pct = imp
            .pressure_scale
            .borrow()
            .as_ref()
            .map(|s| s.value())
            .unwrap_or(75.0);
        let target_bytes = (total_ram as f64 * pct / 100.0) as u64;
        let hold_secs = imp
            .hold_spin
            .borrow()
            .as_ref()
            .map(|s| s.value())
            .unwrap_or(3.0);
        let growth_gb_s = imp
            .growth_scale
            .borrow()
            .as_ref()
            .map(|s| s.value())
            .unwrap_or(2.0);
        let growth_rate = (growth_gb_s * 1_000_000_000.0) as u64;

        // Transition UI to running
        *imp.test_running.borrow_mut() = true;
        self.set_config_sensitive(false);

        if let Some(stack) = imp.status_stack.borrow().as_ref() {
            stack.set_visible_child_name("running");
        }
        if let Some(bar) = imp.progress_bar.borrow().as_ref() {
            bar.set_fraction(0.0);
        }
        if let Some(l) = imp.progress_label.borrow().as_ref() {
            let target_gb = target_bytes as f64 / (1024.0 * 1024.0 * 1024.0);
            l.set_text(&format!("{:.1} / {:.1} GiB", 0.0, target_gb));
        }

        // Create cancellation channel
        let cancel = Arc::new(AtomicBool::new(false));
        *imp.cancel_flag.borrow_mut() = Some(cancel.clone());

        let (tx, rx) = async_channel::bounded::<stress::StressEvent>(128);

        // Spawn allocation thread
        let tx_thread = tx.clone();
        let cancel_thread = cancel.clone();
        std::thread::spawn(move || {
            stress::run_stress_test(
                target_bytes,
                hold_secs,
                growth_rate,
                cancel_thread,
                tx_thread,
            );
        });
        drop(tx); // only the spawned thread holds a sender now

        // Spawn GLib receiver
        let weak = self.downgrade();
        glib::spawn_future_local(async move {
            while let Ok(event) = rx.recv().await {
                let Some(view) = weak.upgrade() else { break };
                match event {
                    stress::StressEvent::Progress {
                        allocated_bytes,
                        target_bytes: tgt,
                        ..
                    } => {
                        let frac = allocated_bytes as f64 / tgt as f64;
                        let alloc_gb = allocated_bytes as f64 / (1024.0 * 1024.0 * 1024.0);
                        let tgt_gb = tgt as f64 / (1024.0 * 1024.0 * 1024.0);
                        if let Some(bar) = view.imp().progress_bar.borrow().as_ref() {
                            bar.set_fraction(frac);
                        }
                        if let Some(l) = view.imp().progress_label.borrow().as_ref() {
                            l.set_text(&format!("{:.1} / {:.1} GiB", alloc_gb, tgt_gb));
                        }
                    }
                    stress::StressEvent::Result(result) => {
                        view.show_results(result);
                        break;
                    }
                }
            }
        });

        // Start memory stats timer (500 ms)
        let weak_stats = self.downgrade();
        let stats_id = glib::timeout_add_local(Duration::from_millis(500), move || {
            let Some(view) = weak_stats.upgrade() else {
                return glib::ControlFlow::Break;
            };
            if *view.imp().test_running.borrow() {
                view.refresh_mem_stats();
                glib::ControlFlow::Continue
            } else {
                glib::ControlFlow::Break
            }
        });
        *imp.stats_timer.borrow_mut() = Some(stats_id);
    }

    fn cancel_test(&self) {
        if let Some(cancel) = self.imp().cancel_flag.borrow().as_ref() {
            cancel.store(true, Ordering::SeqCst);
        }
    }

    fn show_results(&self, result: stress::StressTestResult) {
        let imp = self.imp();

        // Stop stats timer
        if let Some(id) = imp.stats_timer.borrow_mut().take() {
            id.remove();
        }

        *imp.test_running.borrow_mut() = false;
        *imp.cancel_flag.borrow_mut() = None;
        self.set_config_sensitive(true);

        // Build result text
        let target_gb = result.target_bytes as f64 / (1024.0 * 1024.0 * 1024.0);
        let peak_gb = result.peak_allocated_bytes as f64 / (1024.0 * 1024.0 * 1024.0);
        let secs = result.duration.as_secs_f64();

        let summary = if result.cancelled {
            format!(
                "{}\n\n{} {:.1} GiB / {:.1} GiB\n{} {:.0}s",
                i18n("Cancelled by user."),
                i18n("Peak allocated:"),
                peak_gb,
                target_gb,
                i18n("Duration:"),
                secs,
            )
        } else if let Some(ref e) = result.error {
            format!("{}: {}", i18n("Error"), e)
        } else {
            format!(
                "{}\n\n{} {:.1} GiB / {:.1} GiB\n{} {:.1}s",
                i18n("Completed successfully — memory pressure test passed."),
                i18n("Peak allocated:"),
                peak_gb,
                target_gb,
                i18n("Duration:"),
                secs,
            )
        };

        if let Some(l) = imp.results_label.borrow().as_ref() {
            l.set_text(&summary);
        }

        if let Some(stack) = imp.status_stack.borrow().as_ref() {
            stack.set_visible_child_name("results");
        }

        // Ensure progress bar shows final state
        if let Some(bar) = imp.progress_bar.borrow().as_ref() {
            let frac = if result.target_bytes > 0 {
                result.peak_allocated_bytes as f64 / result.target_bytes as f64
            } else {
                0.0
            };
            bar.set_fraction(frac);
        }
    }

    fn refresh_mem_stats(&self) {
        // Read /proc/meminfo (same pattern as dashboard_view)
        let mut total: u64 = 0;
        let mut free: u64 = 0;
        let mut avail: u64 = 0;
        let mut swap_total: u64 = 0;
        let mut swap_free: u64 = 0;

        if let Ok(content) = std::fs::read_to_string("/proc/meminfo") {
            for line in content.lines() {
                let p: Vec<&str> = line.split_whitespace().collect();
                if p.len() < 2 {
                    continue;
                }
                let kb: u64 = p[1].parse().unwrap_or(0);
                match p[0].trim_end_matches(':') {
                    "MemTotal" => total = kb * 1024,
                    "MemFree" => free = kb * 1024,
                    "MemAvailable" => avail = kb * 1024,
                    "SwapTotal" => swap_total = kb * 1024,
                    "SwapFree" => swap_free = kb * 1024,
                    _ => {}
                }
            }
        }

        let used = total.saturating_sub(free);
        let swap_used = swap_total.saturating_sub(swap_free);
        let total_gb = total as f64 / (1024.0 * 1024.0 * 1024.0);
        let used_gb = used as f64 / (1024.0 * 1024.0 * 1024.0);
        let avail_gb = avail as f64 / (1024.0 * 1024.0 * 1024.0);
        let swap_gb = swap_used as f64 / (1024.0 * 1024.0 * 1024.0);

        if let Some(l) = self.imp().mem_stats_label.borrow().as_ref() {
            l.set_text(&format!(
                "RAM: {:.1} GiB total · {:.1} GiB used · {:.1} GiB avail\nSwap used: {:.1} GiB",
                total_gb, used_gb, avail_gb, swap_gb
            ));
        }
    }
}
