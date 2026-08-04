use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;
use std::cell::RefCell;

use crate::i18n::{i18n, i18n_fmt};
use crate::swap::{hibernation, persist, swapfile, sysctl, zram, zswap};
use crate::utils;
use crate::widgets::usage_bar::UsageBar;

// Canonical list — keep in sync with zswap::get_available_compressors()
const COMPRESSORS: &[&str] = &["lz4", "lz4hc", "zstd", "lzo-rle", "lzo", "deflate", "842"];

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct SwapView {
        pub operation_running: RefCell<bool>,
        pub usage_bar: RefCell<Option<UsageBar>>,
        pub partition_icon: RefCell<Option<gtk::Image>>,
        pub partition_row: RefCell<Option<adw::ActionRow>>,
        pub status_icon: RefCell<Option<gtk::Image>>,
        pub enable_row: RefCell<Option<adw::SwitchRow>>,
        pub swappiness_scale: RefCell<Option<gtk::Scale>>,
        pub size_scale: RefCell<Option<gtk::Scale>>,
        pub remove_file_btn: RefCell<Option<gtk::Button>>,
        pub settings_group: RefCell<Option<adw::PreferencesGroup>>,
        pub size_row: RefCell<Option<adw::ActionRow>>,
        pub adv_group: RefCell<Option<adw::PreferencesGroup>>,
        pub expander: RefCell<Option<adw::ExpanderRow>>,
        pub apply_revealer: RefCell<Option<gtk::Revealer>>,
        pub apply_btn: RefCell<Option<gtk::Button>>,
        pub spinner: RefCell<Option<gtk::Spinner>>,
        pub refreshing: RefCell<bool>,
        pub orig_swap: RefCell<u8>,
        pub orig_size: RefCell<u64>,
        // Zswap widgets
        pub zswap_switch: RefCell<Option<adw::SwitchRow>>,
        pub compressor_dropdown: RefCell<Option<adw::ComboRow>>,
        pub pool_scale: RefCell<Option<gtk::Scale>>,
        pub threshold_scale: RefCell<Option<gtk::Scale>>,
        pub shrinker_switch: RefCell<Option<adw::SwitchRow>>,
        pub zswap_rows: RefCell<Vec<gtk::Widget>>,
        pub orig_compressor: RefCell<String>,
        pub orig_pool: RefCell<u8>,
        pub orig_threshold: RefCell<u8>,
        pub orig_shrinker: RefCell<bool>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for SwapView {
        const NAME: &'static str = "SwapView";
        type Type = super::SwapView;
        type ParentType = gtk::Box;
    }
    impl ObjectImpl for SwapView {
        fn constructed(&self) {
            self.parent_constructed();
            self.obj().setup_ui();
        }
    }
    impl WidgetImpl for SwapView {}
    impl BoxImpl for SwapView {}
}

glib::wrapper! {
    pub struct SwapView(ObjectSubclass<imp::SwapView>)
        @extends gtk::Widget, gtk::Box,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

impl SwapView {
    pub fn new() -> Self {
        glib::Object::builder().build()
    }

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

        // Title + subtitle
        inner.append(
            &gtk::Label::builder()
                .label(&i18n("Disk Swap Configuration"))
                .css_classes(["title-1"])
                .halign(gtk::Align::Start)
                .build(),
        );
        inner.append(
            &gtk::Label::builder()
                .label(&i18n(
                    "Review installer-managed swap partitions and manage the legacy supplementary swap file",
                ))
                .css_classes(["caption"])
                .halign(gtk::Align::Start)
                .margin_start(2)
                .build(),
        );

        // Recommendation
        {
            let sw = sysctl::recommended_swappiness();
            let has_zram = !zram::read_zram_devices().is_empty();
            let sw_reason = if has_zram {
                i18n("with Zram")
            } else {
                let total_ram = sysctl::read_total_ram().unwrap_or(32 * 1024 * 1024 * 1024);
                let ram_gb = total_ram as f64 / (1024.0 * 1024.0 * 1024.0);
                i18n_fmt("for {0} GiB RAM", &[&format!("{:.0}", ram_gb)])
            };
            inner.append(
                &gtk::Label::builder()
                    .use_markup(true)
                    .label(&i18n_fmt(
                        "<i>Recommended swappiness: {0} ({1})</i>",
                        &[&sw.to_string(), &sw_reason],
                    ))
                    .css_classes(["caption"])
                    .halign(gtk::Align::Start)
                    .margin_start(2)
                    .build(),
            );
        }

        // Spinner
        let spinner = gtk::Spinner::builder()
            .halign(gtk::Align::Center)
            .visible(false)
            .build();
        inner.append(&spinner);
        *imp.spinner.borrow_mut() = Some(spinner);

        // Usage bar — always visible at the top
        let usage_bar = UsageBar::new(&i18n("Swap usage"), (0.21, 0.52, 0.89));
        inner.append(&usage_bar);
        *imp.usage_bar.borrow_mut() = Some(usage_bar);

        // ─── Main settings: partition status + legacy swapfile ────────
        let settings_group = adw::PreferencesGroup::builder().build();

        let partition_row = adw::ActionRow::builder()
            .title(&i18n("Swap partition"))
            .subtitle(&i18n("Loading..."))
            .build();
        let partition_icon = gtk::Image::builder()
            .icon_name("drive-harddisk-symbolic")
            .pixel_size(24)
            .build();
        partition_row.add_prefix(&partition_icon);
        settings_group.add(&partition_row);
        *imp.partition_icon.borrow_mut() = Some(partition_icon);
        *imp.partition_row.borrow_mut() = Some(partition_row);

        // The switch controls /swapfile only. It never targets a partition.
        let enable_row = adw::SwitchRow::builder()
            .title(&i18n("Additional swap file"))
            .subtitle(&i18n("Loading..."))
            .build();
        let status_icon = gtk::Image::builder().pixel_size(24).build();
        enable_row.add_prefix(&status_icon);
        settings_group.add(&enable_row);
        *imp.status_icon.borrow_mut() = Some(status_icon);
        *imp.enable_row.borrow_mut() = Some(enable_row);

        // Zswap — mutually exclusive with Zram; no "Recommended" label
        let zswap_icon = gtk::Image::builder()
            .icon_name("document-save-symbolic")
            .pixel_size(24)
            .build();
        let zswap_sub = i18n("Compress swap pages in a RAM pool — faster than disk swap. Use Zram or Zswap, not both.");
        let zswap_switch_row = adw::SwitchRow::builder()
            .title(&i18n("Zswap"))
            .subtitle(&zswap_sub)
            .build();
        zswap_switch_row.add_prefix(&zswap_icon);
        settings_group.add(&zswap_switch_row);
        *imp.zswap_switch.borrow_mut() = Some(zswap_switch_row);

        // Optional compatibility file. Zero means absent; partition capacity is
        // intentionally not copied into this control.
        let size_row = adw::ActionRow::builder()
            .title(&i18n("Additional swap file size"))
            .subtitle(&i18n("0 GiB means no supplementary swap file"))
            .build();
        let size_scale = gtk::Scale::builder()
            .orientation(gtk::Orientation::Horizontal)
            .adjustment(&gtk::Adjustment::new(0.0, 0.0, 64.0, 1.0, 4.0, 0.0))
            .draw_value(true)
            .value_pos(gtk::PositionType::Right)
            .hexpand(true)
            .width_request(200)
            .build();
        let remove_file_btn = gtk::Button::builder()
            .label(&i18n("Remove incompatible file"))
            .css_classes(["destructive-action"])
            .valign(gtk::Align::Center)
            .visible(false)
            .build();
        size_row.add_suffix(&remove_file_btn);
        size_row.add_suffix(&size_scale);
        settings_group.add(&size_row);
        *imp.size_scale.borrow_mut() = Some(size_scale);
        *imp.remove_file_btn.borrow_mut() = Some(remove_file_btn);
        *imp.size_row.borrow_mut() = Some(size_row);

        inner.append(&settings_group);
        *imp.settings_group.borrow_mut() = Some(settings_group);

        // ─── Advanced: AdwExpanderRow inside PreferencesGroup ──────────
        let adv_group = adw::PreferencesGroup::builder().build();
        let expander = adw::ExpanderRow::builder()
            .title(&i18n("Advanced settings"))
            .build();

        let rec_sw = sysctl::recommended_swappiness();
        let has_zram_for_sw = !zram::read_zram_devices().is_empty();
        let sw_subtitle = if has_zram_for_sw {
            i18n_fmt("How aggressively the kernel swaps — with Zram active, set to {0} to prefer fast compressed RAM over disk cache (recommended: {0})", &[&rec_sw.to_string()])
        } else {
            i18n_fmt(
                "How aggressively the kernel swaps — lower = stay in RAM longer (recommended: {0})",
                &[&rec_sw.to_string()],
            )
        };

        // Swappiness
        let swappiness_scale = gtk::Scale::builder()
            .orientation(gtk::Orientation::Horizontal)
            .adjustment(&gtk::Adjustment::new(
                rec_sw as f64,
                0.0,
                100.0,
                1.0,
                10.0,
                0.0,
            ))
            .draw_value(true)
            .value_pos(gtk::PositionType::Right)
            .hexpand(true)
            .width_request(200)
            .valign(gtk::Align::Center)
            .build();
        let sw_row = adw::ActionRow::builder()
            .title(&i18n("Swappiness"))
            .subtitle(&sw_subtitle)
            .build();
        sw_row.add_suffix(&swappiness_scale);
        expander.add_row(&sw_row);
        *imp.swappiness_scale.borrow_mut() = Some(swappiness_scale);

        // Zswap advanced rows (visibility follows zswap state)
        let comp_row = adw::ComboRow::builder()
            .title(&i18n("Compression algorithm"))
            .subtitle(&i18n("lz4 = fast (recommended), zstd = best ratio, lzo = legacy. Changing requires zswap restart."))
            .model(&gtk::StringList::new(COMPRESSORS))
            .build();
        expander.add_row(&comp_row);

        let pool_scale = gtk::Scale::builder()
            .orientation(gtk::Orientation::Horizontal)
            .adjustment(&gtk::Adjustment::new(20.0, 1.0, 50.0, 1.0, 5.0, 0.0))
            .draw_value(true)
            .value_pos(gtk::PositionType::Right)
            .hexpand(true)
            .width_request(200)
            .valign(gtk::Align::Center)
            .build();
        let pool_row = adw::ActionRow::builder()
            .title(&i18n("Maximum pool percent"))
            .subtitle(&i18n(
                "Max % of RAM the compressed pool may occupy. Recommended: 20%",
            ))
            .build();
        pool_row.add_suffix(&pool_scale);
        expander.add_row(&pool_row);

        let threshold_scale = gtk::Scale::builder()
            .orientation(gtk::Orientation::Horizontal)
            .adjustment(&gtk::Adjustment::new(90.0, 0.0, 100.0, 1.0, 10.0, 0.0))
            .draw_value(true)
            .value_pos(gtk::PositionType::Right)
            .hexpand(true)
            .width_request(200)
            .valign(gtk::Align::Center)
            .build();
        let thresh_row = adw::ActionRow::builder()
            .title(&i18n("Accept threshold percent"))
            .subtitle(&i18n("Only store pages that compress to ≤ this % of original. Higher = more pages cached. Recommended: 90%"))
            .build();
        thresh_row.add_suffix(&threshold_scale);
        expander.add_row(&thresh_row);

        let shrinker_row = adw::SwitchRow::builder()
            .title(&i18n("Shrinker"))
            .subtitle(&i18n(
                "Reclaim pool pages under memory pressure. Recommended: ON",
            ))
            .build();
        expander.add_row(&shrinker_row);

        adv_group.add(&expander);
        inner.append(&adv_group);
        *imp.adv_group.borrow_mut() = Some(adv_group);
        *imp.expander.borrow_mut() = Some(expander);
        *imp.zswap_rows.borrow_mut() = vec![
            comp_row.clone().upcast::<gtk::Widget>(),
            pool_row.upcast::<gtk::Widget>(),
            thresh_row.upcast::<gtk::Widget>(),
            shrinker_row.clone().upcast::<gtk::Widget>(),
        ];
        *imp.compressor_dropdown.borrow_mut() = Some(comp_row);
        *imp.pool_scale.borrow_mut() = Some(pool_scale);
        *imp.threshold_scale.borrow_mut() = Some(threshold_scale);
        *imp.shrinker_switch.borrow_mut() = Some(shrinker_row);

        // ─── Apply button (revealed on change) ─────────────────────
        let revealer = gtk::Revealer::builder()
            .transition_type(gtk::RevealerTransitionType::SlideDown)
            .build();
        let apply_btn = gtk::Button::builder()
            .label(&i18n("Apply Settings"))
            .halign(gtk::Align::Center)
            .css_classes(["suggested-action", "pill"])
            .build();
        revealer.set_child(Some(&apply_btn));
        inner.append(&revealer);
        *imp.apply_btn.borrow_mut() = Some(apply_btn);
        *imp.apply_revealer.borrow_mut() = Some(revealer);

        self.append(&scroll);
        self.connect_signals();
    }

    fn connect_signals(&self) {
        let imp = self.imp();

        // Enable/Disable switch — use notify::active (fires AFTER visual change)
        if let Some(sw) = imp.enable_row.borrow().as_ref() {
            let weak_self = self.downgrade();
            sw.connect_notify_local(Some("active"), move |sw, _| {
                if let Some(v) = weak_self.upgrade() {
                    if *v.imp().refreshing.borrow() {
                        return;
                    }
                    v.toggle_swap(sw.is_active());
                }
            });
        }

        // Apply button
        if let Some(btn) = imp.apply_btn.borrow().as_ref() {
            let weak_self = self.downgrade();
            btn.connect_clicked(move |_| {
                if let Some(v) = weak_self.upgrade() {
                    v.apply_all();
                }
            });
        }

        if let Some(btn) = imp.remove_file_btn.borrow().as_ref() {
            let weak_self = self.downgrade();
            btn.connect_clicked(move |_| {
                if let Some(view) = weak_self.upgrade() {
                    view.run_op(i18n("Removing incompatible swap file…"), || {
                        swapfile::resize_swapfile(0).map(|_| ())
                    });
                }
            });
        }

        // Zswap enable/disable switch
        if let Some(zsw) = imp.zswap_switch.borrow().as_ref() {
            let weak_self = self.downgrade();
            zsw.connect_notify_local(Some("active"), move |zsw, _| {
                if let Some(v) = weak_self.upgrade() {
                    if *v.imp().refreshing.borrow() {
                        return;
                    }
                    v.toggle_zswap(zsw.is_active());
                }
            });
        }

        // Track changes on advanced controls
        let weak_self = self.downgrade();
        let check = move || {
            if let Some(v) = weak_self.upgrade() {
                if !*v.imp().refreshing.borrow() {
                    v.check_changes();
                }
            }
        };
        if let Some(s) = imp.swappiness_scale.borrow().as_ref() {
            let c = check.clone();
            s.connect_value_changed(move |_| c());
        }
        if let Some(s) = imp.size_scale.borrow().as_ref() {
            let c = check.clone();
            s.connect_value_changed(move |_| c());
        }
        if let Some(d) = imp.compressor_dropdown.borrow().as_ref() {
            let c = check.clone();
            d.connect_notify_local(Some("selected"), move |_, _| c());
        }
        if let Some(s) = imp.pool_scale.borrow().as_ref() {
            let c = check.clone();
            s.connect_value_changed(move |_| c());
        }
        if let Some(s) = imp.threshold_scale.borrow().as_ref() {
            let c = check.clone();
            s.connect_value_changed(move |_| c());
        }
        if let Some(s) = imp.shrinker_switch.borrow().as_ref() {
            let c = check;
            s.connect_notify_local(Some("active"), move |_, _| c());
        }
    }

    fn check_changes(&self) {
        let imp = self.imp();
        let sw = imp
            .swappiness_scale
            .borrow()
            .as_ref()
            .map(|s| s.value() as u8)
            .unwrap_or(10);
        let sz = imp
            .size_scale
            .borrow()
            .as_ref()
            .map(|s| s.value() as u64)
            .unwrap_or(2);
        let comp = imp
            .compressor_dropdown
            .borrow()
            .as_ref()
            .and_then(|d| {
                let idx = d.selected() as usize;
                COMPRESSORS.get(idx).map(|s| s.to_string())
            })
            .unwrap_or_else(|| "lz4".to_string());
        let pool = imp
            .pool_scale
            .borrow()
            .as_ref()
            .map(|s| s.value() as u8)
            .unwrap_or(20);
        let thresh = imp
            .threshold_scale
            .borrow()
            .as_ref()
            .map(|s| s.value() as u8)
            .unwrap_or(90);
        let shrinker = imp
            .shrinker_switch
            .borrow()
            .as_ref()
            .map(|s| s.is_active())
            .unwrap_or(true);
        let changed = sw != *imp.orig_swap.borrow()
            || sz != *imp.orig_size.borrow()
            || comp != *imp.orig_compressor.borrow()
            || pool != *imp.orig_pool.borrow()
            || thresh != *imp.orig_threshold.borrow()
            || shrinker != *imp.orig_shrinker.borrow();
        if let Some(r) = imp.apply_revealer.borrow().as_ref() {
            r.set_reveal_child(changed);
        }
    }

    fn run_op(&self, msg: String, task: impl FnOnce() -> Result<(), String> + Send + 'static) {
        if self.imp().operation_running.borrow().clone() {
            return;
        }
        *self.imp().operation_running.borrow_mut() = true;
        let w = self.downgrade();
        glib::spawn_future_local(async move {
            let win = w
                .upgrade()
                .and_then(|v| v.root().and_then(|r| r.downcast::<gtk::Window>().ok()));
            let result: Result<(), String> = if let Some(p) = win {
                crate::progress_dialog::run_with_progress(&p, &msg, task).await
            } else {
                task()
            };
            if let Some(v) = w.upgrade() {
                *v.imp().operation_running.borrow_mut() = false;
                v.refresh_data();
                if let Err(e) = result {
                    let win = v.root().and_then(|r| r.downcast::<gtk::Window>().ok());
                    if let Some(p) = win {
                        utils::show_error(&p, &i18n("Error"), &e);
                    }
                }
                if let Some(r) = v.imp().apply_revealer.borrow().as_ref() {
                    r.set_reveal_child(false);
                }
            }
        });
    }

    fn toggle_zswap(&self, enable: bool) {
        let imp = self.imp();
        let weak = self.downgrade();
        let msg = if enable {
            i18n("Enabling Zswap...")
        } else {
            i18n("Disabling Zswap...")
        };

        // Read current UI selections so we persist what the user sees
        let comp = imp
            .compressor_dropdown
            .borrow()
            .as_ref()
            .and_then(|d| {
                let idx = d.selected() as usize;
                COMPRESSORS.get(idx).map(|s| s.to_string())
            })
            .unwrap_or_else(|| "lz4".to_string());
        let pool = imp
            .pool_scale
            .borrow()
            .as_ref()
            .map(|s| s.value() as u8)
            .unwrap_or(20);
        let thresh = imp
            .threshold_scale
            .borrow()
            .as_ref()
            .map(|s| s.value() as u8)
            .unwrap_or(90);
        let shrinker = imp
            .shrinker_switch
            .borrow()
            .as_ref()
            .map(|s| s.is_active())
            .unwrap_or(true);

        let do_it = move || {
            if let Some(v) = weak.upgrade() {
                let comp = comp.clone();
                v.run_op(msg, move || {
                    persist::persist_zswap(enable, &comp, pool, thresh, shrinker)
                        .map(|_| ())
                        .map_err(|e| e.to_string())
                });
            }
        };
        do_it();
    }
    fn toggle_swap(&self, enable: bool) {
        let is_active = swapfile::read_managed_swapfile()
            .map(|status| status.active)
            .unwrap_or(false);
        if enable == is_active {
            return;
        }
        let needs_confirm = is_active && hibernation::managed_swapfile_is_resume_target();
        let weak_self = self.downgrade();

        let do_toggle = move || {
            let msg = if is_active {
                i18n("Disabling swap…")
            } else {
                i18n("Enabling swap…")
            };
            if let Some(v) = weak_self.upgrade() {
                v.run_op(msg, move || {
                    if is_active {
                        swapfile::disable_swapfile().map(|_| ())
                    } else {
                        swapfile::enable_swapfile().map(|_| ())
                    }
                });
            }
        };

        if needs_confirm {
            let win = self.root().and_then(|r| r.downcast::<gtk::Window>().ok());
            if let Some(p) = win {
                utils::show_confirm(
                    &p,
                    &i18n("Hibernation Warning"),
                    &i18n("This swap file is the configured resume target. Disabling it will break hibernation. Continue?"),
                    do_toggle,
                );
            }
        } else {
            do_toggle();
        }
    }

    fn apply_all(&self) {
        let imp = self.imp();
        let sw = imp
            .swappiness_scale
            .borrow()
            .as_ref()
            .map(|s| s.value() as u8)
            .unwrap_or(10);
        let sz = imp
            .size_scale
            .borrow()
            .as_ref()
            .map(|s| s.value() as u64)
            .unwrap_or(0);
        let size_changed = sz != *imp.orig_size.borrow();
        let zswap_enabled = imp
            .zswap_switch
            .borrow()
            .as_ref()
            .map(|s| s.is_active())
            .unwrap_or(false);
        let comp = imp
            .compressor_dropdown
            .borrow()
            .as_ref()
            .and_then(|d| {
                let idx = d.selected() as usize;
                COMPRESSORS.get(idx).map(|s| s.to_string())
            })
            .unwrap_or_else(|| "lz4".to_string());
        let pool = imp
            .pool_scale
            .borrow()
            .as_ref()
            .map(|s| s.value() as u8)
            .unwrap_or(20);
        let thresh = imp
            .threshold_scale
            .borrow()
            .as_ref()
            .map(|s| s.value() as u8)
            .unwrap_or(90);
        let shrinker = imp
            .shrinker_switch
            .borrow()
            .as_ref()
            .map(|s| s.is_active())
            .unwrap_or(true);
        let zswap_changed = comp != *imp.orig_compressor.borrow()
            || pool != *imp.orig_pool.borrow()
            || thresh != *imp.orig_threshold.borrow()
            || shrinker != *imp.orig_shrinker.borrow();
        let weak_self = self.downgrade();
        let weak_self2 = weak_self.clone();

        let hibernation = hibernation::check_hibernation();
        let resize_blocked = size_changed && hibernation.managed_swapfile_is_target;

        let do_apply = move || {
            if let Some(v) = weak_self2.upgrade() {
                v.run_op(i18n("Applying settings…"), move || {
                    let mut errs = Vec::new();
                    // Resize first: it is the riskiest operation and must not
                    // leave unrelated settings partially applied on failure.
                    if size_changed {
                        if let Err(e) = swapfile::resize_swapfile(sz) {
                            return Err(format!("Resize: {}", e));
                        }
                    }
                    if let Err(e) = sysctl::set_swappiness(sw) {
                        errs.push(format!("Swappiness: {}", e));
                    }
                    // Only touch zswap persistence when the user actually changed
                    // zswap-specific parameters. Plain swapfile/swappiness changes
                    // must not opt the system into zswap management.
                    if zswap_changed {
                        if let Err(e) =
                            persist::persist_zswap(zswap_enabled, &comp, pool, thresh, shrinker)
                        {
                            errs.push(format!("Zswap: {}", e));
                        }
                    }
                    if errs.is_empty() {
                        Ok(())
                    } else {
                        Err(errs.join("\n"))
                    }
                });
            }
        };

        if resize_blocked {
            if let Some(view) = weak_self.upgrade() {
                let win = view.root().and_then(|r| r.downcast::<gtk::Window>().ok());
                if let Some(p) = win {
                    utils::show_error(
                        &p,
                        &i18n("Hibernation Warning"),
                        &i18n("This swap file is the configured resume target. Its physical offset would change, so resize is blocked until hibernation is disabled."),
                    );
                }
            }
        } else {
            do_apply();
        }
    }

    pub fn refresh_data(&self) {
        let imp = self.imp();
        *imp.refreshing.borrow_mut() = true;

        let aggregate = swapfile::read_swap_status().unwrap_or_default();
        let partitions = swapfile::read_swap_partitions().unwrap_or_default();
        let managed = swapfile::read_managed_swapfile().unwrap_or_default();
        let (filesystem_supported, filesystem) =
            swapfile::swapfile_management_support().unwrap_or((false, i18n("unknown")));
        let hibernation = hibernation::check_hibernation();
        let maximum_file_gib = swapfile::maximum_swapfile_size_gib(managed.size_bytes);
        let resize_supported = filesystem_supported
            && !hibernation.managed_swapfile_is_target
            && (managed.size_bytes > 0 || maximum_file_gib > 0);

        if let Some(icon) = imp.partition_icon.borrow().as_ref() {
            icon.set_icon_name(Some(if partitions.is_empty() {
                "drive-harddisk-symbolic"
            } else {
                "emblem-ok-symbolic"
            }));
        }
        if let Some(row) = imp.partition_row.borrow().as_ref() {
            if partitions.is_empty() {
                row.set_subtitle(&i18n(
                    "No swap partition — a legacy swap file can be managed below",
                ));
            } else {
                let details = partitions
                    .iter()
                    .map(|item| {
                        format!(
                            "{} · {:.1} GiB",
                            item.path,
                            item.size_bytes as f64 / (1024.0 * 1024.0 * 1024.0)
                        )
                    })
                    .collect::<Vec<_>>()
                    .join(", ");
                row.set_subtitle(&i18n_fmt(
                    "{0} — installer-managed; size cannot be changed while the system is running",
                    &[&details],
                ));
            }
        }

        if let Some(icon) = imp.status_icon.borrow().as_ref() {
            icon.set_icon_name(Some(if managed.active {
                "emblem-ok-symbolic"
            } else {
                "drive-harddisk-symbolic"
            }));
        }
        if let Some(row) = imp.enable_row.borrow().as_ref() {
            row.set_active(managed.active);
            row.set_sensitive(managed.active || (filesystem_supported && managed.size_bytes > 0));
            if managed.active {
                row.set_subtitle(&i18n_fmt(
                    "Active — {0} GiB used / {1} GiB total",
                    &[
                        &format!(
                            "{:.1}",
                            managed.used_bytes as f64 / (1024.0 * 1024.0 * 1024.0)
                        ),
                        &format!(
                            "{:.1}",
                            managed.size_bytes as f64 / (1024.0 * 1024.0 * 1024.0)
                        ),
                    ],
                ));
            } else if managed.size_bytes > 0 && !filesystem_supported {
                row.set_subtitle(&i18n_fmt(
                    "Present but inactive — activation is disabled on {0}",
                    &[&filesystem],
                ));
            } else if managed.size_bytes > 0 {
                row.set_subtitle(&i18n("Present but inactive"));
            } else if resize_supported {
                row.set_subtitle(&i18n("Not created — choose a size below to add one"));
            } else if filesystem_supported && maximum_file_gib == 0 {
                row.set_subtitle(&i18n("Not created — at least 20 GiB must remain free"));
            } else {
                row.set_subtitle(&i18n_fmt(
                    "Unavailable on {0}; the swap partition remains active",
                    &[&filesystem],
                ));
            }
        }

        if let Some(bar) = imp.usage_bar.borrow().as_ref() {
            if aggregate.active && aggregate.size_bytes > 0 {
                let frac = aggregate.used_bytes as f64 / aggregate.size_bytes as f64;
                let used_gb = aggregate.used_bytes as f64 / (1024.0 * 1024.0 * 1024.0);
                let total_gb = aggregate.size_bytes as f64 / (1024.0 * 1024.0 * 1024.0);
                bar.set_fraction(frac, &format!("{:.1} / {:.1} GiB", used_gb, total_gb));
            } else {
                bar.set_fraction(0.0, &i18n("Inactive"));
            }
        }

        let file_gib = if managed.size_bytes == 0 {
            0
        } else {
            managed.size_bytes.div_ceil(1024 * 1024 * 1024)
        };
        if let Some(scale) = imp.size_scale.borrow().as_ref() {
            scale.adjustment().set_upper(maximum_file_gib as f64);
            scale.set_value(file_gib as f64);
            scale.set_sensitive(resize_supported);
        }
        if let Some(button) = imp.remove_file_btn.borrow().as_ref() {
            button.set_visible(!filesystem_supported && managed.size_bytes > 0 && !managed.active);
        }
        if let Some(row) = imp.size_row.borrow().as_ref() {
            row.set_sensitive(resize_supported);
            let subtitle = if hibernation.managed_swapfile_is_target {
                i18n("Resize is locked because this file is the configured hibernation resume target")
            } else if resize_supported {
                i18n("Optional compatibility file; 0 GiB removes it and 20 GiB remains reserved for the system")
            } else if filesystem == "btrfs" {
                i18n("Disabled on Btrfs because a root-subvolume swap file would prevent Timeback snapshots")
            } else {
                i18n_fmt("Swap file resizing is unsupported on {0}", &[&filesystem])
            };
            row.set_subtitle(&subtitle);
        }
        *imp.orig_size.borrow_mut() = file_gib;

        if let Ok(val) = sysctl::read_swappiness() {
            if let Some(s) = imp.swappiness_scale.borrow().as_ref() {
                s.set_value(val as f64);
            }
            *imp.orig_swap.borrow_mut() = val;
        }

        // Zswap depends on any active disk-backed swap, not specifically /swapfile.
        let swap_active = aggregate.active;
        if let Some(bar) = imp.usage_bar.borrow().as_ref() {
            bar.set_visible(true);
        }
        if let Some(row) = imp.size_row.borrow().as_ref() {
            row.set_visible(true);
        }
        if let Some(row) = imp.zswap_switch.borrow().as_ref() {
            row.set_visible(swap_active);
        }
        if let Some(g) = imp.adv_group.borrow().as_ref() {
            g.set_visible(swap_active);
        }
        if swap_active {
            if let Ok(zc) = zswap::read_zswap_config() {
                if let Some(zsw) = imp.zswap_switch.borrow().as_ref() {
                    zsw.set_active(zc.enabled);
                }
                if let Some(d) = imp.compressor_dropdown.borrow().as_ref() {
                    let idx = COMPRESSORS
                        .iter()
                        .position(|c| *c == zc.compressor)
                        .unwrap_or(0);
                    d.set_selected(idx as u32);
                }
                if let Some(s) = imp.pool_scale.borrow().as_ref() {
                    s.set_value(zc.max_pool_percent as f64);
                }
                if let Some(s) = imp.threshold_scale.borrow().as_ref() {
                    s.set_value(zc.accept_threshold_percent as f64);
                }
                if let Some(s) = imp.shrinker_switch.borrow().as_ref() {
                    s.set_active(zc.shrinker_enabled);
                }
                for row in imp.zswap_rows.borrow().iter() {
                    row.set_visible(zc.enabled);
                }
                *imp.orig_compressor.borrow_mut() = zc.compressor;
                *imp.orig_pool.borrow_mut() = zc.max_pool_percent;
                *imp.orig_threshold.borrow_mut() = zc.accept_threshold_percent;
                *imp.orig_shrinker.borrow_mut() = zc.shrinker_enabled;
            }
        } else {
            for row in imp.zswap_rows.borrow().iter() {
                row.set_visible(false);
            }
        }

        if let Some(r) = imp.apply_revealer.borrow().as_ref() {
            r.set_reveal_child(false);
        }
        *imp.refreshing.borrow_mut() = false;
    }
}
