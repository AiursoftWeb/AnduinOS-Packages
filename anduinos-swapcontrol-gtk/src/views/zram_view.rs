use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;

use crate::i18n::{i18n, i18n_fmt};
use crate::swap::{persist, zram};
use crate::utils;
use crate::widgets::usage_bar::UsageBar;

mod imp {
    use super::*;
    use std::cell::RefCell;

    #[derive(Default)]
    pub struct ZramView {
        pub device_list: RefCell<Option<gtk::ListBox>>,
        pub stack: RefCell<Option<gtk::Stack>>,
        pub create_btn: RefCell<Option<gtk::Button>>,
        pub spinner: RefCell<Option<gtk::Spinner>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for ZramView {
        const NAME: &'static str = "ZramView";
        type Type = super::ZramView;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for ZramView {
        fn constructed(&self) {
            self.parent_constructed();
            let obj = self.obj();
            obj.setup_ui();
        }
    }

    impl WidgetImpl for ZramView {}
    impl BoxImpl for ZramView {}
}

glib::wrapper! {
    pub struct ZramView(ObjectSubclass<imp::ZramView>)
        @extends gtk::Widget, gtk::Box,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

impl ZramView {
    pub fn new() -> Self {
        glib::Object::builder().build()
    }

    fn setup_ui(&self) {
        let imp = self.imp();

        self.set_orientation(gtk::Orientation::Vertical);
        self.set_spacing(12);
        self.set_margin_start(24);
        self.set_margin_end(24);
        self.set_margin_top(24);
        self.set_margin_bottom(24);
        self.set_vexpand(true);

        // Title
        self.append(
            &gtk::Label::builder()
                .label(&i18n("Zram Devices"))
                .css_classes(["title-1"])
                .halign(gtk::Align::Start)
                .build(),
        );
        self.append(
            &gtk::Label::builder()
                .label(&i18n("Compressed RAM block device — fast in-memory swap"))
                .css_classes(["caption"])
                .halign(gtk::Align::Start)
                .margin_start(2)
                .build(),
        );

        // Recommendation
        {
            let total_ram =
                crate::swap::sysctl::read_total_ram().unwrap_or(32 * 1024 * 1024 * 1024);
            let ram_gb = total_ram as f64 / (1024.0 * 1024.0 * 1024.0);
            let rec_mb = (total_ram / 2 / (1024 * 1024)) as u64; // 50%
            self.append(
                &gtk::Label::builder()
                    .use_markup(true)
                    .label(&i18n_fmt(
                        "<i>Recommended: {0} MiB, lz4, priority 100 (for {1} GiB RAM)</i>",
                        &[&rec_mb.to_string(), &format!("{:.0}", ram_gb)],
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
        self.append(&spinner);
        *imp.spinner.borrow_mut() = Some(spinner);

        // Create button (always visible at the bottom)
        let create_btn = gtk::Button::builder()
            .label(&i18n("Create Zram Device"))
            .halign(gtk::Align::Center)
            .css_classes(["suggested-action", "pill"])
            .margin_top(12)
            .margin_bottom(6)
            .build();

        // Stack: empty state ↔ device list (ufwall Profiles pattern)
        let empty_page = adw::StatusPage::builder()
            .title(&i18n("No Zram Devices"))
            .description(&i18n(
                "Create a compressed RAM swap device for faster memory pressure handling.",
            ))
            .icon_name("media-flash")
            .hexpand(true)
            .vexpand(true)
            .build();

        let scroll = gtk::ScrolledWindow::builder().vexpand(true).build();
        let device_list = gtk::ListBox::builder()
            .css_classes(["boxed-list"])
            .selection_mode(gtk::SelectionMode::None)
            .build();
        scroll.set_child(Some(&device_list));

        let stack = gtk::Stack::builder()
            .transition_type(gtk::StackTransitionType::Crossfade)
            .hexpand(true)
            .vexpand(true)
            .build();
        stack.add_named(&empty_page, Some("empty"));
        stack.add_named(&scroll, Some("devices"));
        stack.set_visible_child_name("empty");

        self.append(&stack);
        *imp.device_list.borrow_mut() = Some(device_list);
        *imp.stack.borrow_mut() = Some(stack);

        // Bottom Create button (shown when devices exist)
        self.append(&create_btn);
        *imp.create_btn.borrow_mut() = Some(create_btn);

        // Connect signals
        self.connect_signals();
    }

    fn connect_signals(&self) {
        let imp = self.imp();

        if let Some(btn) = imp.create_btn.borrow().as_ref() {
            let weak_self = self.downgrade();
            btn.connect_clicked(move |_| {
                if let Some(view) = weak_self.upgrade() {
                    view.show_create_dialog();
                }
            });
        }
    }

    fn set_busy(&self, busy: bool) {
        let imp = self.imp();
        if let Some(spinner) = imp.spinner.borrow().as_ref() {
            spinner.set_visible(busy);
        }
        if let Some(btn) = imp.create_btn.borrow().as_ref() {
            btn.set_sensitive(!busy);
        }
    }

    fn show_error(&self, msg: &str) {
        let win = self.root().and_then(|r| r.downcast::<gtk::Window>().ok());
        if let Some(parent) = win {
            utils::show_error(&parent, &i18n("Error"), msg);
        }
    }

    fn show_create_dialog(&self) {
        let parent = self.root().and_then(|r| r.downcast::<gtk::Window>().ok());

        let dialog = gtk::Dialog::builder()
            .title(&i18n("Create Zram Device"))
            .modal(true)
            .build();

        if let Some(ref p) = parent {
            dialog.set_transient_for(Some(p));
        }

        let content = dialog.content_area();

        let _grid = gtk::Grid::builder()
            .row_spacing(12)
            .column_spacing(12)
            .margin_start(24)
            .margin_end(24)
            .margin_top(24)
            .margin_bottom(12)
            .build();

        let total_ram = crate::swap::sysctl::read_total_ram().unwrap_or(32 * 1024 * 1024 * 1024);
        let ram_gb = total_ram as f64 / (1024.0 * 1024.0 * 1024.0);
        let default_mb = (total_ram / 2 / (1024 * 1024)) as u64; // 50% of RAM
        let max_mb = (total_ram / (1024 * 1024)) as u64;

        // ─── Simple view: hint + slider ────────────────────────────
        let hint = gtk::Label::builder()
            .label(&format!(
                "{} MiB (50% of {:.0} GiB RAM — recommended)",
                default_mb, ram_gb
            ))
            .css_classes(["caption"])
            .halign(gtk::Align::Start)
            .margin_start(12)
            .margin_end(12)
            .build();
        content.append(&hint);

        let scale = gtk::Scale::builder()
            .orientation(gtk::Orientation::Horizontal)
            .adjustment(&gtk::Adjustment::new(
                default_mb as f64,
                512.0,
                max_mb as f64 + 512.0,
                256.0,
                1024.0,
                0.0,
            ))
            .draw_value(true)
            .value_pos(gtk::PositionType::Right)
            .margin_start(12)
            .margin_end(12)
            .margin_top(6)
            .build();
        content.append(&scale);

        // ─── Advanced expander ──────────────────────────────────────
        let expander = gtk::Expander::builder()
            .label(&i18n("Advanced options"))
            .margin_start(12)
            .margin_end(12)
            .margin_top(12)
            .build();

        let adv_grid = gtk::Grid::builder()
            .row_spacing(12)
            .column_spacing(12)
            .margin_start(12)
            .margin_top(8)
            .build();

        // Manual size input
        let size_spin = gtk::SpinButton::with_range(512.0, max_mb as f64 + 512.0, 64.0);
        size_spin.set_value(default_mb as f64);
        let spin_row = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(8)
            .build();
        spin_row.append(
            &gtk::Label::builder()
                .label(&i18n("Size (MiB)"))
                .halign(gtk::Align::Start)
                .build(),
        );
        spin_row.append(&size_spin);
        adv_grid.attach(&spin_row, 0, 0, 1, 1);

        // Sync slider ↔ spin
        scale.connect_value_changed({
            let s = size_spin.clone();
            move |sc| {
                s.set_value(sc.value());
            }
        });
        size_spin.connect_value_changed({
            let sc = scale.clone();
            move |s| {
                sc.set_value(s.value());
            }
        });

        // Algorithm
        let algos = zram::get_available_algorithms();
        let algo_list = gtk::StringList::new(&algos.iter().map(|s| s.as_str()).collect::<Vec<_>>());
        let algo_combo = gtk::DropDown::builder().model(&algo_list).build();
        if !algos.is_empty() {
            algo_combo.set_selected(0);
        }
        let algo_tag = gtk::Label::builder()
            .css_classes(["caption"])
            .halign(gtk::Align::Start)
            .build();
        let algo_info = gtk::Label::builder()
            .css_classes(["caption"])
            .halign(gtk::Align::Start)
            .wrap(true)
            .margin_start(4)
            .build();

        fn algo_info_for(name: &str) -> &'static str {
            match name {
                "lz4" => "⚡⚡ Fastest · ⭐⭐ Good  —  Recommended",
                "lzo-rle" => "⚡ Fast · ⭐⭐ Good  —  Desktop alternative",
                "zstd" => "⚡ Moderate · ⭐⭐⭐ Best  —  Best for small RAM",
                "lz4hc" => "⚡ Slow · ⭐⭐⭐ High  —  Server workloads",
                "lzo" => "⚡ Fast · ⭐ Moderate  —  Legacy (avoid)",
                "deflate" => "🐢 Slowest · ⭐⭐⭐ High  —  Avoid on desktops",
                "842" => "— · —  —  IBM POWER only",
                _ => "",
            }
        }

        let algos_cb = algos.clone();
        let ai = algo_info.clone();
        let at = algo_tag.clone();
        algo_combo.connect_selected_item_notify(move |c| {
            let idx = c.selected() as usize;
            if let Some(n) = algos_cb.get(idx) {
                at.set_text(if n == "lz4" { "(Recommended)" } else { "" });
                ai.set_text(algo_info_for(&n));
            }
        });
        // Init
        {
            let n = algos.get(0).map(|s| s.to_string()).unwrap_or_default();
            algo_tag.set_text(if n == "lz4" { "(Recommended)" } else { "" });
            algo_info.set_text(algo_info_for(&n));
        }

        let algo_row = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(4)
            .build();
        let algo_hdr = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(8)
            .build();
        algo_hdr.append(
            &gtk::Label::builder()
                .label(&i18n("Algorithm"))
                .halign(gtk::Align::Start)
                .build(),
        );
        algo_hdr.append(&algo_combo);
        algo_hdr.append(&algo_tag);
        algo_row.append(&algo_hdr);
        algo_row.append(&algo_info);
        adv_grid.attach(&algo_row, 0, 1, 1, 1);

        // Priority
        let prio_spin = gtk::SpinButton::with_range(-10.0, 32767.0, 1.0);
        prio_spin.set_value(100.0);
        let prio_row = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(8)
            .build();
        prio_row.append(
            &gtk::Label::builder()
                .label(&i18n("Priority"))
                .halign(gtk::Align::Start)
                .build(),
        );
        prio_row.append(&prio_spin);
        adv_grid.attach(&prio_row, 0, 2, 1, 1);

        expander.set_child(Some(&adv_grid));
        content.append(&expander);

        // Buttons
        dialog.add_button(&i18n("Cancel"), gtk::ResponseType::Cancel);
        dialog.add_button(&i18n("Create"), gtk::ResponseType::Ok);

        dialog.connect_response({
            let weak_self = self.downgrade();
            move |dialog, response| {
                if response != gtk::ResponseType::Ok {
                    dialog.close();
                    return;
                }
                let size_mb = size_spin.value() as u64;
                let algo_idx = algo_combo.selected() as usize;
                let algo = algos
                    .get(algo_idx)
                    .cloned()
                    .unwrap_or_else(|| "lzo-rle".to_string());
                let priority = prio_spin.value() as i32;

                dialog.close();

                if let Some(view) = weak_self.upgrade() {
                    view.create_device(size_mb, &algo, priority);
                }
            }
        });

        dialog.present();
    }

    fn create_device(&self, size_mb: u64, algo: &str, priority: i32) {
        let weak_self = self.downgrade();
        self.set_busy(true);
        let algo_owned = algo.to_string();

        glib::spawn_future_local(async move {
            // Build config list including the new device
            let current = zram::read_zram_devices();
            let mut configs: Vec<(u64, String, i32)> = current
                .iter()
                .map(|d| {
                    (
                        d.size_bytes / (1024 * 1024),
                        d.comp_algorithm.clone(),
                        d.swap_priority,
                    )
                })
                .collect();
            configs.push((size_mb, algo_owned, priority));

            // Write config + restart vendor service
            let result = persist::persist_zram(&configs);

            if let Some(view) = weak_self.upgrade() {
                view.set_busy(false);
                view.refresh_all();
                match &result {
                    Ok(_) => {
                        let win = view.root().and_then(|r| r.downcast::<gtk::Window>().ok());
                        if let Some(parent) = win {
                            utils::show_info(
                                &parent,
                                &i18n("Device Created"),
                                &i18n(
                                    "Zram configuration saved. The device will be created shortly.",
                                ),
                            );
                        }
                    }
                    Err(e) => view.show_error(&e),
                }
            }
        });
    }

    fn destroy_device(&self, dev_path: String) {
        let weak_self = self.downgrade();
        self.set_busy(true);

        glib::spawn_future_local(async move {
            // Build config list excluding the destroyed device
            let current = zram::read_zram_devices();
            let configs: Vec<(u64, String, i32)> = current
                .iter()
                .filter(|d| format!("/dev/{}", d.name) != dev_path)
                .map(|d| {
                    (
                        d.size_bytes / (1024 * 1024),
                        d.comp_algorithm.clone(),
                        d.swap_priority,
                    )
                })
                .collect();

            let result = persist::persist_zram(&configs);

            if let Some(view) = weak_self.upgrade() {
                view.set_busy(false);
                view.refresh_all();
                if let Err(e) = result {
                    view.show_error(&e);
                }
            }
        });
    }

    fn refresh_all(&self) {
        self.refresh_data();
        if let Some(root) = self.root() {
            if let Ok(win) = root.downcast::<crate::window::SwapcontrolWindow>() {
                win.refresh_views();
            }
        }
    }

    pub fn refresh_data(&self) {
        let imp = self.imp();
        let devices = zram::read_zram_devices();

        let empty = devices.is_empty();

        // Toggle empty state vs device list
        if let Some(stack) = imp.stack.borrow().as_ref() {
            stack.set_visible_child_name(if empty { "empty" } else { "devices" });
        }

        // Refresh the device list
        if let Some(list) = imp.device_list.borrow().as_ref() {
            // Remove all existing rows
            while let Some(row) = list.first_child() {
                list.remove(&row);
            }

            if !empty {
                for dev in &devices {
                    let size_mb = dev.size_bytes as f64 / (1024.0 * 1024.0);
                    let used_mb = dev.used_bytes as f64 / (1024.0 * 1024.0);
                    let orig_mb = dev.orig_data_size as f64 / (1024.0 * 1024.0);
                    let compr_mb = dev.compr_data_size as f64 / (1024.0 * 1024.0);
                    let stats_text = if used_mb > 1.0 {
                        if orig_mb > 1.0 {
                            let saved = (1.0 - compr_mb / orig_mb) * 100.0;
                            format!("{:.0} MiB used · saved {:.0}%", used_mb, saved)
                        } else {
                            format!("{:.0} MiB used (stats reset)", used_mb)
                        }
                    } else {
                        format!("Idle — {:.0} MiB available", size_mb)
                    };

                    let frac = if dev.size_bytes > 0 {
                        dev.used_bytes as f64 / dev.size_bytes as f64
                    } else {
                        0.0
                    };
                    let used_gb = dev.used_bytes as f64 / (1024.0 * 1024.0 * 1024.0);
                    let total_gb = dev.size_bytes as f64 / (1024.0 * 1024.0 * 1024.0);
                    let bar = UsageBar::new("", (1.0, 0.47, 0.0));
                    bar.set_fraction(frac, &format!("{:.2} / {:.1} GiB", used_gb, total_gb));

                    let row = adw::ActionRow::builder()
                        .title(&format!("{} — {}", dev.name, dev.comp_algorithm))
                        .subtitle(&stats_text)
                        .build();
                    row.add_suffix(&bar);

                    let dev_path = format!("/dev/{}", dev.name);
                    let destroy_btn = gtk::Button::builder()
                        .label(&i18n("Remove"))
                        .css_classes(["destructive-action"])
                        .valign(gtk::Align::Center)
                        .build();
                    row.add_suffix(&destroy_btn);

                    let path_clone = dev_path.clone();
                    let path_clone2 = dev_path.clone();
                    let weak_self = self.downgrade();
                    destroy_btn.connect_clicked(move |_| {
                        if let Some(view) = weak_self.upgrade() {
                            let win = view.root().and_then(|r| r.downcast::<gtk::Window>().ok());
                            if let Some(parent) = win {
                                let path = path_clone2.clone();
                                let weak_self2 = view.downgrade();
                                utils::show_confirm(
                                    &parent,
                                    &i18n("Destroy Zram Device"),
                                    &format!(
                                        "This will immediately remove {} and flush its contents to disk swap. Continue?",
                                        path_clone
                                    ),
                                    move || {
                                        if let Some(v) = weak_self2.upgrade() {
                                            v.destroy_device(path.clone());
                                        }
                                    },
                                );
                            }
                        }
                    });

                    list.append(&row);
                }
            }
        }
    }
}
