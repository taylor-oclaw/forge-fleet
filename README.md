# ForgeFleet

**Distributed AI Coding Agent Orchestration System**

ForgeFleet turns a fleet of commodity machines with local LLMs into a production-grade software engineering team. Each agent can read files, write code, run tests, and iterate — using the best tool for each job.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    ForgeFleet                         │
├──────────┬──────────┬──────────┬──────────┬─────────┤
│ Context  │ Routing  │  Agent   │  Fleet   │ Memory  │
│ Engine   │ Engine   │  Loop    │  Coord   │ Store   │
├──────────┼──────────┼──────────┼──────────┼─────────┤
│CocoIndex │ 10x/     │ Strands  │ agent.py │ SQLite  │
│ Aider    │ graniet  │ mini-SWE │ git+GH   │ Strands │
│ repo-map │ fleet.json│ Crush   │ MC API   │ Mem0    │
├──────────┴──────────┴──────────┴──────────┴─────────┤
│            Local LLMs (llama.cpp)                     │
│  Qwen3.5-9B │ Qwen2.5-Coder-32B │ Qwen2.5-72B │ 235B │
├──────────────┴───────────────────┴─────────────┴──────┤
│              6 Physical Machines                       │
│  Taylor(96GB) James(64GB) Marcus Sophie Priya Ace     │
└───────────────────────────────────────────────────────┘
```

## Core Concept: Tiered Build Pipeline

Small models scaffold fast → bigger models fill in quality → biggest models finish complex parts.

```
Tier 1: Qwen3.5-9B (fast scaffold)
  ↓ commits + pushes branch
Tier 2: Qwen2.5-Coder-32B (quality code)
  ↓ reads Tier 1's work, fills in implementations
Tier 3: Qwen2.5-72B (complex logic)
  ↓ reads Tier 2's work, finishes hard parts
Tier 4: Qwen3-235B (hardest tasks)
  ↓ cluster inference for architecture-level decisions
Tier 5: Codex (paid fallback)
Tier 6: Human (breaks into smaller tickets)
```

Each tier builds on the previous tier's work via git branches.
Fleet-aware load balancing routes to idle models across the network.

## Components (extracted from best-in-class tools)

| Component | Source | What we extract |
|-----------|--------|----------------|
| Agent Loop | Strands SDK | file_read/write, shell, llamacpp.py provider |
| Code Understanding | Aider | repo-map, diff-based editing |
| Token Optimization | CocoIndex | AST search, 70% token savings |
| Model Routing | 10x + graniet/llm | multi-step pipelines, parallel eval |
| Fleet Coordination | agent.py + Strands A2A | cross-machine task handoff |
| Shared Memory | graniet/llm + SQLite | error patterns, success patterns |
| API Proxy | graniet/llm REST | serve local models as OpenAI API |
| LSP Intelligence | Crush | code structure understanding |
| CI/Verify | Aider lint/test | cargo check + test integration |
| Swarm Coordination | Ruflo | multi-agent parallel work |

## Getting Started

```bash
pip install forgefleet
forgefleet init  # reads fleet.json, discovers models
forgefleet run "Build billing subscription CRUD"  # dispatches to fleet
```

## License

Apache-2.0
