use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;
use gtk::gio;
use std::cell::RefCell;

use crate::i18n::i18n;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct NetworkAuditView {
        pub data_model: RefCell<Option<gio::ListStore>>,
        pub list_box: RefCell<Option<gtk::ListBox>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for NetworkAuditView {
        const NAME: &'static str = "NetworkAuditView";
        type Type = super::NetworkAuditView;
        type ParentType = adw::Bin;
    }

    impl ObjectImpl for NetworkAuditView {
        fn constructed(&self) {
            self.parent_constructed();
            let obj = self.obj();
            obj.setup_ui();
        }
    }
    
    impl WidgetImpl for NetworkAuditView {}
    impl BinImpl for NetworkAuditView {}
}

glib::wrapper! {
    pub struct NetworkAuditView(ObjectSubclass<imp::NetworkAuditView>)
        @extends gtk::Widget, adw::Bin,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

impl NetworkAuditView {
    pub fn new() -> Self {
        glib::Object::builder().build()
    }

    fn setup_ui(&self) {
        let imp = self.imp();

        let main_box = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(0)
            .build();

        // 1. Banner
        let banner = adw::Banner::builder()
            .title(&i18n("Monitoring Network Activity"))
            .revealed(true)
            .build();
        main_box.append(&banner);

        // 2. Graph Placeholder
        let graph_box = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .vexpand(false)
            .margin_top(16)
            .margin_bottom(16)
            .margin_start(16)
            .margin_end(16)
            .height_request(150)
            .css_classes(["card"])
            .build();
        
        let history: std::rc::Rc<RefCell<Vec<(u64, u64)>>> = std::rc::Rc::new(RefCell::new(Vec::new()));
        
        let drawing_area = gtk::DrawingArea::builder()
            .hexpand(true)
            .vexpand(true)
            .margin_top(10)
            .margin_bottom(10)
            .margin_start(10)
            .margin_end(10)
            .tooltip_text(&i18n(
                "Network Traffic\nRed line: Upload speed\nGreen line: Download speed",
            ))
            .build();
            
        let draw_history = history.clone();
        drawing_area.set_draw_func(move |_, cr, width, height| {
            let data = draw_history.borrow();
            let width = width as f64;
            let height = height as f64;
            
            if data.is_empty() { return; }
            
            let mut max_speed = 1024.0 * 10.0;
            for &(up, down) in data.iter() {
                let up = up as f64;
                let down = down as f64;
                if up > max_speed { max_speed = up; }
                if down > max_speed { max_speed = down; }
            }
            
            let max_points = 60_usize;
            let step_x = width / (max_points as f64 - 1.0);
            let scale_y = height / max_speed;
            
            let start_idx = if data.len() > max_points { data.len() - max_points } else { 0 };
            let display_data = &data[start_idx..];
            
            // Draw Upload (Red)
            cr.set_source_rgba(0.9, 0.3, 0.3, 1.0);
            cr.set_line_width(2.0);
            for (i, &(up, _)) in display_data.iter().enumerate() {
                let x = width - ((display_data.len() - 1 - i) as f64 * step_x);
                let y = height - (up as f64 * scale_y);
                if i == 0 { cr.move_to(x, y); } else { cr.line_to(x, y); }
            }
            let _ = cr.stroke();
            
            // Draw Download (Green)
            cr.set_source_rgba(0.2, 0.8, 0.4, 1.0);
            for (i, &(_, down)) in display_data.iter().enumerate() {
                let x = width - ((display_data.len() - 1 - i) as f64 * step_x);
                let y = height - (down as f64 * scale_y);
                if i == 0 { cr.move_to(x, y); } else { cr.line_to(x, y); }
            }
            let _ = cr.stroke();
            
            // Draw Legend and current speeds
            let current_up = display_data.last().map(|(u, _)| *u as f64).unwrap_or(0.0);
            let current_down = display_data.last().map(|(_, d)| *d as f64).unwrap_or(0.0);
            
            let format_speed = |speed: f64| -> String {
                if speed >= 1024.0 * 1024.0 {
                    format!("{:.1} MB/s", speed / (1024.0 * 1024.0))
                } else if speed >= 1024.0 {
                    format!("{:.1} KB/s", speed / 1024.0)
                } else {
                    format!("{} B/s", speed as u64)
                }
            };
            
            cr.select_font_face("Sans", gtk::cairo::FontSlant::Normal, gtk::cairo::FontWeight::Normal);
            cr.set_font_size(11.0);
            
            cr.move_to(10.0, 15.0);
            cr.set_source_rgba(0.9, 0.3, 0.3, 1.0);
            let _ = cr.show_text(&format!(
                "— {}: {}",
                i18n("Upload"),
                format_speed(current_up)
            ));
            
            cr.move_to(10.0, 32.0);
            cr.set_source_rgba(0.2, 0.8, 0.4, 1.0);
            let _ = cr.show_text(&format!(
                "— {}: {}",
                i18n("Download"),
                format_speed(current_down)
            ));
            
            // Y-axis max label
            cr.set_source_rgba(0.6, 0.6, 0.6, 0.8);
            let max_str = format!("{}: {}", i18n("Max"), format_speed(max_speed));
            let extents = cr.text_extents(&max_str).unwrap();
            cr.move_to(width - extents.width() - 10.0, 15.0);
            let _ = cr.show_text(&max_str);
        });
        
        graph_box.append(&drawing_area);
        main_box.append(&graph_box);

        // 3. Search and Filter Bar
        let filter_bar = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(12)
            .margin_start(16)
            .margin_end(16)
            .margin_bottom(12)
            .build();

        let search_entry = gtk::SearchEntry::builder()
            .hexpand(true)
            .placeholder_text(&i18n("Search Process or IP..."))
            .build();
        
        let resolve_dns_switch = gtk::Switch::builder()
            .valign(gtk::Align::Center)
            .active(true)
            .build();
        
        let switch_label = gtk::Label::builder()
            .label(&i18n("Resolve Domains"))
            .valign(gtk::Align::Center)
            .build();

        let direction_box = gtk::Box::builder().css_classes(["linked"]).valign(gtk::Align::Center).build();
        let btn_all = gtk::ToggleButton::builder().label(&i18n("All")).active(true).build();
        let btn_in = gtk::ToggleButton::builder().label(&i18n("Inbound")).group(&btn_all).build();
        let btn_out = gtk::ToggleButton::builder().label(&i18n("Outbound")).group(&btn_all).build();
        direction_box.append(&btn_all);
        direction_box.append(&btn_in);
        direction_box.append(&btn_out);

        let proto_box = gtk::Box::builder().css_classes(["linked"]).valign(gtk::Align::Center).build();
        let proto_all = gtk::ToggleButton::builder().label(&i18n("All")).active(true).build();
        let proto_tcp = gtk::ToggleButton::builder().label(&i18n("TCP")).group(&proto_all).build();
        let proto_udp = gtk::ToggleButton::builder().label(&i18n("UDP")).group(&proto_all).build();
        proto_box.append(&proto_all);
        proto_box.append(&proto_tcp);
        proto_box.append(&proto_udp);

        let pause_btn = gtk::ToggleButton::builder()
            .icon_name("media-playback-pause-symbolic")
            .tooltip_text(&i18n("Pause list updates"))
            .valign(gtk::Align::Center)
            .build();

        filter_bar.append(&search_entry);
        filter_bar.append(&direction_box);
        filter_bar.append(&proto_box);
        filter_bar.append(&pause_btn);
        filter_bar.append(&switch_label);
        filter_bar.append(&resolve_dns_switch);
        
        main_box.append(&filter_bar);

        // 4. Data Table (Placeholder)
        let list_store = gio::ListStore::new::<glib::Object>();
        *imp.data_model.borrow_mut() = Some(list_store);

        let list_box = gtk::ListBox::builder()
            .css_classes(["boxed-list"])
            .margin_start(16)
            .margin_end(16)
            .margin_bottom(16)
            .selection_mode(gtk::SelectionMode::None)
            .build();
        *imp.list_box.borrow_mut() = Some(list_box.clone());

        let scrolled_window = gtk::ScrolledWindow::builder()
            .hscrollbar_policy(gtk::PolicyType::Never)
            .vscrollbar_policy(gtk::PolicyType::Automatic)
            .overlay_scrolling(false)
            .vexpand(true)
            .child(&list_box)
            .build();
        
        main_box.append(&scrolled_window);
        self.set_child(Some(&main_box));

        // Start real-time monitoring
        let (sender, receiver) = async_channel::unbounded();
        crate::ufw::traffic_monitor::TrafficMonitor::start_monitoring(sender);

        let list_box_clone = list_box.clone();
        let resolve_dns_switch_clone = resolve_dns_switch.clone();
        let btn_in_clone = btn_in.clone();
        let btn_out_clone = btn_out.clone();
        let proto_tcp_clone = proto_tcp.clone();
        let proto_udp_clone = proto_udp.clone();
        let drawing_area_clone = drawing_area.clone();
        let search_entry_clone = search_entry.clone();
        let pause_btn_clone = pause_btn.clone();
        
        let mut last_filter_state: Option<(bool, bool, bool, bool, String)> = None;
        let mut last_stats = Vec::new();

        glib::spawn_future_local(async move {
            while let Ok(msg) = receiver.recv().await {
                let stats = match msg {
                    Ok(s) => s,
                    Err(e) => {
                        let win = drawing_area_clone.root().and_then(|r| r.downcast::<gtk::Window>().ok());
                        if let Some(w) = win {
                            crate::ufw::show_error(&w, &i18n("Auditor Error"), &e);
                        }
                        break;
                    }
                };
                let mut total_up = 0;
                let mut total_down = 0;
                for stat in &stats {
                    total_up += stat.upload_speed;
                    total_down += stat.download_speed;
                }
                
                {
                    let mut h = history.borrow_mut();
                    h.push((total_up, total_down));
                    if h.len() > 60 {
                        h.remove(0);
                    }
                }
                drawing_area_clone.queue_draw();

                let search_text = search_entry_clone.text().to_string().to_lowercase();
                let current_filter_state = (
                    btn_in_clone.is_active(),
                    btn_out_clone.is_active(),
                    proto_tcp_clone.is_active(),
                    proto_udp_clone.is_active(),
                    search_text.clone(),
                );

                let is_paused = pause_btn_clone.is_active();
                let filter_changed = last_filter_state.as_ref() != Some(&current_filter_state);

                if is_paused && !filter_changed {
                    continue;
                }

                last_filter_state = Some(current_filter_state);

                let stats_to_use = if is_paused {
                    last_stats.clone()
                } else {
                    last_stats = stats.clone();
                    stats
                };

                // Clear current list
                while let Some(child) = list_box_clone.first_child() {
                    list_box_clone.remove(&child);
                }
                
                let filtered_stats: Vec<_> = stats_to_use.into_iter().filter(|s| {
                    if btn_in_clone.is_active() && s.direction != "Inbound" {
                        return false;
                    }
                    if btn_out_clone.is_active() && s.direction != "Outbound" {
                        return false;
                    }
                    if proto_tcp_clone.is_active() && !s.protocol.to_uppercase().starts_with("TCP") {
                        return false;
                    }
                    if proto_udp_clone.is_active() && !s.protocol.to_uppercase().starts_with("UDP") {
                        return false;
                    }
                    if !search_text.is_empty() {
                        let proc_match = s.process_name.to_lowercase().contains(&search_text);
                        let ip_match = s.remote_ip.to_lowercase().contains(&search_text);
                        let domain_match = s.domain_name.as_ref().map(|d| d.to_lowercase().contains(&search_text)).unwrap_or(false);
                        let port_match = s.local_port.to_string().contains(&search_text) || s.remote_port.to_string().contains(&search_text);
                        if !proc_match && !ip_match && !domain_match && !port_match {
                            return false;
                        }
                    }
                    true
                }).collect();

                let mut sorted_stats = filtered_stats;
                sorted_stats.sort_by(|a, b| {
                    (b.upload_speed + b.download_speed).cmp(&(a.upload_speed + a.download_speed))
                });

                if sorted_stats.is_empty() {
                    let (title, subtitle) = if btn_in_clone.is_active() {
                        (
                            i18n("No Inbound Connections"),
                            i18n("No external devices are connecting to your open ports."),
                        )
                    } else if btn_out_clone.is_active() {
                        (
                            i18n("No Outbound Connections"),
                            i18n("No applications are sending data to the internet."),
                        )
                    } else {
                        (
                            i18n("No Active Connections"),
                            i18n("No network activity detected."),
                        )
                    };
                    
                    let empty_row = adw::ActionRow::builder()
                        .title(&title)
                        .subtitle(&subtitle)
                        .activatable(false)
                        .build();
                    let speed_label = gtk::Label::builder()
                        .label("0 B/s")
                        .css_classes(["dim-label"])
                        .build();
                    empty_row.add_suffix(&speed_label);
                    list_box_clone.append(&empty_row);
                    continue;
                }

                for stat in sorted_stats.iter() {

                    
                    
                    let format_compact = |speed: u64| -> String {
                        let speed_kb = speed as f64 / 1024.0;
                        if speed_kb > 1024.0 {
                            format!("{:.1} MB/s", speed_kb / 1024.0)
                        } else {
                            format!("{:.1} KB/s", speed_kb)
                        }
                    };

                    let up_str = format_compact(stat.upload_speed);
                    let down_str = format_compact(stat.download_speed);

                    let display_host = if resolve_dns_switch_clone.is_active() {
                        if let Some(domain) = &stat.domain_name {
                            format!("{} ({})", domain, stat.remote_ip)
                        } else {
                            stat.remote_ip.clone()
                        }
                    } else {
                        stat.remote_ip.clone()
                    };

                    let subtitle_text = if stat.direction == "Inbound" {
                        format!("{} {} -> {}", stat.protocol, display_host, stat.local_port)
                    } else {
                        format!("{} {} -> {}", stat.protocol, stat.local_port, display_host)
                    };
                    
                    let row = adw::ActionRow::builder()
                        .title(&stat.process_name)
                        .subtitle(&subtitle_text)
                        .activatable(false)
                        .build();
                        
                    let is_connected = (stat.direction == "Inbound" && stat.total_uploaded > 0) 
                                    || (stat.direction == "Outbound" && stat.total_downloaded > 0);
                    
                    let tooltip = if is_connected {
                        if stat.direction == "Inbound" {
                            "Connection Established:\nReceived inbound request and successfully replied (Uploaded > 0)."
                        } else {
                            "Connection Established:\nSent outbound request and successfully received reply (Downloaded > 0)."
                        }
                    } else {
                        if stat.direction == "Inbound" {
                            "Blocked or Ignored:\nReceived inbound request but never replied (Uploaded = 0)."
                        } else {
                            "Blocked or No Response:\nSent outbound request but never received reply (Downloaded = 0)."
                        }
                    };
                    
                    let status_icon = gtk::Image::builder()
                        .icon_name(if is_connected { "network-transmit-receive-symbolic" } else { "network-error-symbolic" })
                        .css_classes(if is_connected { ["success"].to_vec() } else { ["error"].to_vec() })
                        .valign(gtk::Align::Center)
                        .margin_end(10)
                        .tooltip_text(tooltip)
                        .build();
                    row.add_prefix(&status_icon);
                    
                    let speed_box = gtk::Box::builder()
                        .orientation(gtk::Orientation::Horizontal)
                        .spacing(20)
                        .valign(gtk::Align::Center)
                        .build();

                    let up_label = gtk::Label::builder()
                        .label(&format!("▲{}", up_str))
                        .css_classes(if stat.upload_speed > 0 { ["success", "monospace"].to_vec() } else { ["dim-label", "monospace"].to_vec() })
                        .build();

                    let down_label = gtk::Label::builder()
                        .label(&format!("▼{}", down_str))
                        .css_classes(if stat.download_speed > 0 { ["accent", "monospace"].to_vec() } else { ["dim-label", "monospace"].to_vec() })
                        .build();

                    speed_box.append(&up_label);
                    speed_box.append(&down_label);
                    
                    row.add_suffix(&speed_box);

                    let block_btn = gtk::Button::builder()
                        .icon_name("network-wireless-disconnected-symbolic")
                        .css_classes(["destructive-action"])
                        .valign(gtk::Align::Center)
                        .build();
                        
                    let remote_ip_clone = stat.remote_ip.clone();
                    let process_name_clone = stat.process_name.clone();
                    let direction_clone = stat.direction.clone();
                    let btn_clone = block_btn.clone();
                    block_btn.connect_clicked(move |_| {
                        let ip = remote_ip_clone.clone();
                        let proc_name = process_name_clone.clone();
                        let dir = direction_clone.clone();
                        let btn = btn_clone.clone();

                        let is_inbound = dir == "Inbound";
                        let dialog_body = if is_inbound {
                            format!("{}: {} ({})\n{}", i18n("Block inbound traffic from"), proc_name, ip, i18n("This will prevent the remote host from sending any packets to your machine."))
                        } else {
                            format!("{}: {} ({})\n{}", i18n("Block outbound traffic to"), proc_name, ip, i18n("This will prevent your machine from sending any packets to the remote host."))
                        };

                        let parent = btn.root().and_then(|r| r.downcast::<gtk::Window>().ok());
                        let dialog = adw::AlertDialog::builder()
                            .heading(i18n("Block Connection?"))
                            .body(dialog_body)
                            .build();
                        dialog.add_response("cancel", &i18n("Cancel"));
                        dialog.add_response("block", &i18n("Block"));
                        dialog.set_response_appearance("block", adw::ResponseAppearance::Destructive);
                        dialog.set_default_response(Some("cancel"));
                        dialog.set_close_response("cancel");

                        dialog.choose(parent.as_ref(), gtk::gio::Cancellable::NONE, move |response| {
                            if response == "block" {
                                let ip = ip.clone();
                                let proc_name = proc_name.clone();
                                let dir2 = dir.clone();
                                let proc_name_clone = proc_name.clone();
                                let btn2 = btn.clone();
                                glib::spawn_future_local(async move {
                                    let result = tokio::task::spawn_blocking(move || {
                                        let is_in = dir2 == "Inbound";
                                        let (rule_dir, from, to) = if is_in {
                                            (crate::ufw::types::Direction::In, Some(ip.clone()), None)
                                        } else {
                                            (crate::ufw::types::Direction::Out, None, Some(ip.clone()))
                                        };
                                        let params = crate::ufw::types::RuleParams {
                                            port: "".to_string(),
                                            action: crate::ufw::types::Action::Deny,
                                            direction: Some(rule_dir),
                                            protocol: None,
                                            from,
                                            to,
                                            interface: None,
                                            comment: Some(format!(
                                                "{}: {}",
                                                i18n("Audit Block"),
                                                proc_name
                                            )),
                                            // Never use insert_position here: on a fresh system
                                            // with zero existing rules, `ufw insert 1` fails with
                                            // "ERROR: Invalid position 1" because there is no
                                            // position 1 to insert before (applies to both v4
                                            // and v6 chains when empty). Appending is safe:
                                            // the deny rule is more specific than the default
                                            // policy and will take effect correctly.
                                            insert_position: None,
                                        };
                                        crate::ufw::backend::add_rule(&params)
                                    }).await.unwrap();

                                    match result {
                                        Ok(_) => {
                                            crate::ufw::show_info(&btn2, &i18n("Blocked Successfully"), &format!("{} {}", i18n("Rule added to block"), proc_name_clone));
                                        }
                                        Err(e) => {
                                            crate::ufw::show_error(&btn2, &i18n("Failed to Block"), &e.message);
                                        }
                                    }
                                });
                            }
                        });

                    });
                    
                    row.add_suffix(&block_btn);

                    list_box_clone.append(&row);
                }
            }
        });
    }
}
