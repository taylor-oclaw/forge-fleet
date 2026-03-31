"""Fleet Router — concurrent multi-node execution with automatic escalation.

Loads model inventory from canonical ForgeFleet config (fleet.toml)
and falls back to network discovery only when no configured models exist.
"""
import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional
from .llm import LLM
from .scheduling_policy import SchedulingPolicy, TaskRequirements
from .. import config


@dataclass
class ModelEndpoint:
    """A model running on a specific node."""
    name: str
    node: str
    ip: str
    port: int
    tier: int  # 1=9B, 2=32B, 3=72B, 4=235B
    url: str = ""
    busy: bool = False
    healthy: bool = True
    last_check: float = 0
    
    def __post_init__(self):
        if not self.url:
            self.url = f"http://{self.ip}:{self.port}"


@dataclass
class FleetRouter:
    """Routes tasks to the best available model across the fleet.
    
    Current behavior:
    - prefers canonical fleet.toml model inventory
    - falls back to discovery only if no configured models are present
    - still supports health/busy checks and tier escalation

    Next evolution:
    - capability-aware filtering
    - preference-aware scoring
    - speed-to-completion selection
    - ownership/handoff awareness
    """

    policy: SchedulingPolicy = field(default_factory=SchedulingPolicy)
    endpoints: list = field(default_factory=list)
    tiers: dict = field(default_factory=dict)  # tier_num -> [endpoints]
    _loaded: bool = False
    
    def __post_init__(self):
        self._load_from_config()

        # If no configured models found, use network discovery fallback
        if not self.endpoints:
            self._discover_models()
    
    def _load_from_config(self):
        """Load nodes/models from canonical ForgeFleet config (fleet.toml)."""
        models = config.get_all_models()
        for model in models:
            ep = ModelEndpoint(
                name=model.get("name", model.get("key", "unknown")),
                node=model.get("node", "unknown"),
                ip=model.get("ip", "127.0.0.1"),
                port=model.get("port", 55000),
                tier=model.get("tier", 1),
            )
            self.endpoints.append(ep)
            self.tiers.setdefault(ep.tier, []).append(ep)
        if models:
            self._loaded = True
    
    def _discover_models(self):
        """Fall back to network discovery when no config/legacy model listings exist."""
        from .discovery import NetworkDiscovery
        disc = NetworkDiscovery()
        discovered = disc.scan_known_hosts()
        
        for ep in discovered:
            model_ep = ModelEndpoint(
                name=ep.model_name,
                node=ep.hostname or ep.ip,
                ip=ep.ip,
                port=ep.port,
                tier=ep.tier,
            )
            self.endpoints.append(model_ep)
            self.tiers.setdefault(model_ep.tier, []).append(model_ep)
        
        if discovered:
            self._loaded = True
    
    def check_health(self, ep: ModelEndpoint) -> bool:
        """Check if endpoint is reachable."""
        try:
            req = urllib.request.Request(f"{ep.url}/health")
            with urllib.request.urlopen(req, timeout=3) as resp:
                ep.healthy = resp.status == 200
        except Exception:
            ep.healthy = False
        ep.last_check = time.time()
        return ep.healthy
    
    def check_busy(self, ep: ModelEndpoint) -> bool:
        """Check if endpoint is currently processing a request."""
        try:
            req = urllib.request.Request(f"{ep.url}/slots")
            with urllib.request.urlopen(req, timeout=3) as resp:
                slots = json.loads(resp.read())
                if isinstance(slots, list):
                    ep.busy = any(s.get("is_processing", False) for s in slots)
                else:
                    ep.busy = False
        except Exception:
            ep.busy = False  # Assume not busy if we can't check
        return ep.busy
    
    def get_available(self, tier: int, requirements: TaskRequirements | None = None) -> list[ModelEndpoint]:
        """Get available (healthy + not busy) endpoints for a tier filtered by eligibility."""
        requirements = requirements or TaskRequirements()
        candidates = self.tiers.get(tier, [])
        available = []
        
        for ep in candidates:
            # Re-check if stale (>30s since last check)
            if time.time() - ep.last_check > 30:
                self.check_health(ep)
                if ep.healthy:
                    self.check_busy(ep)
            
            if ep.healthy and not ep.busy:
                ok, _reason = self.policy.node_eligible(ep.node, requirements)
                if ok:
                    available.append(ep)
        
        return available
    
    def get_llm(self, tier: int, fallback_up: bool = True,
                requirements: TaskRequirements | None = None,
                current_loads: dict | None = None) -> Optional[LLM]:
        """Get an LLM for the requested tier, escalating if needed.

        First-pass smart routing:
        - eligibility filtering
        - preference/resource scoring
        """
        requirements = requirements or TaskRequirements()
        current_loads = current_loads or {}

        def pick_best(endpoints: list[ModelEndpoint]) -> Optional[ModelEndpoint]:
            if not endpoints:
                return None
            ranked = sorted(
                endpoints,
                key=lambda ep: self.policy.score_node(ep.node, requirements, current_loads.get(ep.node, {})),
                reverse=True,
            )
            return ranked[0]

        # Try requested tier first
        available = self.get_available(tier, requirements=requirements)
        ep = pick_best(available)
        if ep:
            return LLM(
                base_url=f"{ep.url}/v1",
                model=ep.name,
                timeout=900 if tier >= 3 else 300,
            )
        
        # Escalate to higher tiers if allowed
        if fallback_up:
            for higher_tier in range(tier + 1, 5):
                available = self.get_available(higher_tier, requirements=requirements)
                ep = pick_best(available)
                if ep:
                    return LLM(
                        base_url=f"{ep.url}/v1",
                        model=ep.name,
                        timeout=900 if higher_tier >= 3 else 300,
                    )
        
        return None
