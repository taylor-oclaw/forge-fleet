"""ForgeFleet Configuration — single source of truth. No hardcoding anywhere else.

All settings loaded from:
1. Environment variables (highest priority)
2. ~/.forgefleet/config.json (user config)
3. fleet.json (node discovery)
4. Defaults (lowest priority)

NOTHING should be hardcoded in any module. Import from here.
"""
import json
import os
from dataclasses import dataclass, field


def _load_config_file() -> dict:
    """Load ~/.forgefleet/config.json if it exists."""
    path = os.path.expanduser("~/.forgefleet/config.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except:
            pass
    return {}


def _load_fleet_json() -> dict:
    """Load fleet.json for node info."""
    for path in [
        os.path.expanduser("~/fleet.json"),
        os.path.expanduser("~/.openclaw/workspace/fleet.json"),
    ]:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except:
                pass
    return {}


_user_config = _load_config_file()
_fleet = _load_fleet_json()


# ─── Getters (env var → config file → default) ─────────

def get(key: str, default=None):
    """Get a config value. Priority: env var → config file → default."""
    env_key = f"FORGEFLEET_{key.upper()}"
    return os.environ.get(env_key, _user_config.get(key, default))


# ─── Core Settings ──────────────────────────────────────

# Mission Control
MC_URL = get("mc_url", "http://192.168.5.100:60002")

# Telegram notifications
TELEGRAM_CHAT_ID = get("telegram_chat_id", "8496613333")
TELEGRAM_CHANNEL = get("telegram_channel", "telegram")

# Default repo (can be overridden per-task)
DEFAULT_REPO = get("default_repo", os.getcwd())

# Node identity
NODE_NAME = get("node_name", os.uname().nodename.split(".")[0].lower())

# ForgeFleet ports
FORGEFLEET_PORT = int(get("port", "51820"))
ANNOUNCE_PORT = int(get("announce_port", "50099"))

# LLM scan ports
LLM_PORTS = [int(p) for p in get("llm_ports", "51800,51801,51802,51803").split(",")]

# Fleet data directory
DATA_DIR = get("data_dir", os.path.expanduser("~/.forgefleet"))

# Timeouts per tier
TIER_TIMEOUTS = {
    1: int(get("tier1_timeout", "120")),
    2: int(get("tier2_timeout", "300")),
    3: int(get("tier3_timeout", "600")),
    4: int(get("tier4_timeout", "900")),
}


def get_fleet_nodes() -> dict:
    """Get node info from fleet.json."""
    return _fleet.get("nodes", {})


def get_node_ip(node_name: str) -> str:
    """Get IP for a node name."""
    nodes = get_fleet_nodes()
    node = nodes.get(node_name, {})
    return node.get("ip", "")


def save_config(updates: dict):
    """Save updates to ~/.forgefleet/config.json."""
    config_path = os.path.expanduser("~/.forgefleet/config.json")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    
    existing = _load_config_file()
    existing.update(updates)
    
    with open(config_path, "w") as f:
        json.dump(existing, f, indent=2)
