# ForgeFleet Architecture

## Design Principles

1. **Smallest model first** — 90% of tasks complete on the fast local model
2. **Each tier builds on previous work** — not fresh start
3. **Fleet-aware** — try localhost first, then remote nodes
4. **One source of truth** — fleet.json drives everything
5. **Tools over text** — models use file_read/shell, not raw prompts
6. **Learn from failures** — shared memory across agents

## Layers

### Layer 1: Context Engine
- CocoIndex-Code: AST-based search finds relevant code
- Aider repo-map: understands full project structure
- Output: relevant file contents + function signatures for prompt

### Layer 2: Routing Engine  
- Reads fleet.json tiered_pipeline config
- Checks /slots on all fleet nodes for availability
- Routes to smallest available model that can handle the task
- Multi-step: different model per step

### Layer 3: Agent Loop
- Strands SDK provides tool-calling agent loop
- Tools: file_read, file_write, shell, editor
- Model calls tools → agent.py executes → feeds output back
- Loop until task complete or max iterations

### Layer 4: Fleet Coordination
- Git branches as shared workspace between tiers
- Tier 1 pushes → Tier 2 pulls + continues
- Mission Control tracks tickets, assignments, status
- Strands A2A for agent-to-agent communication

### Layer 5: Shared Memory
- SQLite on each node + periodic sync
- Stores: error patterns, successful code patterns, model performance
- Agents check memory before starting: "has this been tried before?"

### Layer 6: Verification
- cargo check after every file write
- cargo test after implementation complete
- If fails → feed error back to model → iterate
