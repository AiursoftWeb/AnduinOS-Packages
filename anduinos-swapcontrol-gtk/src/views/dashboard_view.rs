use adw::subclass::prelude::*;
use gtk::glib;
use gtk::prelude::*;
use std::cell::RefCell;

use crate::i18n::{i18n, i18n_fmt};
use crate::swap::{hibernation, ram_info, swapfile, sysctl, zram, zswap};
use crate::widgets::memory_ring::{MemoryRing, Segment};
use crate::widgets::usage_bar::UsageBar;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct DashboardView {
        pub ring: RefCell<Option<MemoryRing>>,
        pub legend: RefCell<Option<gtk::Box>>,
        pub zram_bar: RefCell<Option<UsageBar>>,
        pub zswap_bar: RefCell<Option<UsageBar>>,
        pub swap_bar: RefCell<Option<UsageBar>>,
        pub last_dmidecode: RefCell<Option<std::time::Instant>>,
        pub dmidecode_pending: RefCell<bool>,
        pub ram_total: RefCell<Option<gtk::Label>>,
        pub ram_type: RefCell<Option<gtk::Label>>,
        pub ram_speed: RefCell<Option<gtk::Label>>,
        pub ram_dimm: RefCell<Option<gtk::Label>>,
        pub swap_sub: RefCell<Option<gtk::Label>>,
        pub zswap_sub: RefCell<Option<gtk::Label>>,
        pub zram_sub: RefCell<Option<gtk::Label>>,
        pub hiber_sub: RefCell<Option<gtk::Label>>,
        pub swappiness_sub: RefCell<Option<gtk::Label>>,
        pub recommendation_box: RefCell<Option<gtk::Box>>,
        pub refresh_timer: RefCell<Option<glib::SourceId>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for DashboardView {
        const NAME: &'static str = "DashboardView";
        type Type = super::DashboardView;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for DashboardView {
        fn constructed(&self) {
            self.parent_constructed();
            self.obj().setup_ui();
            self.obj().start_auto_refresh();
        }
        fn dispose(&self) {
            self.obj().stop_auto_refresh();
        }
    }
    impl WidgetImpl for DashboardView {}
    impl BoxImpl for DashboardView {}
}

glib::wrapper! {
    pub struct DashboardView(ObjectSubclass<imp::DashboardView>)
        @extends gtk::Widget, gtk::Box,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

#[derive(Default)]
struct MemInfo {
    total: u64,
    used: u64,
    buffers: u64,
    cached: u64,
    free: u64,
}

fn read_meminfo() -> MemInfo {
    let mut info = MemInfo::default();
    let Ok(content) = std::fs::read_to_string("/proc/meminfo") else {
        return info;
    };
    for line in content.lines() {
        let p: Vec<&str> = line.split_whitespace().collect();
        if p.len() < 2 {
            continue;
        }
        let kb: u64 = p[1].parse().unwrap_or(0) * 1024;
        match p[0].trim_end_matches(':') {
            "MemTotal" => info.total = kb,
            "MemFree" => info.free = kb,
            "Buffers" => info.buffers = kb,
            "Cached" => info.cached = kb,
            _ => {}
        }
    }
    info.used = info
        .total
        .saturating_sub(info.free + info.buffers + info.cached);
    info
}

impl DashboardView {
    pub fn new() -> Self {
        glib::Object::builder().build()
    }

    fn start_auto_refresh(&self) {
        let weak = self.downgrade();
        let id = glib::timeout_add_local(std::time::Duration::from_millis(500), move || {
            if let Some(view) = weak.upgrade() {
                view.refresh_data();
                glib::ControlFlow::Continue
            } else {
                glib::ControlFlow::Break
            }
        });
        *self.imp().refresh_timer.borrow_mut() = Some(id);
    }

    fn stop_auto_refresh(&self) {
        if let Some(id) = self.imp().refresh_timer.borrow_mut().take() {
            id.remove();
        }
    }

    fn setup_ui(&self) {
        let imp = self.imp();
        self.set_orientation(gtk::Orientation::Vertical);
        self.set_spacing(18);
        self.set_margin_start(24);
        self.set_margin_end(24);
        self.set_margin_top(24);
        self.set_margin_bottom(24);
        self.set_vexpand(true);

        // Title
        self.append(
            &gtk::Label::builder()
                .label(&i18n("Memory Overview"))
                .css_classes(["title-1"])
                .halign(gtk::Align::Start)
                .build(),
        );
        self.append(
            &gtk::Label::builder()
                .label(&i18n(
                    "Real-time RAM, swap, and compression subsystem status",
                ))
                .css_classes(["caption"])
                .halign(gtk::Align::Start)
                .margin_start(2)
                .build(),
        );
        // Swappiness recommendation
        {
            let sw = crate::swap::sysctl::recommended_swappiness();
            let has_zram = !crate::swap::zram::read_zram_devices().is_empty();
            let reason = if has_zram {
                i18n("with Zram active — prefer fast compressed RAM over disk cache")
            } else {
                let total_ram =
                    crate::swap::sysctl::read_total_ram().unwrap_or(32 * 1024 * 1024 * 1024);
                let ram_gb = total_ram as f64 / (1024.0 * 1024.0 * 1024.0);
                i18n_fmt("for {0} GiB RAM", &[&format!("{:.0}", ram_gb)])
            };
            self.append(
                &gtk::Label::builder()
                    .use_markup(true)
                    .label(&i18n_fmt(
                        "<i>Recommended swappiness: {0} ({1})</i>",
                        &[&sw.to_string(), &reason],
                    ))
                    .css_classes(["caption"])
                    .halign(gtk::Align::Start)
                    .margin_start(2)
                    .build(),
            );
        }

        // ─── Recommendations (context-aware tips) ─────────────────────
        let rec_box = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(6)
            .valign(gtk::Align::Start)
            .build();
        self.append(&rec_box);
        *imp.recommendation_box.borrow_mut() = Some(rec_box);

        // ─── RAM spec bar ────────────────────────────────────────────
        let spec = gtk::Grid::builder()
            .row_spacing(8)
            .column_spacing(8)
            .column_homogeneous(true)
            .build();
        let (c0, l0) = mini_stat(&i18n("Total RAM"), "...");
        let (c1, l1) = mini_stat(&i18n("Type"), "...");
        let (c2, l2) = mini_stat(&i18n("Speed"), "...");
        let (c3, l3) = mini_stat(&i18n("Channels"), "...");
        spec.attach(&c0, 0, 0, 1, 1);
        spec.attach(&c1, 1, 0, 1, 1);
        spec.attach(&c2, 2, 0, 1, 1);
        spec.attach(&c3, 3, 0, 1, 1);
        self.append(&spec);
        *imp.ram_total.borrow_mut() = Some(l0);
        *imp.ram_type.borrow_mut() = Some(l1);
        *imp.ram_speed.borrow_mut() = Some(l2);
        *imp.ram_dimm.borrow_mut() = Some(l3);

        // ─── Ring + Bars row ─────────────────────────────────────────
        let middle = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(24)
            .build();

        // Left side: ring + legend
        let ring_col = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(8)
            .halign(gtk::Align::Center)
            .valign(gtk::Align::Start)
            .build();

        let ring = MemoryRing::new();
        ring.set_halign(gtk::Align::Center);
        ring.set_valign(gtk::Align::Start);
        ring_col.append(&ring);
        *imp.ring.borrow_mut() = Some(ring);

        let legend = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(14)
            .halign(gtk::Align::Center)
            .build();
        ring_col.append(&legend);
        *imp.legend.borrow_mut() = Some(legend);

        middle.append(&ring_col);

        // Right: 3 usage bars (align top to match ring)
        let bars = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(16)
            .vexpand(true)
            .valign(gtk::Align::Start)
            .hexpand(true)
            .margin_top(10)
            .build();

        let zram_bar = UsageBar::new(&i18n("Zram"), (1.0, 0.47, 0.0));
        bars.append(&zram_bar);
        *imp.zram_bar.borrow_mut() = Some(zram_bar);

        let zswap_bar = UsageBar::new(&i18n("Zswap"), (0.20, 0.82, 0.48));
        bars.append(&zswap_bar);
        *imp.zswap_bar.borrow_mut() = Some(zswap_bar);

        let swap_bar = UsageBar::new(&i18n("Swap"), (0.21, 0.52, 0.89));
        bars.append(&swap_bar);
        *imp.swap_bar.borrow_mut() = Some(swap_bar);

        middle.append(&bars);
        self.append(&middle);

        // ─── Bottom status cards ────────────────────────────────────
        let grid = gtk::Grid::builder()
            .row_spacing(8)
            .column_spacing(8)
            .column_homogeneous(true)
            .build();

        let (c1, s1) = info_card("drive-harddisk-symbolic", &i18n("Swap"), "");
        let (c2, s2) = info_card("emblem-synchronizing-symbolic", &i18n("Zswap"), "");
        let (c3, s3) = info_card("media-flash-symbolic", &i18n("Zram"), "");
        let (c4, s4) = info_card("weather-clear-night-symbolic", &i18n("Hibernation"), "");
        let (c5, s5) = info_card("preferences-system-symbolic", &i18n("Swappiness"), "");

        grid.attach(&c1, 0, 0, 1, 1);
        grid.attach(&c2, 1, 0, 1, 1);
        grid.attach(&c3, 2, 0, 1, 1);
        grid.attach(&c4, 3, 0, 1, 1);
        grid.attach(&c5, 4, 0, 1, 1);
        self.append(&grid);

        *imp.swap_sub.borrow_mut() = Some(s1);
        *imp.zswap_sub.borrow_mut() = Some(s2);
        *imp.zram_sub.borrow_mut() = Some(s3);
        *imp.hiber_sub.borrow_mut() = Some(s4);
        *imp.swappiness_sub.borrow_mut() = Some(s5);
    }

    pub fn refresh_data(&self) {
        let imp = self.imp();

        // ─── RAM hardware ────────────────────────────────────────────
        let ram = ram_info::read_ram_basic(); // non-blocking, no pkexec

        let has_dmidecode_data = imp
            .ram_type
            .borrow()
            .as_ref()
            .map(|l| {
                l.label().as_str() != "-"
                    && l.label().as_str() != "..."
                    && !l.label().as_str().contains("Auth")
            })
            .unwrap_or(false);

        // Try dmidecode: only if we don't have data yet, no call is in flight,
        // and cooldown has elapsed since the last attempt finished.
        if !has_dmidecode_data && !*imp.dmidecode_pending.borrow() {
            let now = std::time::Instant::now();
            let cooldown_ok = imp
                .last_dmidecode
                .borrow()
                .map(|t| now.duration_since(t) > std::time::Duration::from_secs(30))
                .unwrap_or(true);
            if cooldown_ok {
                *imp.dmidecode_pending.borrow_mut() = true;
                let (tx, rx) = async_channel::bounded(1);
                tokio::spawn(async move {
                    let full = ram_info::read_ram_info_full();
                    let _ = tx.send(full).await;
                });
                let weak = self.downgrade();
                glib::spawn_future_local(async move {
                    let result = rx.recv().await;
                    if let Some(view) = weak.upgrade() {
                        let imp = view.imp();
                        *imp.dmidecode_pending.borrow_mut() = false;
                        *imp.last_dmidecode.borrow_mut() = Some(std::time::Instant::now());
                        if let Ok(full) = result {
                            if !full.ram_type.is_empty() {
                                if let Some(l) = imp.ram_type.borrow().as_ref() {
                                    l.set_text(&full.ram_type);
                                }
                            }
                            if full.speed_mts > 0 {
                                let s = format!("{} MT/s", full.speed_mts);
                                if let Some(l) = imp.ram_speed.borrow().as_ref() {
                                    l.set_text(&s);
                                }
                            }
                            let pop: Vec<_> = full.dimms.iter().filter(|d| d.size_gb > 0).collect();
                            if !pop.is_empty() {
                                let d = format!("{}×{}GB", pop.len(), pop[0].size_gb);
                                if let Some(l) = imp.ram_dimm.borrow().as_ref() {
                                    l.set_text(&d);
                                }
                            }
                        }
                    }
                });
            }
        }
        // Total RAM always updates from /proc/meminfo
        let ram_gb_str = format!("{:.0} GiB", ram.total_gb);
        if let Some(l) = imp.ram_total.borrow().as_ref() {
            l.set_text(&ram_gb_str);
        }
        // Type/Speed/Dimm: show fallback until dmidecode delivers real data
        if !has_dmidecode_data {
            if let Some(l) = imp.ram_type.borrow().as_ref() {
                l.set_text(if ram.ram_type.is_empty() {
                    "-"
                } else {
                    &ram.ram_type
                });
            }
            let speed_str = format!("{} MT/s", ram.speed_mts);
            if let Some(l) = imp.ram_speed.borrow().as_ref() {
                l.set_text(if ram.speed_mts > 0 { &speed_str } else { "-" });
            }
            if let Some(l) = imp.ram_dimm.borrow().as_ref() {
                let pop: Vec<_> = ram.dimms.iter().filter(|d| d.size_gb > 0).collect();
                let auth_str = i18n("Auth needed");
                let dimm_str = if !pop.is_empty() {
                    format!("{}×{}GB", pop.len(), pop[0].size_gb)
                } else {
                    String::new()
                };
                l.set_text(if pop.is_empty() { &auth_str } else { &dimm_str });
            }
        }

        // ─── Ring chart ─────────────────────────────────────────────
        let mem = read_meminfo();
        if let Some(ring) = imp.ring.borrow().as_ref() {
            ring.set_segments(vec![
                Segment {
                    label: i18n("Used"),
                    value: mem.used as f64,
                    color: (0.89, 0.20, 0.20),
                },
                Segment {
                    label: i18n("Buffers"),
                    value: mem.buffers as f64,
                    color: (0.20, 0.55, 0.91),
                },
                Segment {
                    label: i18n("Cached"),
                    value: mem.cached as f64,
                    color: (0.20, 0.82, 0.48),
                },
                Segment {
                    label: i18n("Free"),
                    value: mem.free as f64,
                    color: (0.60, 0.60, 0.60),
                },
            ]);
        }
        // Legend
        if let Some(legend) = imp.legend.borrow().as_ref() {
            while let Some(c) = legend.first_child() {
                legend.remove(&c);
            }
            let items: [(&str, (f64, f64, f64), u64); 4] = [
                (&i18n("Used"), (0.89, 0.20, 0.20), mem.used),
                (&i18n("Buf"), (0.20, 0.55, 0.91), mem.buffers),
                (&i18n("Cache"), (0.20, 0.82, 0.48), mem.cached),
                (&i18n("Free"), (0.60, 0.60, 0.60), mem.free),
            ];
            for (name, (r, g, b), val) in &items {
                let item = gtk::Box::builder()
                    .orientation(gtk::Orientation::Horizontal)
                    .spacing(3)
                    .build();
                let dot = gtk::DrawingArea::builder()
                    .content_width(8)
                    .content_height(8)
                    .halign(gtk::Align::Center)
                    .valign(gtk::Align::Center)
                    .build();
                let (dr, dg, db) = (*r, *g, *b);
                dot.set_draw_func(move |_, ctx, w, h| {
                    ctx.set_source_rgb(dr, dg, db);
                    ctx.arc(
                        w as f64 / 2.,
                        h as f64 / 2.,
                        3.5,
                        0.,
                        2. * std::f64::consts::PI,
                    );
                    ctx.fill().ok();
                });
                item.append(&dot);
                let gb = *val as f64 / (1024. * 1024. * 1024.);
                item.append(
                    &gtk::Label::builder()
                        .label(&format!("{} {:.1}G", name, gb))
                        .css_classes(["caption"])
                        .build(),
                );
                legend.append(&item);
            }
        }

        // ─── Zram bar ────────────────────────────────────────────────
        let devs = zram::read_zram_devices();
        if let Some(bar) = imp.zram_bar.borrow().as_ref() {
            if devs.is_empty() {
                bar.set_fraction(0.0, &i18n("No device"));
            } else {
                let d = &devs[0];
                let total_gb = d.size_bytes as f64 / (1024.0 * 1024.0 * 1024.0);
                let used_gb = d.used_bytes as f64 / (1024.0 * 1024.0 * 1024.0);
                // Use /proc/swaps usage (survives zram reset), mm_stat for compression ratio
                if used_gb > 0.001 {
                    let frac = d.used_bytes as f64 / d.size_bytes as f64;
                    let saved = if d.orig_data_size > 1024 * 1024 {
                        (1.0 - d.compr_data_size as f64 / d.orig_data_size as f64) * 100.0
                    } else {
                        0.0
                    };
                    let bar_str = format!("{:.2} / {:.1} GiB", used_gb, total_gb);
                    if saved > 0.0 {
                        bar.set_fraction(
                            frac,
                            &i18n_fmt("{0} · saved {1}%", &[&bar_str, &format!("{:.0}", saved)]),
                        );
                    } else {
                        bar.set_fraction(frac, &bar_str);
                    }
                } else {
                    let bar_str =
                        i18n_fmt("Idle ({0} GiB available)", &[&format!("{:.1}", total_gb)]);
                    bar.set_fraction(0.0, &bar_str);
                }
            }
        }

        // ─── Zswap bar ───────────────────────────────────────────────
        if let Ok(cfg) = zswap::read_zswap_config() {
            if let Some(bar) = imp.zswap_bar.borrow().as_ref() {
                if cfg.enabled {
                    bar.set_fraction(
                        cfg.max_pool_percent as f64 / 100.0,
                        &i18n_fmt(
                            "{0} · pool {1}%",
                            &[&cfg.compressor, &cfg.max_pool_percent.to_string()],
                        ),
                    );
                } else {
                    bar.set_fraction(0.0, &i18n("Disabled"));
                }
            }
        }

        // ─── Swap bar ────────────────────────────────────────────────
        if let Ok(status) = swapfile::read_swap_status() {
            if let Some(bar) = imp.swap_bar.borrow().as_ref() {
                if status.active && status.size_bytes > 0 {
                    let frac = status.used_bytes as f64 / status.size_bytes as f64;
                    let used_gb = status.used_bytes as f64 / (1024.0 * 1024.0 * 1024.0);
                    let total_gb = status.size_bytes as f64 / (1024.0 * 1024.0 * 1024.0);
                    bar.set_fraction(frac, &format!("{:.1} / {:.1} GiB", used_gb, total_gb));
                } else {
                    bar.set_fraction(0.0, &i18n("Inactive"));
                }
            }
        }

        // ─── Bottom cards ────────────────────────────────────────────
        if let Ok(status) = swapfile::read_swap_status() {
            let txt = if status.active {
                format!(
                    "{:.1} GiB",
                    status.size_bytes as f64 / (1024.0 * 1024.0 * 1024.0)
                )
            } else {
                i18n("Off")
            };
            if let Some(l) = imp.swap_sub.borrow().as_ref() {
                l.set_text(&txt);
            }
        }
        if let Ok(cfg) = zswap::read_zswap_config() {
            let a = i18n("On");
            let d = i18n("Off");
            if let Some(l) = imp.zswap_sub.borrow().as_ref() {
                l.set_text(if cfg.enabled { &a } else { &d });
            }
        }
        let devs = zram::read_zram_devices();
        if let Some(l) = imp.zram_sub.borrow().as_ref() {
            let dev_str = format!("{} dev", devs.len());
            let none_str = i18n("None");
            l.set_text(if devs.is_empty() { &none_str } else { &dev_str });
        }
        let h = hibernation::check_hibernation();
        if let Some(l) = imp.hiber_sub.borrow().as_ref() {
            let state = if h.ready {
                i18n("Ready")
            } else if !h.system_supports || h.disk_modes.is_empty() {
                i18n("Unsupported")
            } else if h.configured_target.is_none() {
                i18n("Not configured")
            } else if h.resolved_target.is_none() {
                i18n("Resume target missing")
            } else if !h.target_active {
                i18n("Resume target inactive")
            } else if h.target_size_bytes < h.required_size_bytes {
                i18n("Swap too small")
            } else {
                i18n("Not ready")
            };
            l.set_text(&state);
        }
        if let Ok(sw) = sysctl::read_swappiness() {
            let sw_str = format!("{}", sw);
            if let Some(l) = imp.swappiness_sub.borrow().as_ref() {
                l.set_text(&sw_str);
            }
        }

        // ─── Recommendations (at most one, highest priority first) ─────
        // Zram and Zswap are mutually exclusive — enabling both is an anti-pattern.
        if let Some(rec_box) = imp.recommendation_box.borrow().as_ref() {
            while let Some(c) = rec_box.first_child() {
                rec_box.remove(&c);
            }

            let swap_active = swapfile::is_swap_active();
            let has_zram = !zram::read_zram_devices().is_empty();
            let zswap_on = zswap::read_zswap_config()
                .map(|c| c.enabled)
                .unwrap_or(false);

            let swappiness = sysctl::read_swappiness().unwrap_or(10);

            if !swap_active {
                let card = build_rec_card((0.93, 0.55, 0.0),
                    &i18n("No disk swap detected"),
                    &i18n("When RAM is full the kernel may kill applications. Enable disk swap for a reliable safety net."));
                rec_box.append(&card);
            } else if has_zram && zswap_on {
                let card = build_rec_card((0.93, 0.20, 0.20),
                    &i18n("Zram and Zswap are both active"),
                    &i18n("Zram and Zswap are mutually exclusive — each creates its own compressed RAM pool. Running both wastes CPU and can cause severe memory thrashing. Disable one of them."));
                rec_box.append(&card);
            } else if has_zram && swappiness != 100 {
                let rec = sysctl::recommended_swappiness();
                let card = build_rec_card((0.93, 0.73, 0.0),
                    &i18n("Swappiness not optimized for Zram"),
                    &i18n_fmt("Zram is active but swappiness is {0} instead of {1}. Set it to {1} to prefer fast compressed RAM swap — dropping file cache when zram is available wastes I/O performance.", &[&swappiness.to_string(), &rec.to_string()]));
                rec_box.append(&card);
            } else if !has_zram && !zswap_on {
                let card = build_rec_card((0.93, 0.55, 0.0),
                    &i18n("No memory acceleration enabled"),
                    &i18n("Enable Zram for snappy compressed swap in RAM — it's 10× faster than disk swap and dramatically reduces I/O under memory pressure."));
                rec_box.append(&card);
            } else {
                let card = build_rec_card((0.15, 0.72, 0.25),
                    &i18n("Optimal memory configuration"),
                    &i18n("Disk swap is active with memory compression — your system is well-protected against memory pressure."));
                rec_box.append(&card);
            }
        }
    }
}

fn build_rec_card(accent: (f64, f64, f64), title: &str, subtitle: &str) -> gtk::Box {
    let card = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .css_classes(["card"])
        .spacing(12)
        .valign(gtk::Align::Start)
        .build();
    let bar = gtk::DrawingArea::builder()
        .content_width(4)
        .vexpand(true)
        .halign(gtk::Align::Fill)
        .valign(gtk::Align::Fill)
        .build();
    let (r, g, b) = accent;
    bar.set_draw_func(move |_, ctx, w, h| {
        ctx.set_source_rgb(r, g, b);
        ctx.rectangle(0.0, 0.0, w as f64, h as f64);
        ctx.fill().ok();
    });
    card.append(&bar);
    let inner = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(2)
        .hexpand(true)
        .margin_start(10)
        .margin_end(14)
        .margin_top(8)
        .margin_bottom(8)
        .build();
    inner.append(
        &gtk::Label::builder()
            .label(title)
            .css_classes(["heading"])
            .halign(gtk::Align::Start)
            .build(),
    );
    inner.append(
        &gtk::Label::builder()
            .label(subtitle)
            .css_classes(["caption"])
            .wrap(true)
            .halign(gtk::Align::Start)
            .build(),
    );
    card.append(&inner);
    card
}

fn info_card(icon: &str, title: &str, subtitle: &str) -> (gtk::Box, gtk::Label) {
    let card = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .css_classes(["card"])
        .build();
    let inner = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(8)
        .margin_start(10)
        .margin_end(10)
        .margin_top(8)
        .margin_bottom(8)
        .build();
    inner.append(&gtk::Image::builder().icon_name(icon).pixel_size(20).build());
    let tb = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(1)
        .hexpand(true)
        .build();
    tb.append(
        &gtk::Label::builder()
            .label(title)
            .css_classes(["caption"])
            .halign(gtk::Align::Start)
            .build(),
    );
    let sub = gtk::Label::builder()
        .label(subtitle)
        .css_classes(["title-4"])
        .halign(gtk::Align::Start)
        .build();
    tb.append(&sub);
    inner.append(&tb);
    card.append(&inner);
    (card, sub)
}

fn mini_stat(label: &str, value: &str) -> (gtk::Box, gtk::Label) {
    let card = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(2)
        .css_classes(["card"])
        .build();
    let inner = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(2)
        .margin_start(8)
        .margin_end(8)
        .margin_top(8)
        .margin_bottom(8)
        .build();
    let val = gtk::Label::builder()
        .label(value)
        .css_classes(["title-3"])
        .halign(gtk::Align::Center)
        .build();
    let cap = gtk::Label::builder()
        .label(label)
        .css_classes(["caption"])
        .halign(gtk::Align::Center)
        .build();
    inner.append(&val);
    inner.append(&cap);
    card.append(&inner);
    (card, val)
}
