use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;
use std::cell::RefCell;

use crate::i18n::i18n;
use crate::ufw::show_error;
use crate::ufw::types::{Action, Direction, Protocol, RuleParams};
use crate::ufw::backend;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct AddRuleDialog {
        pub preset_combo: RefCell<Option<adw::ComboRow>>,
        pub port_entry: RefCell<Option<adw::EntryRow>>,
        pub action_combo: RefCell<Option<adw::ComboRow>>,
        pub dir_combo: RefCell<Option<adw::ComboRow>>,
        pub proto_combo: RefCell<Option<adw::ComboRow>>,
        pub source_entry: RefCell<Option<adw::EntryRow>>,
        pub dest_entry: RefCell<Option<adw::EntryRow>>,
        pub interface_entry: RefCell<Option<adw::EntryRow>>,
        pub comment_entry: RefCell<Option<adw::EntryRow>>,
        pub position_spin: RefCell<Option<adw::SpinRow>>,
        pub add_btn: RefCell<Option<gtk::Button>>,
        pub edit_rule_number: RefCell<Option<u32>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for AddRuleDialog {
        const NAME: &'static str = "AddRuleDialog";
        type Type = super::AddRuleDialog;
        type ParentType = adw::Dialog;
    }

    impl ObjectImpl for AddRuleDialog {
        fn constructed(&self) {
            self.parent_constructed();
            self.obj().setup_ui();
        }
    }
    
    impl WidgetImpl for AddRuleDialog {}
    impl AdwDialogImpl for AddRuleDialog {}
}

glib::wrapper! {
    pub struct AddRuleDialog(ObjectSubclass<imp::AddRuleDialog>)
        @extends gtk::Widget, adw::Dialog,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

impl AddRuleDialog {
    pub fn new() -> Self {
        glib::Object::builder()
            .property("title", i18n("Add Rule"))
            .property("content-width", 400)
            .build()
    }

    fn setup_ui(&self) {
        let imp = self.imp();

        let page = adw::PreferencesPage::new();
        
        let basic_group = adw::PreferencesGroup::builder().build();

        let preset_combo = adw::ComboRow::builder()
            .title(i18n("Service"))
            .model(&gtk::StringList::new(&[
                &i18n("Custom"),
                "SSH (22)",
                "HTTP (80)",
                "HTTPS (443)",
                "FTP (21)",
                "SMTP (25)",
                "POP3 (110)",
                "IMAP (143)",
                "DNS (53)",
                "MySQL (3306)",
                "PostgreSQL (5432)",
                "RDP (3389)",
                "VNC (5900)",
            ]))
            .build();

        let port_entry = adw::EntryRow::builder()
            .title(i18n("Port / Service Name"))
            .build();
            
        let action_combo = adw::ComboRow::builder()
            .title(i18n("Action"))
            .model(&gtk::StringList::new(&[
                &i18n("Deny"),
                &i18n("Allow"),
                &i18n("Reject"),
                &i18n("Limit"),
            ]))
            .build();

        basic_group.add(&preset_combo);
        basic_group.add(&port_entry);
        basic_group.add(&action_combo);
        action_combo.set_selected(1); // Default: Allow
        page.add(&basic_group);

        let adv_group = adw::PreferencesGroup::builder().build();
        let expander = adw::ExpanderRow::builder()
            .title(i18n("Advanced"))
            .build();

        let dir_combo = adw::ComboRow::builder()
            .title(i18n("Direction"))
            .model(&gtk::StringList::new(&[
                &i18n("In"),
                &i18n("Out"),
            ]))
            .build();
            
        let proto_combo = adw::ComboRow::builder()
            .title(i18n("Protocol"))
            .model(&gtk::StringList::new(&[
                &i18n("Both"),
                &i18n("TCP"),
                &i18n("UDP"),
            ]))
            .build();

        let source_entry = adw::EntryRow::builder()
            .title(i18n("Source IP"))
            .build();

        let dest_entry = adw::EntryRow::builder()
            .title(i18n("Destination IP"))
            .build();

        expander.add_row(&dir_combo);
        expander.add_row(&proto_combo);
        expander.add_row(&source_entry);
        expander.add_row(&dest_entry);

        let interface_entry = adw::EntryRow::builder()
            .title(i18n("Interface"))
            .build();
        expander.add_row(&interface_entry);

        let comment_entry = adw::EntryRow::builder()
            .title(i18n("Comment"))
            .build();
        expander.add_row(&comment_entry);

        let position_spin = adw::SpinRow::builder()
            .title(i18n("Insert Before Rule"))
            .subtitle(i18n("0 = append at end"))
            .adjustment(&gtk::Adjustment::new(0.0, 0.0, 1000.0, 1.0, 10.0, 0.0))
            .build();
        expander.add_row(&position_spin);

        adv_group.add(&expander);
        page.add(&adv_group);

        let header = adw::HeaderBar::builder()
            .show_start_title_buttons(false)
            .show_end_title_buttons(false)
            .build();

        let cancel_btn = gtk::Button::with_label(&i18n("Cancel"));
        let add_btn = gtk::Button::builder()
            .label(i18n("Add"))
            .css_classes(["suggested-action"])
            .build();

        header.pack_start(&cancel_btn);
        header.pack_end(&add_btn);

        let toolbar = adw::ToolbarView::builder()
            .content(&page)
            .build();
        toolbar.add_top_bar(&header);

        self.set_child(Some(&toolbar));

        *imp.preset_combo.borrow_mut() = Some(preset_combo.clone());
        *imp.port_entry.borrow_mut() = Some(port_entry.clone());
        *imp.action_combo.borrow_mut() = Some(action_combo.clone());
        *imp.dir_combo.borrow_mut() = Some(dir_combo.clone());
        *imp.proto_combo.borrow_mut() = Some(proto_combo.clone());
        *imp.source_entry.borrow_mut() = Some(source_entry.clone());
        *imp.dest_entry.borrow_mut() = Some(dest_entry.clone());
        *imp.interface_entry.borrow_mut() = Some(interface_entry.clone());
        *imp.comment_entry.borrow_mut() = Some(comment_entry.clone());
        *imp.position_spin.borrow_mut() = Some(position_spin.clone());
        *imp.add_btn.borrow_mut() = Some(add_btn.clone());

        preset_combo.connect_selected_notify(glib::clone!(
            #[weak] port_entry,
            move |combo| {
                let text = match combo.selected() {
                    0 => "",
                    1 => "22",
                    2 => "80",
                    3 => "443",
                    4 => "21",
                    5 => "25",
                    6 => "110",
                    7 => "143",
                    8 => "53",
                    9 => "3306",
                    10 => "5432",
                    11 => "3389",
                    12 => "5900",
                    _ => "",
                };
                if combo.selected() != 0 {
                    port_entry.set_text(text);
                    port_entry.set_visible(false);
                } else {
                    port_entry.set_visible(true);
                }
            }
        ));

        let weak_self = self.downgrade();
        cancel_btn.connect_clicked(move |_| {
            if let Some(dialog) = weak_self.upgrade() {
                dialog.close();
            }
        });

        let weak_self2 = self.downgrade();
        add_btn.connect_clicked(move |btn| {
            if let Some(dialog) = weak_self2.upgrade() {
                dialog.on_add_clicked(btn);
            }
        });
    }

    /// Pre-populate the dialog for editing an existing rule.
    pub fn set_edit_data(&self, rule: &crate::ufw::types::UfwRule) {
        let imp = self.imp();
        *imp.edit_rule_number.borrow_mut() = Some(rule.number);

        if let Some(entry) = imp.port_entry.borrow().as_ref() {
            entry.set_text(&rule.port);
        }
        if let Some(combo) = imp.action_combo.borrow().as_ref() {
            combo.set_selected(rule.action.index());
        }
        if let Some(combo) = imp.dir_combo.borrow().as_ref() {
            combo.set_selected(rule.direction.index());
        }
        if let Some(btn) = imp.add_btn.borrow().as_ref() {
            btn.set_label(&i18n("Save"));
        }
    }

    fn on_add_clicked(&self, btn: &gtk::Button) {
        let imp = self.imp();
        let edit_num = *imp.edit_rule_number.borrow();
        
        let port = imp.port_entry.borrow().as_ref().unwrap().text().to_string();
        if port.trim().is_empty() {
            return;
        }

        let action = Action::from_index(imp.action_combo.borrow().as_ref().unwrap().selected());
        
        let dir_combo = imp.dir_combo.borrow();
        let direction = Some(match dir_combo.as_ref().unwrap().selected() {
            0 => Direction::In,
            _ => Direction::Out,
        });

        let proto_combo = imp.proto_combo.borrow();
        let protocol = Some(Protocol::from_index(proto_combo.as_ref().unwrap().selected()));

        let from_text = imp.source_entry.borrow().as_ref().unwrap().text().to_string();
        let from = if from_text.trim().is_empty() { None } else { Some(from_text) };

        let to_text = imp.dest_entry.borrow().as_ref().unwrap().text().to_string();
        let to = if to_text.trim().is_empty() { None } else { Some(to_text) };

        let iface_text = imp.interface_entry.borrow().as_ref().unwrap().text().to_string();
        let interface = if iface_text.trim().is_empty() { None } else { Some(iface_text) };

        let comment_text = imp.comment_entry.borrow().as_ref().unwrap().text().to_string();
        let comment = if comment_text.trim().is_empty() { None } else { Some(comment_text) };

        let pos = imp.position_spin.borrow().as_ref().unwrap().value() as u32;
        let insert_position = if pos == 0 { None } else { Some(pos) };

        let params = RuleParams {
            port,
            action,
            direction,
            protocol,
            from,
            to,
            interface,
            comment,
            insert_position,
        };

        btn.set_sensitive(false);
        let weak_self = self.downgrade();

        glib::spawn_future_local(async move {
            let result = tokio::task::spawn_blocking(move || {
                if let Some(num) = edit_num {
                    // Edit mode: delete old rule, then add new at same position
                    backend::delete_rule(num)?;
                    let mut edit_params = params.clone();
                    edit_params.insert_position = Some(num);
                    backend::add_rule(&edit_params)
                } else {
                    backend::add_rule(&params)
                }
            }).await.unwrap();

            if let Some(dialog) = weak_self.upgrade() {
                match result {
                    Ok(_) => { dialog.close(); }
                    Err(e) => {
                        show_error(&dialog, &i18n("Error"), &e.to_string());
                    }
                }
            }
        });
    }
}

impl Default for AddRuleDialog {
    fn default() -> Self {
        Self::new()
    }
}
