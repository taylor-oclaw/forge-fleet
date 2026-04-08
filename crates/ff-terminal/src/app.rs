//! App state — the central state container for ForgeFleet Terminal.

use std::path::PathBuf;

use ff_agent::agent_loop::{AgentEvent, AgentSession, AgentSessionConfig};
use ff_agent::commands::CommandRegistry;

use crate::input::InputState;
use crate::messages::{DisplayMessage, render_user_message};

// ─── Port scheme (same on every node) ──────────────────────────────────────

/// ForgeFleet daemon port
pub const PORT_DAEMON: u16 = 51000;
/// LLM inference API port
pub const PORT_LLM: u16 = 51001;
/// Web UI port
pub const PORT_WEB: u16 = 51002;
/// WebSocket port
pub const PORT_WS: u16 = 51003;
/// Metrics/Prometheus port
pub const PORT_METRICS: u16 = 51004;

// ─── Main app state ────────────────────────────────────────────────────────

pub struct App {
    // Session
    pub config: AgentSessionConfig,
    pub session: Option<AgentSession>,
    pub commands: CommandRegistry,
    pub session_id: String,

    // Display
    pub messages: Vec<DisplayMessage>,
    pub input: InputState,
    pub is_running: bool,
    pub scroll_offset: u16,
    pub auto_scroll: bool,
    pub status: String,
    pub frame: u64,
    pub should_quit: bool,

    // Fleet
    pub fleet_nodes: Vec<FleetNode>,

    // Current model/token tracking
    pub current_model: String,
    pub tokens_used: usize,
    pub tokens_total: usize,
    pub turn: u32,

    // Project
    pub current_project: Option<ProjectInfo>,
    pub working_dir: PathBuf,

    // Sessions
    pub saved_sessions: Vec<SessionInfo>,
    pub active_session_index: usize,
}

/// A fleet node with its ForgeFleet daemon and model status.
#[derive(Debug, Clone)]
pub struct FleetNode {
    pub name: String,
    pub ip: String,
    /// Is the ForgeFleet daemon running on this node?
    pub daemon_online: bool,
    /// Models loaded on this node.
    pub models: Vec<NodeModel>,
}

/// A model running on a fleet node.
#[derive(Debug, Clone)]
pub struct NodeModel {
    pub name: String,
    pub port: u16,
    pub online: bool,
    pub context_window: usize,
    pub tokens_used: usize,
}

/// Current project info.
#[derive(Debug, Clone)]
pub struct ProjectInfo {
    pub id: String,
    pub name: String,
    pub path: PathBuf,
}

/// Saved session for switching.
#[derive(Debug, Clone)]
pub struct SessionInfo {
    pub id: String,
    pub name: String,
    pub project: Option<String>,
    pub message_count: usize,
    pub last_active: String,
}

impl App {
    pub fn new(config: AgentSessionConfig) -> Self {
        let working_dir = config.working_dir.clone();

        // Detect project from working directory
        let current_project = detect_project(&working_dir);

        Self {
            config,
            session: None,
            commands: CommandRegistry::new(),
            session_id: String::new(),

            messages: Vec::new(),
            input: InputState::new(),
            is_running: false,
            scroll_offset: 0,
            auto_scroll: true,
            status: "Ready".into(),
            frame: 0,
            should_quit: false,

            fleet_nodes: default_fleet_nodes(),

            current_model: "auto".into(),
            tokens_used: 0,
            tokens_total: 32_768,
            turn: 0,

            current_project,
            working_dir,

            saved_sessions: Vec::new(),
            active_session_index: 0,
        }
    }

    /// Process an agent event and update display.
    pub fn handle_event(&mut self, event: AgentEvent) {
        if let Some(display) = crate::messages::event_to_display(&event) {
            self.messages.push(display);
        }

        match &event {
            AgentEvent::TurnComplete { turn, .. } => {
                self.turn = *turn;
            }
            AgentEvent::TokenWarning { usage_pct, estimated_tokens, .. } => {
                self.tokens_used = *estimated_tokens;
                self.status = format!("Context: {usage_pct:.0}%");
            }
            AgentEvent::Done { .. } => {
                self.is_running = false;
                self.status = "Ready".into();
            }
            AgentEvent::Error { message, .. } => {
                self.is_running = false;
                self.status = format!("Error: {}", &message[..message.len().min(50)]);
            }
            AgentEvent::Status { message, .. } => {
                self.status = message.clone();
            }
            _ => {}
        }
    }

    /// Submit user input.
    pub fn submit_input(&mut self) {
        let text = self.input.submit();
        if text.is_empty() { return; }
        self.messages.push(render_user_message(&text));
        self.is_running = true;
        self.status = "Thinking...".into();
    }

    /// Get the spinner character for the current frame.
    pub fn spinner(&self) -> &'static str {
        const FRAMES: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
        FRAMES[(self.frame as usize / 2) % FRAMES.len()]
    }

    /// Total lines in the message pane.
    pub fn total_message_lines(&self) -> usize {
        self.messages.iter().map(|m| m.lines.len()).sum()
    }

    /// Get web UI URL for this machine.
    pub fn web_url(&self) -> String {
        format!("http://localhost:{}", PORT_WEB)
    }
}

/// Detect project from working directory (check for FORGEFLEET.md, Cargo.toml, package.json).
fn detect_project(dir: &std::path::Path) -> Option<ProjectInfo> {
    // Check for FORGEFLEET.md
    let ff_md = dir.join("FORGEFLEET.md");
    if ff_md.exists() {
        let name = dir.file_name()?.to_str()?.to_string();
        return Some(ProjectInfo {
            id: name.clone(),
            name,
            path: dir.to_path_buf(),
        });
    }

    // Check for Cargo.toml with package name
    let cargo = dir.join("Cargo.toml");
    if cargo.exists() {
        let name = dir.file_name()?.to_str()?.to_string();
        return Some(ProjectInfo {
            id: name.clone(),
            name,
            path: dir.to_path_buf(),
        });
    }

    // Check for package.json
    let pkg = dir.join("package.json");
    if pkg.exists() {
        let name = dir.file_name()?.to_str()?.to_string();
        return Some(ProjectInfo {
            id: name.clone(),
            name,
            path: dir.to_path_buf(),
        });
    }

    None
}

fn default_fleet_nodes() -> Vec<FleetNode> {
    vec![
        FleetNode {
            name: "Taylor".into(), ip: "192.168.5.100".into(), daemon_online: false,
            models: vec![
                NodeModel { name: "Gemma-4-31B".into(), port: 51000, online: false, context_window: 262_144, tokens_used: 0 },
                NodeModel { name: "Qwen3-Coder".into(), port: 51001, online: false, context_window: 32_768, tokens_used: 0 },
            ],
        },
        FleetNode {
            name: "Marcus".into(), ip: "192.168.5.102".into(), daemon_online: false,
            models: vec![
                NodeModel { name: "Qwen2.5-Coder-32B".into(), port: 51000, online: false, context_window: 32_768, tokens_used: 0 },
            ],
        },
        FleetNode {
            name: "Sophie".into(), ip: "192.168.5.103".into(), daemon_online: false,
            models: vec![
                NodeModel { name: "Qwen2.5-Coder-32B".into(), port: 51000, online: false, context_window: 32_768, tokens_used: 0 },
            ],
        },
        FleetNode {
            name: "Priya".into(), ip: "192.168.5.104".into(), daemon_online: false,
            models: vec![
                NodeModel { name: "Qwen2.5-Coder-32B".into(), port: 51000, online: false, context_window: 32_768, tokens_used: 0 },
            ],
        },
        FleetNode {
            name: "James".into(), ip: "192.168.5.108".into(), daemon_online: false,
            models: vec![
                NodeModel { name: "Qwen2.5-72B".into(), port: 51000, online: false, context_window: 32_768, tokens_used: 0 },
                NodeModel { name: "Qwen3.5-9B".into(), port: 51001, online: false, context_window: 32_768, tokens_used: 0 },
            ],
        },
        FleetNode {
            name: "Ace".into(), ip: "192.168.5.105".into(), daemon_online: false,
            models: vec![],
        },
    ]
}
