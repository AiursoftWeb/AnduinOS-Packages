use std::fmt;

/// Firewall default policy.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Policy {
    Allow,
    Deny,
    Reject,
}

impl Policy {
    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "allow" => Some(Self::Allow),
            "deny" | "drop" => Some(Self::Deny),
            "reject" => Some(Self::Reject),
            _ => None,
        }
    }

    pub fn as_ufw_arg(&self) -> &'static str {
        match self {
            Self::Allow => "allow",
            Self::Deny => "deny",
            Self::Reject => "reject",
        }
    }

    pub fn index(&self) -> u32 {
        match self {
            Self::Deny => 0,
            Self::Allow => 1,
            Self::Reject => 2,
        }
    }

    pub fn from_index(idx: u32) -> Self {
        match idx {
            0 => Self::Deny,
            1 => Self::Allow,
            2 => Self::Reject,
            _ => Self::Deny,
        }
    }
}

impl fmt::Display for Policy {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Allow => write!(f, "Allow"),
            Self::Deny => write!(f, "Deny"),
            Self::Reject => write!(f, "Reject"),
        }
    }
}

/// Rule action (superset of Policy, includes Limit).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Allow,
    Deny,
    Reject,
    Limit,
}

impl Action {
    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "allow" => Some(Self::Allow),
            "deny" => Some(Self::Deny),
            "reject" => Some(Self::Reject),
            "limit" => Some(Self::Limit),
            _ => None,
        }
    }

    pub fn as_ufw_arg(&self) -> &'static str {
        match self {
            Self::Allow => "allow",
            Self::Deny => "deny",
            Self::Reject => "reject",
            Self::Limit => "limit",
        }
    }

    pub fn index(&self) -> u32 {
        match self {
            Self::Deny => 0,
            Self::Allow => 1,
            Self::Reject => 2,
            Self::Limit => 3,
        }
    }

    pub fn from_index(idx: u32) -> Self {
        match idx {
            0 => Self::Deny,
            1 => Self::Allow,
            2 => Self::Reject,
            3 => Self::Limit,
            _ => Self::Deny,
        }
    }
}

impl fmt::Display for Action {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Allow => write!(f, "ALLOW"),
            Self::Deny => write!(f, "DENY"),
            Self::Reject => write!(f, "REJECT"),
            Self::Limit => write!(f, "LIMIT"),
        }
    }
}

/// Traffic direction.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    In,
    Out,
}

impl Direction {
    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "in" => Some(Self::In),
            "out" => Some(Self::Out),
            _ => None,
        }
    }

    pub fn as_ufw_arg(&self) -> &'static str {
        match self {
            Self::In => "in",
            Self::Out => "out",
        }
    }

    pub fn index(&self) -> u32 {
        match self {
            Self::In => 0,
            Self::Out => 1,
        }
    }
}

impl fmt::Display for Direction {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::In => write!(f, "IN"),
            Self::Out => write!(f, "OUT"),
        }
    }
}

/// Network protocol.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Protocol {
    Tcp,
    Udp,
    Both,
}

impl Protocol {
    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "tcp" => Some(Self::Tcp),
            "udp" => Some(Self::Udp),
            "any" | "both" | "" => Some(Self::Both),
            _ => None,
        }
    }

    pub fn as_ufw_arg(&self) -> Option<&'static str> {
        match self {
            Self::Tcp => Some("tcp"),
            Self::Udp => Some("udp"),
            Self::Both => None, // don't append /proto
        }
    }

    pub fn index(&self) -> u32 {
        match self {
            Self::Both => 0,
            Self::Tcp => 1,
            Self::Udp => 2,
        }
    }

    pub fn from_index(idx: u32) -> Self {
        match idx {
            0 => Self::Both,
            1 => Self::Tcp,
            2 => Self::Udp,
            _ => Self::Both,
        }
    }
}

/// A single UFW firewall rule.
#[derive(Debug, Clone)]
pub struct UfwRule {
    /// Rule number (1-indexed, for deletion).
    pub number: u32,
    /// Port, port range, or app name (e.g. "80", "443/tcp", "Nginx Full").
    pub port: String,
    /// The rule action.
    pub action: Action,
    /// Traffic direction.
    pub direction: Direction,
    /// Source address (e.g. "Anywhere", "192.168.1.0/24").
    pub from: String,
    /// Destination address.
    pub to: String,
    /// Whether this is an IPv6 rule.
    pub v6: bool,
}

impl UfwRule {
    /// Human-readable subtitle for display in the rule list.
    pub fn subtitle(&self) -> String {
        let dir = &self.direction;
        let action = &self.action;
        if self.from == "Anywhere" || self.from == "Anywhere (v6)" {
            format!("{action} {dir}")
        } else {
            format!("{action} {dir} from {}", self.from)
        }
    }

    /// Display title (port + v6 indicator).
    pub fn title(&self) -> String {
        if self.v6 {
            format!("{} (v6)", self.port)
        } else {
            self.port.clone()
        }
    }
}

/// Complete UFW status snapshot.
#[derive(Debug, Clone)]
pub struct UfwStatus {
    /// Whether the firewall is active.
    pub active: bool,
    /// Default policy for incoming traffic.
    pub default_incoming: Policy,
    /// Default policy for outgoing traffic.
    pub default_outgoing: Policy,
    /// Currently configured rules.
    pub rules: Vec<UfwRule>,
    /// Logging level (e.g. "off", "low", "medium", "high", "full").
    pub logging: String,
}

impl Default for UfwStatus {
    fn default() -> Self {
        Self {
            active: false,
            default_incoming: Policy::Deny,
            default_outgoing: Policy::Allow,
            rules: Vec::new(),
            logging: "off".to_string(),
        }
    }
}

/// An application profile from /etc/ufw/applications.d/.
#[derive(Debug, Clone)]
pub struct AppProfile {
    /// Profile name (section header, e.g. "Nginx Full").
    pub name: String,
    /// Human-readable title.
    pub title: String,
    /// Description of the application.
    pub description: String,
    /// Port specification (e.g. "80,443/tcp").
    pub ports: String,
}

/// Parameters for adding a new firewall rule.
#[derive(Debug, Clone)]
pub struct RuleParams {
    /// Port number, range, or service name.
    pub port: String,
    /// Rule action.
    pub action: Action,
    /// Traffic direction (None = both/default In).
    pub direction: Option<Direction>,
    /// Protocol (None = both).
    pub protocol: Option<Protocol>,
    /// Source IP/subnet (None = Anywhere).
    pub from: Option<String>,
    /// Destination IP/subnet (None = any).
    pub to: Option<String>,
    /// Network interface to bind to (e.g. "eth0").
    pub interface: Option<String>,
    /// Rule comment for documentation.
    pub comment: Option<String>,
    /// Insert before rule number (None = append).
    pub insert_position: Option<u32>,
}

/// Error type for UFW operations.
#[derive(Debug, Clone)]
pub struct UfwError {
    pub message: String,
}

impl fmt::Display for UfwError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for UfwError {}

impl From<String> for UfwError {
    fn from(message: String) -> Self {
        Self { message }
    }
}

impl From<std::io::Error> for UfwError {
    fn from(e: std::io::Error) -> Self {
        Self {
            message: e.to_string(),
        }
    }
}
