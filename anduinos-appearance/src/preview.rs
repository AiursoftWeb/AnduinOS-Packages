use gtk::cairo::{Context, FontSlant, FontWeight};

use crate::layout::{Position, Style};

const ICON_COLORS: [(f64, f64, f64); 5] = [
    (0.95, 0.45, 0.20),
    (0.25, 0.55, 0.95),
    (0.25, 0.75, 0.45),
    (0.90, 0.25, 0.25),
    (0.85, 0.65, 0.15),
];

fn rounded_rect(cr: &Context, x: f64, y: f64, w: f64, h: f64, radius: f64) {
    cr.new_sub_path();
    cr.arc(x + w - radius, y + radius, radius, -std::f64::consts::PI / 2.0, 0.0);
    cr.arc(x + w - radius, y + h - radius, radius, 0.0, std::f64::consts::PI / 2.0);
    cr.arc(x + radius, y + h - radius, radius, std::f64::consts::PI / 2.0, std::f64::consts::PI);
    cr.arc(x + radius, y + radius, radius, std::f64::consts::PI, 3.0 * std::f64::consts::PI / 2.0);
    cr.close_path();
}

fn draw_icons(cr: &Context, x: f64, y: f64, icon_w: f64, icon_h: f64, icon_r: f64, icon_gap: f64, count: usize) {
    for index in 0..count {
        let icon_x = x + index as f64 * (icon_w + icon_gap);
        rounded_rect(cr, icon_x, y, icon_w, icon_h, icon_r);
        let (r, g, b) = ICON_COLORS[index % ICON_COLORS.len()];
        cr.set_source_rgb(r, g, b);
        let _ = cr.fill();
    }
}

fn draw_start_button(cr: &Context, x: f64, y: f64, w: f64, h: f64) {
    rounded_rect(cr, x, y, w, h, 3.0);
    cr.set_source_rgb(0.25, 0.55, 0.95);
    let _ = cr.fill();
    cr.set_source_rgb(1.0, 1.0, 1.0);
    let center_x = x + w / 2.0;
    let center_y = y + h / 2.0;
    let size = 3.0;
    cr.set_line_width(1.5);
    cr.move_to(center_x - size, center_y);
    cr.line_to(center_x + size, center_y);
    cr.move_to(center_x, center_y - size);
    cr.line_to(center_x, center_y + size);
    let _ = cr.stroke();
}

fn draw_sys_tray(cr: &Context, x: f64, y: f64) {
    for index in 0..4 {
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.45);
        cr.arc(x + index as f64 * 10.0, y + 4.0, 2.5, 0.0, 2.0 * std::f64::consts::PI);
        let _ = cr.fill();
    }
    let chevron_x = x + 40.0;
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.35);
    cr.set_line_width(1.5);
    cr.move_to(chevron_x, y);
    cr.line_to(chevron_x + 5.0, y + 5.0);
    cr.line_to(chevron_x, y + 8.0);
    let _ = cr.stroke();
}

pub fn draw(cr: &Context, w: i32, h: i32, style: Style, position: Position) {
    let w = w as f64;
    let h = h as f64;
    let start_centered = style == Style::Eleven;
    let icons_centered = matches!(style, Style::Eleven | Style::Seperated);
    let bar_thick = 18.0;
    let (icon_w, icon_h, icon_r, icon_gap, icon_count) = (14.0, 14.0, 3.0, 5.0, 5_usize);
    let (start_w, start_h) = (18.0, 12.0);

    cr.set_source_rgb(0.12, 0.12, 0.14);
    cr.rectangle(0.0, 0.0, w, h);
    let _ = cr.fill();

    let (bar_x, bar_y, bar_w, bar_h) = match position {
        Position::Bottom => (0.0, h - bar_thick, w, bar_thick),
        Position::Top => (0.0, 0.0, w, bar_thick),
        Position::Left => (0.0, 0.0, bar_thick, h),
        Position::Right => (w - bar_thick, 0.0, bar_thick, h),
    };

    let horizontal = matches!(position, Position::Bottom | Position::Top);
    cr.set_source_rgba(0.18, 0.18, 0.20, 0.85);
    cr.rectangle(bar_x, bar_y, bar_w, bar_h);
    let _ = cr.fill();
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.07);
    cr.set_line_width(1.0);
    match position {
        Position::Bottom => {
            cr.move_to(bar_x, bar_y);
            cr.line_to(bar_x + bar_w, bar_y);
        }
        Position::Top => {
            cr.move_to(bar_x, bar_y + bar_h);
            cr.line_to(bar_x + bar_w, bar_y + bar_h);
        }
        Position::Left => {
            cr.move_to(bar_x + bar_w, bar_y);
            cr.line_to(bar_x + bar_w, bar_y + bar_h);
        }
        Position::Right => {
            cr.move_to(bar_x, bar_y);
            cr.line_to(bar_x, bar_y + bar_h);
        }
    }
    let _ = cr.stroke();

    if horizontal {
        let padding = 6.0;
        let start_y = bar_y + 3.0;
        let icons_y = bar_y + 2.0;
        let icons_width = icon_count as f64 * icon_w + (icon_count as f64 - 1.0) * icon_gap;
        if start_centered {
            let group_width = start_w + 12.0 + icons_width;
            let group_x = bar_x + (bar_w - group_width) / 2.0;
            draw_start_button(cr, group_x, start_y, start_w, start_h);
            draw_icons(cr, group_x + start_w + 12.0, icons_y, icon_w, icon_h, icon_r, icon_gap, icon_count);
        } else if icons_centered {
            draw_start_button(cr, bar_x + padding, start_y, start_w, start_h);
            draw_icons(cr, bar_x + (bar_w - icons_width) / 2.0, icons_y, icon_w, icon_h, icon_r, icon_gap, icon_count);
        } else {
            draw_start_button(cr, bar_x + padding, start_y, start_w, start_h);
            draw_icons(cr, bar_x + padding + start_w + 12.0, icons_y, icon_w, icon_h, icon_r, icon_gap, icon_count);
        }
        draw_sys_tray(cr, bar_x + bar_w - 52.0, bar_y + 5.0);
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.5);
        cr.select_font_face("sans-serif", FontSlant::Normal, FontWeight::Normal);
        cr.set_font_size(6.5);
        cr.move_to(bar_x + bar_w - 90.0, bar_y + 14.0);
        let _ = cr.show_text("12:34");
    } else {
        let start_x = bar_x + 2.0;
        let start_y = bar_y + 6.0;
        let vertical_start_size = bar_thick - 4.0;
        draw_start_button(cr, start_x, start_y, vertical_start_size, vertical_start_size);
        let vertical_icon_size = bar_thick - 6.0;
        let icons_height = icon_count as f64 * vertical_icon_size + (icon_count as f64 - 1.0) * icon_gap;
        let icons_y = if icons_centered {
            bar_y + (bar_h - icons_height) / 2.0
        } else {
            bar_y + 30.0
        };
        for index in 0..icon_count {
            let icon_y = icons_y + index as f64 * (vertical_icon_size + icon_gap);
            rounded_rect(cr, bar_x + 3.0, icon_y, vertical_icon_size, vertical_icon_size, icon_r);
            let (r, g, b) = ICON_COLORS[index % ICON_COLORS.len()];
            cr.set_source_rgb(r, g, b);
            let _ = cr.fill();
        }
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.45);
        cr.select_font_face("sans-serif", FontSlant::Normal, FontWeight::Normal);
        cr.set_font_size(5.5);
        cr.move_to(bar_x + 2.0, bar_y + bar_h - 6.0);
        let _ = cr.show_text("12:34");
    }
}
