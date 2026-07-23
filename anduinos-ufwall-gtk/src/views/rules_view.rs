use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;

use crate::i18n::i18n;
use crate::ufw::types::{UfwRule, UfwStatus};
use crate::widgets::rule_row::RuleRow;
use crate::widgets::add_rule_dialog::AddRuleDialog;
use crate::ufw::backend;
use crate::ufw::show_error;

mod imp {
    use super::*;
    use std::cell::RefCell;

    #[derive(Default)]
    pub struct RulesView {
        pub rules_group: RefCell<Option<adw::PreferencesGroup>>,
        pub empty_state: RefCell<Option<adw::StatusPage>>,
        pub added_rows: RefCell<Vec<gtk::Widget>>,
        pub current_rules: RefCell<Vec<UfwRule>>,
        pub search_entry: RefCell<Option<gtk::SearchEntry>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for RulesView {
        const NAME: &'static str = "RulesView";
        type Type = super::RulesView;
        type ParentType = adw::Bin;
    }

    impl ObjectImpl for RulesView {
        fn constructed(&self) {
            self.parent_constructed();
            self.obj().setup_ui();
        }
    }
    
    impl WidgetImpl for RulesView {}
    impl BinImpl for RulesView {}
}

glib::wrapper! {
    pub struct RulesView(ObjectSubclass<imp::RulesView>)
        @extends gtk::Widget, adw::Bin,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

impl RulesView {
    pub fn new() -> Self {
        glib::Object::builder()
            .build()
    }

    fn setup_ui(&self) {
        let imp = self.imp();

        let page = adw::PreferencesPage::new();

        // Search entry for filtering rules
        let search_entry = gtk::SearchEntry::builder()
            .placeholder_text(i18n("Search rules..."))
            .margin_bottom(12)
            .build();
        let search_group = adw::PreferencesGroup::builder().build();
        search_group.add(&search_entry);
        page.add(&search_group);

        let rules_group = adw::PreferencesGroup::builder()
            .title(i18n("Firewall Rules"))
            .build();

        // Empty-state status page (shown when no rules)
        let empty_state = adw::StatusPage::builder()
            .title(i18n("No Rules"))
            .description(i18n("There are no rules configured yet."))
            .icon_name("network-firewall")
            .build();
        rules_group.add(&empty_state);

        page.add(&rules_group);

        let weak_self = self.downgrade();
        search_entry.connect_search_changed(move |entry| {
            if let Some(view) = weak_self.upgrade() {
                view.apply_filter(entry.text().to_string());
            }
        });

        *imp.search_entry.borrow_mut() = Some(search_entry);

        let add_btn = gtk::Button::builder()
            .label(i18n("Add Rule"))
            .css_classes(["suggested-action", "pill"])
            .valign(gtk::Align::Center)
            .build();

        let weak_self = self.downgrade();
        add_btn.connect_clicked(move |_| {
            if let Some(view) = weak_self.upgrade() {
                if let Some(root) = view.root() {
                    let dialog = AddRuleDialog::new();
                    dialog.present(Some(&root));
                }
            }
        });

        let reset_btn = gtk::Button::builder()
            .label(i18n("Delete All"))
            .css_classes(["destructive-action", "pill"])
            .valign(gtk::Align::Center)
            .build();

        let weak_self2 = self.downgrade();
        reset_btn.connect_clicked(move |_| {
            if let Some(view) = weak_self2.upgrade() {
                    let dialog = adw::AlertDialog::builder()
                        .heading(i18n("Delete All Rules?"))
                        .body(i18n("This will delete all custom rules. This action cannot be undone."))
                        .build();
                    dialog.add_response("cancel", &i18n("Cancel"));
                    dialog.add_response("delete_all", &i18n("Delete All"));
                    dialog.set_response_appearance("delete_all", adw::ResponseAppearance::Destructive);
                    dialog.set_default_response(Some("cancel"));
                    dialog.set_close_response("cancel");

                    let v = view.downgrade();
                    let parent_window = view.root().and_then(|r| r.downcast::<gtk::Window>().ok());
                    dialog.choose(parent_window.as_ref(), gtk::gio::Cancellable::NONE, move |response| {
                        if response == "delete_all" {
                            if let Some(view) = v.upgrade() {
                                glib::spawn_future_local(async move {
                                    let result = tokio::task::spawn_blocking(move || {
                                        backend::delete_all_rules()
                                    }).await.unwrap();
                                    if let Err(e) = &result {
                                        show_error(&view, &i18n("Error"), &e.to_string());
                                    }
                                });
                            }
                        }
                    });
            }
        });

        let header_box = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(12)
            .margin_top(12)
            .homogeneous(true)
            .build();
        header_box.append(&reset_btn);
        header_box.append(&add_btn);

        rules_group.set_header_suffix(Some(&header_box));

        self.set_child(Some(&page));

        *imp.rules_group.borrow_mut() = Some(rules_group);
        *imp.empty_state.borrow_mut() = Some(empty_state);
    }

    pub fn update(&self, status: &UfwStatus) {
        let imp = self.imp();
        self.set_sensitive(status.active);

        // Store rules for edit access
        *imp.current_rules.borrow_mut() = status.rules.clone();

        if let Some(group) = imp.rules_group.borrow().as_ref() {
            for row in imp.added_rows.borrow().iter() {
                group.remove(row);
            }
            imp.added_rows.borrow_mut().clear();

            // Show/hide empty state
            if let Some(empty) = imp.empty_state.borrow().as_ref() {
                empty.set_visible(status.rules.is_empty());
            }

            if !status.rules.is_empty() {
                // Get filter text
                let filter = imp.search_entry.borrow().as_ref()
                    .map(|e| e.text().to_string().to_lowercase())
                    .unwrap_or_default();

                let weak_self = self.downgrade();
                let mut added = imp.added_rows.borrow_mut();
                for rule in &status.rules {
                    // Apply filter
                    if !filter.is_empty() {
                        let port_lower = rule.port.to_lowercase();
                        let from_lower = rule.from.to_lowercase();
                        let to_lower = rule.to.to_lowercase();
                        if !port_lower.contains(&filter)
                            && !from_lower.contains(&filter)
                            && !to_lower.contains(&filter)
                        {
                            continue;
                        }
                    }

                    
                    let weak_self_clone = weak_self.clone();
                    let on_edit = Box::new(move |num: u32| {
                        if let Some(view) = weak_self_clone.upgrade() {
                            view.on_edit_rule(num);
                        }
                    });
                    let weak_self_clone2 = weak_self.clone();
                    let on_deleted = Box::new(move || {
                        if let Some(view) = weak_self_clone2.upgrade() {
                            view.refresh_rules();
                        }
                    });
                    let row = RuleRow::new(rule, on_edit, on_deleted);
                    group.add(&row);
                    added.push(row.upcast::<gtk::Widget>());
                }
            }
        }
    }

    fn refresh_rules(&self) {
        let weak_self = self.downgrade();
        glib::spawn_future_local(async move {
            let result = tokio::task::spawn_blocking(|| {
                backend::read_status()
            }).await.unwrap();
            if let Some(view) = weak_self.upgrade() {
                match result {
                    Ok(status) => view.update(&status),
                    Err(e) => {
                        eprintln!("refresh_rules: failed to read status: {}", e);
                    }
                }
            }
        });
    }

    fn on_edit_rule(&self, num: u32) {
        let imp = self.imp();
        let rules = imp.current_rules.borrow();
        if let Some(rule) = rules.iter().find(|r| r.number == num) {
            if let Some(root) = self.root() {
                let dialog = AddRuleDialog::new();
                dialog.set_edit_data(rule);
                dialog.present(Some(&root));
            }
        }
    }

    fn apply_filter(&self, _filter: String) {
        // Re-render with current rules and filter
        let imp = self.imp();
        
        
        // Only re-render rules, don't call backend again
        if let Some(group) = imp.rules_group.borrow().as_ref() {
            for row in imp.added_rows.borrow().iter() {
                group.remove(row);
            }
            imp.added_rows.borrow_mut().clear();

            let rules = imp.current_rules.borrow();
            let has_rules = !rules.is_empty();
            drop(rules);

            if let Some(empty) = imp.empty_state.borrow().as_ref() {
                empty.set_visible(!has_rules);
            }

            let filter = imp.search_entry.borrow().as_ref()
                .map(|e| e.text().to_string().to_lowercase())
                .unwrap_or_default();

            let weak_self = self.downgrade();
            let mut added = imp.added_rows.borrow_mut();
            for rule in &imp.current_rules.borrow().clone() {
                if !filter.is_empty() {
                    let pl = rule.port.to_lowercase();
                    let fl = rule.from.to_lowercase();
                    let tl = rule.to.to_lowercase();
                    if !pl.contains(&filter) && !fl.contains(&filter) && !tl.contains(&filter) {
                        continue;
                    }
                }
                let weak_self_clone = weak_self.clone();
                let on_edit = Box::new(move |num: u32| {
                    if let Some(view) = weak_self_clone.upgrade() {
                        view.on_edit_rule(num);
                    }
                });
                let weak_self_clone2 = weak_self.clone();
                let on_deleted = Box::new(move || {
                    if let Some(view) = weak_self_clone2.upgrade() {
                        view.refresh_rules();
                    }
                });
                let row = RuleRow::new(rule, on_edit, on_deleted);
                group.add(&row);
                added.push(row.upcast::<gtk::Widget>());
            }
        }
    }
}

impl Default for RulesView {
    fn default() -> Self {
        Self::new()
    }
}
