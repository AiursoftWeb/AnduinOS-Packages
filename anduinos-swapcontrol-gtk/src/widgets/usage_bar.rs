use adw::subclass::prelude::*;
use gtk::cairo;
use gtk::glib;
use gtk::prelude::*;
use std::cell::RefCell;

const BAR_HEIGHT: f64 = 10.0;
const MIN_VISIBLE_FILL_PX: f64 = 6.0;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct UsageBar {
        pub fraction: RefCell<f64>,
        pub bar_color: RefCell<(f64, f64, f64)>,
        pub title_label: RefCell<Option<gtk::Label>>,
        pub sub_label: RefCell<Option<gtk::Label>>,
        pub drawing_area: RefCell<Option<gtk::DrawingArea>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for UsageBar {
        const NAME: &'static str = "UsageBar";
        type Type = super::UsageBar;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for UsageBar {}
    impl WidgetImpl for UsageBar {}
    impl BoxImpl for UsageBar {}
}

glib::wrapper! {
    pub struct UsageBar(ObjectSubclass<imp::UsageBar>)
        @extends gtk::Widget, gtk::Box,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

impl UsageBar {
    pub fn new(label: &str, color: (f64, f64, f64)) -> Self {
        let obj: Self = glib::Object::builder().build();
        obj.set_orientation(gtk::Orientation::Vertical);
        obj.set_spacing(6);
        obj.set_hexpand(true);
        obj.set_valign(gtk::Align::Center);

        *obj.imp().bar_color.borrow_mut() = color;

        let header = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(12)
            .hexpand(true)
            .build();

        let title_label = gtk::Label::builder()
            .label(label)
            .css_classes(["heading"])
            .halign(gtk::Align::Start)
            .valign(gtk::Align::Center)
            .single_line_mode(true)
            .visible(!label.is_empty())
            .build();

        let sub_label = gtk::Label::builder()
            .label("")
            .css_classes(["caption", "numeric"])
            .halign(gtk::Align::End)
            .valign(gtk::Align::Center)
            .hexpand(true)
            .xalign(1.0)
            .single_line_mode(true)
            .ellipsize(pango::EllipsizeMode::End)
            .build();

        header.append(&title_label);
        header.append(&sub_label);

        let drawing_area = gtk::DrawingArea::builder()
            .content_height(BAR_HEIGHT as i32)
            .content_width(140)
            .hexpand(true)
            .build();

        let weak = obj.downgrade();
        drawing_area.set_draw_func(move |_, cr, w, h| {
            if let Some(this) = weak.upgrade() {
                Self::draw(&this, cr, w as f64, h as f64);
            }
        });

        obj.append(&header);
        obj.append(&drawing_area);

        *obj.imp().title_label.borrow_mut() = Some(title_label);
        *obj.imp().sub_label.borrow_mut() = Some(sub_label);
        *obj.imp().drawing_area.borrow_mut() = Some(drawing_area);

        obj
    }

    pub fn set_fraction(&self, fraction: f64, sub_text: &str) {
        let imp = self.imp();
        *imp.fraction.borrow_mut() = fraction;
        if let Some(label) = imp.sub_label.borrow().as_ref() {
            label.set_text(sub_text);
            label.set_tooltip_text(if sub_text.is_empty() {
                None
            } else {
                Some(sub_text)
            });
        }
        if let Some(area) = imp.drawing_area.borrow().as_ref() {
            area.queue_draw();
        }
    }

    #[cfg(test)]
    pub(crate) fn assert_two_row_non_overlapping(&self) {
        let imp = self.imp();
        let title = imp.title_label.borrow().clone().expect("title label");
        let sub = imp.sub_label.borrow().clone().expect("sub label");
        let area = imp.drawing_area.borrow().clone().expect("drawing area");

        let title_bounds = title.compute_bounds(self).expect("title bounds");
        let sub_bounds = sub.compute_bounds(self).expect("sub bounds");
        let area_bounds = area.compute_bounds(self).expect("area bounds");

        assert!(
            title_bounds.x() + title_bounds.width() <= sub_bounds.x(),
            "title {:?} overlaps sub {:?}",
            title_bounds,
            sub_bounds
        );
        assert!(
            title_bounds.y() + title_bounds.height() <= area_bounds.y(),
            "title {:?} overlaps bar {:?}",
            title_bounds,
            area_bounds
        );
        assert!(
            sub_bounds.y() + sub_bounds.height() <= area_bounds.y(),
            "sub {:?} overlaps bar {:?}",
            sub_bounds,
            area_bounds
        );
    }

    fn draw(this: &Self, cr: &cairo::Context, w: f64, h: f64) {
        if w <= 0.0 || h <= 0.0 {
            return;
        }
        let imp = this.imp();
        let frac = *imp.fraction.borrow();
        let (r, g, b) = *imp.bar_color.borrow();

        let bar_h = h.min(BAR_HEIGHT);
        let bar_y = (h - bar_h) / 2.0;
        let radius = bar_h / 2.0;

        // Theme-aware background track (adapts to light and dark modes).
        if adw::StyleManager::default().is_dark() {
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.15);
        } else {
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.12);
        }
        rounded_rect(cr, 0.0, bar_y, w, bar_h, radius);
        cr.fill().ok();

        // Filled portion — guarantee a visible minimum width for any positive usage,
        // clipped cleanly to the outer rounded track.
        let fill_w = compute_fill_width(w, frac);
        if fill_w > 0.0 {
            cr.save().ok();
            rounded_rect(cr, 0.0, bar_y, w, bar_h, radius);
            cr.clip();

            cr.set_source_rgb(r, g, b);
            rounded_rect(cr, 0.0, bar_y, fill_w, bar_h, radius);
            cr.fill().ok();
            cr.restore().ok();
        }
    }
}

pub fn compute_fill_width(width: f64, fraction: f64) -> f64 {
    if width <= 0.0 || !fraction.is_finite() || fraction <= 0.0 {
        return 0.0;
    }
    let clamped = fraction.clamp(0.0, 1.0);
    (width * clamped).max(MIN_VISIBLE_FILL_PX).min(width)
}

fn rounded_rect(cr: &cairo::Context, x: f64, y: f64, w: f64, h: f64, r: f64) {
    let r = r.min(w / 2.0).min(h / 2.0).max(0.0);
    cr.new_sub_path();
    cr.arc(x + w - r, y + r, r, -std::f64::consts::PI / 2., 0.);
    cr.arc(x + w - r, y + h - r, r, 0., std::f64::consts::PI / 2.);
    cr.arc(
        x + r,
        y + h - r,
        r,
        std::f64::consts::PI / 2.,
        std::f64::consts::PI,
    );
    cr.arc(
        x + r,
        y + r,
        r,
        std::f64::consts::PI,
        3. * std::f64::consts::PI / 2.,
    );
    cr.close_path();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compute_fill_width_guarantees_visible_fill_for_tiny_positive_fraction() {
        assert_eq!(compute_fill_width(300.0, 0.0), 0.0);
        assert_eq!(compute_fill_width(300.0, -0.1), 0.0);
        assert_eq!(compute_fill_width(300.0, f64::NAN), 0.0);

        // 14.7 MiB / 17.0 GiB ~= 0.00084 -> previously 0.25 px (invisible!)
        let tiny_fill = compute_fill_width(300.0, 14.7 / (17.0 * 1024.0));
        assert_eq!(tiny_fill, MIN_VISIBLE_FILL_PX);

        // Normal and full fractions scale proportionally and stay clamped to width.
        assert!((compute_fill_width(300.0, 0.5) - 150.0).abs() < 1e-9);
        assert_eq!(compute_fill_width(300.0, 1.2), 300.0);
        assert_eq!(compute_fill_width(4.0, 0.01), 4.0);
    }
}
