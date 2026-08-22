use std::io::Write;
use std::process::{Command, Stdio};

use serde_json::{json, Map, Value};

pub const ARC: &str = "/org/gnome/shell/extensions/arcmenu";
pub const DTP: &str = "/org/gnome/shell/extensions/dash-to-panel";

const MIN_MENU_HEIGHT: i32 = 650;
const MAX_MENU_HEIGHT: i32 = 785;
const MIN_SCREEN_HEIGHT: i32 = 768;
const MAX_SCREEN_HEIGHT: i32 = 1080;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Style {
    Classic,
    Eleven,
    Seperated,
}

impl Style {
    #[allow(dead_code)]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Classic => "classic",
            Self::Eleven => "eleven",
            Self::Seperated => "seperated",
        }
    }

    pub fn uses_group_apps(self) -> bool {
        matches!(self, Self::Classic | Self::Seperated)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Position {
    Bottom,
    Top,
    Left,
    Right,
}

impl Position {
    #[allow(dead_code)]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Bottom => "bottom",
            Self::Top => "top",
            Self::Left => "left",
            Self::Right => "right",
        }
    }

    pub fn dconf_value(self) -> &'static str {
        match self {
            Self::Bottom => "BOTTOM",
            Self::Top => "TOP",
            Self::Left => "LEFT",
            Self::Right => "RIGHT",
        }
    }

    pub fn all() -> [Position; 4] {
        [Self::Bottom, Self::Top, Self::Left, Self::Right]
    }
}

pub trait Dconf {
    fn read(&self, key: &str) -> Option<String>;
    fn write(&self, key: &str, value: &str) -> Result<(), ()>;
    fn reset(&self, key: &str) -> Result<(), ()>;
}

pub struct CliDconf;

impl Dconf for CliDconf {
    fn read(&self, key: &str) -> Option<String> {
        dconf_read(key)
    }

    fn write(&self, key: &str, value: &str) -> Result<(), ()> {
        let status = Command::new("dconf")
            .args(["write", key, value])
            .status()
            .map_err(|_| ())?;
        if status.success() {
            Ok(())
        } else {
            Err(())
        }
    }

    fn reset(&self, key: &str) -> Result<(), ()> {
        let status = Command::new("dconf")
            .args(["reset", key])
            .status()
            .map_err(|_| ())?;
        if status.success() {
            Ok(())
        } else {
            Err(())
        }
    }
}

pub fn dconf_read(key: &str) -> Option<String> {
    let output = Command::new("dconf").args(["read", key]).output().ok()?;
    if !output.status.success() {
        return None;
    }
    let value = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if value.is_empty() {
        None
    } else {
        Some(value)
    }
}

fn menu_config(style: Style, position: Position) -> (&'static str, &'static str) {
    match (style, position) {
        (Style::Classic | Style::Seperated, Position::Bottom) => ("arcmenu", "BottomLeft"),
        (Style::Classic | Style::Seperated, Position::Top) => ("arcmenu", "TopLeft"),
        (Style::Classic | Style::Seperated, Position::Left) => ("arcmenu", "TopLeft"),
        (Style::Classic | Style::Seperated, Position::Right) => ("arcmenu", "TopRight"),
        (Style::Eleven, Position::Bottom) => ("11", "BottomCentered"),
        (Style::Eleven, Position::Top) => ("11", "TopCentered"),
        (Style::Eleven, Position::Left | Position::Right) => ("11", "Off"),
    }
}

fn menu_max_height(style: Style) -> i32 {
    match style {
        Style::Eleven => MIN_MENU_HEIGHT,
        Style::Classic | Style::Seperated => MAX_MENU_HEIGHT,
    }
}

fn panel_elements(style: Style) -> Value {
    match style {
        Style::Seperated => json!([
            {"element": "centerBox", "visible": true, "position": "stackedTL"},
            {"element": "taskbar", "visible": true, "position": "centerMonitor"},
            {"element": "showAppsButton", "visible": false, "position": "stackedTL"},
            {"element": "activitiesButton", "visible": true, "position": "stackedBR"},
            {"element": "leftBox", "visible": true, "position": "stackedBR"},
            {"element": "rightBox", "visible": true, "position": "stackedBR"},
            {"element": "systemMenu", "visible": true, "position": "stackedBR"},
            {"element": "dateMenu", "visible": true, "position": "stackedBR"},
            {"element": "desktopButton", "visible": true, "position": "stackedBR"},
        ]),
        Style::Eleven => json!([
            {"element": "activitiesButton", "visible": true, "position": "stackedTL"},
            {"element": "showAppsButton", "visible": false, "position": "stackedTL"},
            {"element": "leftBox", "visible": true, "position": "stackedTL"},
            {"element": "centerBox", "visible": true, "position": "stackedBR"},
            {"element": "taskbar", "visible": true, "position": "centerMonitor"},
            {"element": "rightBox", "visible": true, "position": "stackedBR"},
            {"element": "systemMenu", "visible": true, "position": "stackedBR"},
            {"element": "dateMenu", "visible": true, "position": "stackedBR"},
            {"element": "desktopButton", "visible": true, "position": "stackedBR"},
        ]),
        Style::Classic => json!([
            {"element": "centerBox", "visible": true, "position": "stackedTL"},
            {"element": "taskbar", "visible": true, "position": "stackedTL"},
            {"element": "showAppsButton", "visible": false, "position": "stackedTL"},
            {"element": "activitiesButton", "visible": true, "position": "stackedBR"},
            {"element": "leftBox", "visible": true, "position": "stackedBR"},
            {"element": "rightBox", "visible": true, "position": "stackedBR"},
            {"element": "systemMenu", "visible": true, "position": "stackedBR"},
            {"element": "dateMenu", "visible": true, "position": "stackedBR"},
            {"element": "desktopButton", "visible": true, "position": "stackedBR"},
        ]),
    }
}

fn make_panel_element_positions(style: Style, monitors: &[String]) -> String {
    let elements = panel_elements(style);
    let mut map = Map::new();
    for monitor in monitors {
        map.insert(monitor.clone(), elements.clone());
    }
    Value::Object(map).to_string()
}

fn parse_json_object(raw: &str) -> Option<Map<String, Value>> {
    let trimmed = raw.trim();
    let unquoted = if trimmed.len() >= 2 && trimmed.starts_with('\'') && trimmed.ends_with('\'') {
        &trimmed[1..trimmed.len() - 1]
    } else {
        trimmed
    };
    let normalized = unquoted.replace('\'', "\"");
    serde_json::from_str::<Value>(&normalized)
        .ok()
        .and_then(|value| value.as_object().cloned())
}

pub fn detect_current_with(dconf: &dyn Dconf) -> (Style, Position) {
    let menu_layout = dconf.read(&format!("{ARC}/menu-layout"));
    let style = if menu_layout
        .as_deref()
        .is_some_and(|value| value.contains("arcmenu"))
    {
        let elements = dconf.read(&format!("{DTP}/panel-element-positions"));
        if elements
            .as_deref()
            .is_some_and(|value| value.contains("centerMonitor"))
        {
            Style::Seperated
        } else {
            Style::Classic
        }
    } else {
        Style::Eleven
    };

    let mut position = Position::Bottom;
    if let Some(panel_positions) = dconf.read(&format!("{DTP}/panel-positions")) {
        for candidate in Position::all() {
            if panel_positions.contains(candidate.dconf_value()) {
                position = candidate;
                break;
            }
        }
    }
    (style, position)
}

pub fn detect_current() -> (Style, Position) {
    detect_current_with(&CliDconf)
}

pub fn read_group_apps() -> bool {
    dconf_read(&format!("{DTP}/group-apps")).as_deref() != Some("false")
}

pub fn write_group_apps(enabled: bool) -> Result<(), ()> {
    CliDconf.write(
        &format!("{DTP}/group-apps"),
        if enabled { "true" } else { "false" },
    )
}

pub fn read_use_launchers() -> bool {
    dconf_read(&format!("{DTP}/group-apps-use-launchers")).as_deref() == Some("true")
}

pub fn write_use_launchers(enabled: bool) -> Result<(), ()> {
    CliDconf.write(
        &format!("{DTP}/group-apps-use-launchers"),
        if enabled { "true" } else { "false" },
    )
}

pub fn extension_enabled(uuid: &str) -> bool {
    dconf_read(&format!("{}/enabled-extensions", crate::config::SHELL_BASE))
        .is_some_and(|value| value.contains(uuid))
}

fn known_monitors(dconf: &dyn Dconf) -> Vec<String> {
    let mut monitors = vec!["0".into(), "1".into(), "2".into()];
    if let Some(raw) = dconf.read(&format!("{DTP}/panel-anchors")) {
        if let Some(anchors) = parse_json_object(&raw) {
            for monitor in anchors.keys() {
                if !monitors.iter().any(|existing| existing == monitor) {
                    monitors.push(monitor.clone());
                }
            }
        }
    }
    monitors
}

fn panel_sizes(dconf: &dyn Dconf, monitors: &[String]) -> String {
    let existing = dconf
        .read(&format!("{DTP}/panel-sizes"))
        .as_deref()
        .and_then(parse_json_object)
        .unwrap_or_default();
    let mut map = Map::new();
    for monitor in monitors {
        let size = existing
            .get(monitor)
            .and_then(Value::as_i64)
            .unwrap_or(48);
        map.insert(monitor.clone(), json!(size));
    }
    Value::Object(map).to_string()
}

pub fn calculate_menu_height(style: Style, screen_height: Option<i32>) -> i32 {
    let adaptive_height = match screen_height {
        None => menu_max_height(style),
        Some(screen_height) => {
            let height_range = (MAX_MENU_HEIGHT - MIN_MENU_HEIGHT) as f64;
            let screen_range = (MAX_SCREEN_HEIGHT - MIN_SCREEN_HEIGHT) as f64;
            let progress = (screen_height - MIN_SCREEN_HEIGHT) as f64 / screen_range;
            let adaptive = MIN_MENU_HEIGHT as f64 + height_range * progress;
            adaptive
                .round()
                .clamp(MIN_MENU_HEIGHT as f64, MAX_MENU_HEIGHT as f64) as i32
        }
    };
    adaptive_height.min(menu_max_height(style))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DconfOp {
    Write(String, String),
    Reset(String),
}

fn planned_ops(
    dconf: &dyn Dconf,
    style: Style,
    position: Position,
    screen_height: Option<i32>,
) -> Vec<DconfOp> {
    let (menu_layout, force_menu) = menu_config(style, position);
    let panel_position = position.dconf_value();
    let monitors = known_monitors(dconf);
    let element_positions = make_panel_element_positions(style, &monitors);
    let mut panel_positions_map = Map::new();
    for monitor in &monitors {
        panel_positions_map.insert(monitor.clone(), json!(panel_position));
    }
    let panel_positions = Value::Object(panel_positions_map).to_string();
    let panel_sizes = panel_sizes(dconf, &monitors);
    let menu_height = calculate_menu_height(style, screen_height);

    let mut ops = vec![
        DconfOp::Write(
            format!("{DTP}/dot-position"),
            format!("'{panel_position}'"),
        ),
        DconfOp::Write(
            format!("{DTP}/panel-positions"),
            format!("'{panel_positions}'"),
        ),
        DconfOp::Write(format!("{DTP}/panel-sizes"), format!("'{panel_sizes}'")),
        DconfOp::Write(
            format!("{DTP}/panel-element-positions"),
            format!("'{element_positions}'"),
        ),
        DconfOp::Write(format!("{ARC}/menu-height"), menu_height.to_string()),
        DconfOp::Write(
            format!("{ARC}/force-menu-location"),
            format!("'{force_menu}'"),
        ),
        DconfOp::Write(format!("{ARC}/menu-layout"), format!("'{menu_layout}'")),
    ];
    if style == Style::Eleven {
        ops.push(DconfOp::Reset(format!("{ARC}/menu-arrow-rise")));
        ops.push(DconfOp::Write(format!("{DTP}/group-apps"), "true".into()));
        ops.push(DconfOp::Write(
            format!("{DTP}/group-apps-use-launchers"),
            "true".into(),
        ));
    } else {
        ops.push(DconfOp::Write(
            format!("{ARC}/menu-arrow-rise"),
            "(true, -8)".into(),
        ));
    }
    ops
}

#[cfg(test)]
pub fn apply_with(
    dconf: &dyn Dconf,
    style: Style,
    position: Position,
    screen_height: Option<i32>,
) -> bool {
    let result: Result<(), ()> = (|| {
        for op in planned_ops(dconf, style, position, screen_height) {
            match op {
                DconfOp::Write(key, value) => dconf.write(&key, &value)?,
                DconfOp::Reset(key) => dconf.reset(&key)?,
            }
        }
        Ok(())
    })();
    result.is_ok()
}

fn ops_to_dump(ops: &[DconfOp]) -> String {
    let mut dtp = Vec::new();
    let mut arc = Vec::new();
    for op in ops {
        if let DconfOp::Write(key, value) = op {
            if let Some(name) = key.strip_prefix(&format!("{DTP}/")) {
                dtp.push(format!("{name}={value}"));
            } else if let Some(name) = key.strip_prefix(&format!("{ARC}/")) {
                arc.push(format!("{name}={value}"));
            }
        }
    }
    let mut dump = String::new();
    if !dtp.is_empty() {
        dump.push_str("[dash-to-panel]\n");
        dump.push_str(&dtp.join("\n"));
        dump.push('\n');
    }
    if !arc.is_empty() {
        if !dump.is_empty() {
            dump.push('\n');
        }
        dump.push_str("[arcmenu]\n");
        dump.push_str(&arc.join("\n"));
        dump.push('\n');
    }
    dump
}

fn dconf_load(dir: &str, dump: &str) -> Result<(), ()> {
    let mut child = Command::new("dconf")
        .args(["load", dir])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| ())?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(dump.as_bytes()).map_err(|_| ())?;
    }
    let status = child.wait().map_err(|_| ())?;
    if status.success() {
        Ok(())
    } else {
        Err(())
    }
}

fn apply_ops_batched(ops: &[DconfOp]) -> bool {
    let dump = ops_to_dump(ops);
    if !dump.is_empty() && dconf_load("/org/gnome/shell/extensions/", &dump).is_err() {
        return false;
    }
    for op in ops {
        if let DconfOp::Reset(key) = op {
            if CliDconf.reset(key).is_err() {
                return false;
            }
        }
    }
    true
}

pub fn apply_style_and_position(style: Style, position: Position) -> bool {
    apply_style_and_position_with_height(
        style,
        position,
        crate::display::smallest_monitor_height(),
    )
}

pub fn apply_style_and_position_with_height(
    style: Style,
    position: Position,
    screen_height: Option<i32>,
) -> bool {
    let ops = planned_ops(&CliDconf, style, position, screen_height);
    apply_ops_batched(&ops)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::HashMap;

    struct Fake {
        reads: HashMap<String, String>,
        log: RefCell<Vec<Vec<String>>>,
        fail_key: Option<String>,
    }

    impl Fake {
        fn with_defaults() -> Self {
            let mut reads = HashMap::new();
            reads.insert(format!("{DTP}/panel-anchors"), "{'DP-1': {}}".into());
            reads.insert(format!("{DTP}/panel-sizes"), "{'0': 52, 'DP-1': 60}".into());
            Self {
                reads,
                log: RefCell::new(Vec::new()),
                fail_key: None,
            }
        }

        fn writes(&self) -> Vec<Vec<String>> {
            self.log.borrow().clone()
        }
    }

    impl Dconf for Fake {
        fn read(&self, key: &str) -> Option<String> {
            self.log.borrow_mut().push(vec![
                "dconf".into(),
                "read".into(),
                key.into(),
            ]);
            self.reads.get(key).cloned()
        }

        fn write(&self, key: &str, value: &str) -> Result<(), ()> {
            if self.fail_key.as_deref() == Some(key) {
                return Err(());
            }
            self.log.borrow_mut().push(vec![
                "dconf".into(),
                "write".into(),
                key.into(),
                value.into(),
            ]);
            Ok(())
        }

        fn reset(&self, key: &str) -> Result<(), ()> {
            self.log.borrow_mut().push(vec![
                "dconf".into(),
                "reset".into(),
                key.into(),
            ]);
            Ok(())
        }
    }

    fn assert_write(log: &[Vec<String>], key: &str, value: &str) {
        assert!(
            log.iter()
                .any(|command| command == &["dconf".to_string(), "write".into(), key.into(), value.into()]),
            "missing write {key} = {value} in {log:?}"
        );
    }

    #[test]
    fn eleven_uses_650_height_and_windows_grouping() {
        let fake = Fake::with_defaults();
        assert!(apply_with(&fake, Style::Eleven, Position::Bottom, Some(1080)));
        let log = fake.writes();
        assert_write(&log, &format!("{ARC}/menu-height"), "650");
        assert_write(&log, &format!("{ARC}/menu-layout"), "'11'");
        assert!(log.iter().any(|command| {
            command == &["dconf".to_string(), "reset".into(), format!("{ARC}/menu-arrow-rise")]
        }));
        assert_write(&log, &format!("{DTP}/group-apps"), "true");
        assert_write(&log, &format!("{DTP}/group-apps-use-launchers"), "true");
    }

    #[test]
    fn classic_uses_785_height_without_overwriting_grouping() {
        let fake = Fake::with_defaults();
        assert!(apply_with(&fake, Style::Classic, Position::Bottom, Some(1080)));
        let log = fake.writes();
        assert_write(&log, &format!("{ARC}/menu-height"), "785");
        assert_write(&log, &format!("{ARC}/menu-layout"), "'arcmenu'");
        assert_write(&log, &format!("{ARC}/menu-arrow-rise"), "(true, -8)");
        assert!(!log.iter().any(|command| {
            command == &["dconf".to_string(), "write".into(), format!("{DTP}/group-apps"), "true".into()]
        }));
    }

    #[test]
    fn seperated_uses_classic_menu_height() {
        let fake = Fake::with_defaults();
        assert!(apply_with(&fake, Style::Seperated, Position::Bottom, Some(1080)));
        let log = fake.writes();
        assert_write(&log, &format!("{ARC}/menu-height"), "785");
        assert_write(&log, &format!("{ARC}/menu-arrow-rise"), "(true, -8)");
    }

    #[test]
    fn classic_menu_height_scales_with_screen_height() {
        assert_eq!(calculate_menu_height(Style::Classic, Some(600)), 650);
        assert_eq!(calculate_menu_height(Style::Classic, Some(768)), 650);
        assert_eq!(calculate_menu_height(Style::Classic, Some(800)), 664);
        assert_eq!(calculate_menu_height(Style::Classic, Some(900)), 707);
        assert_eq!(calculate_menu_height(Style::Classic, Some(1080)), 785);
        assert_eq!(calculate_menu_height(Style::Classic, Some(10000)), 785);
    }

    #[test]
    fn eleven_menu_height_always_stays_at_650() {
        assert_eq!(calculate_menu_height(Style::Eleven, Some(600)), 650);
        assert_eq!(calculate_menu_height(Style::Eleven, Some(900)), 650);
        assert_eq!(calculate_menu_height(Style::Eleven, Some(10000)), 650);
    }

    #[test]
    fn apply_uses_smallest_monitor_height() {
        let fake = Fake::with_defaults();
        assert!(apply_with(&fake, Style::Classic, Position::Bottom, Some(900)));
        assert_write(&fake.writes(), &format!("{ARC}/menu-height"), "707");
    }

    #[test]
    fn monitor_ids_and_existing_panel_sizes_are_preserved() {
        let fake = Fake::with_defaults();
        assert!(apply_with(&fake, Style::Classic, Position::Bottom, Some(1080)));
        let write = fake
            .writes()
            .into_iter()
            .find(|command| {
                command.len() == 4
                    && command[0] == "dconf"
                    && command[1] == "write"
                    && command[2] == format!("{DTP}/panel-sizes")
            })
            .unwrap();
        let sizes: Map<String, Value> = parse_json_object(&write[3]).unwrap();
        assert_eq!(sizes.get("0").and_then(Value::as_i64), Some(52));
        assert_eq!(sizes.get("DP-1").and_then(Value::as_i64), Some(60));
    }

    #[test]
    fn write_failure_is_reported() {
        let mut fake = Fake::with_defaults();
        fake.fail_key = Some(format!("{ARC}/menu-height"));
        assert!(!apply_with(&fake, Style::Eleven, Position::Bottom, Some(1080)));
    }

    #[test]
    fn real_dconf_json_sizes_are_parsed() {
        let parsed = parse_json_object("'{\"2\":48,\"GSM-0x0001d909\":48}'").unwrap();
        assert_eq!(parsed.get("2").and_then(Value::as_i64), Some(48));
    }

    #[test]
    fn batched_dump_groups_extension_keys() {
        let fake = Fake::with_defaults();
        let ops = planned_ops(&fake, Style::Classic, Position::Bottom, Some(1080));
        let dump = ops_to_dump(&ops);
        assert!(dump.contains("[dash-to-panel]"));
        assert!(dump.contains("[arcmenu]"));
        assert!(dump.contains("menu-layout='arcmenu'"));
        assert!(dump.contains("menu-height=785"));
        assert!(dump.contains("dot-position='BOTTOM'"));
        assert!(dump.contains("menu-arrow-rise=(true, -8)"));
    }
}
