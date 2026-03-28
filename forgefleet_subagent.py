"""ForgeFleet Sub-Agent — runs on remote nodes."""
import sys, os, time, signal, subprocess

sys.path.insert(0, os.path.expanduser("~/taylorProjects/forge-fleet"))

from forgefleet.engine.seniority import SeniorityPipeline
from forgefleet.engine.fleet_router import FleetRouter
from forgefleet.engine.mc_client import MCClient
from forgefleet.engine.tool import Tool
from forgefleet.engine.git_ops import GitOps

NODE = os.uname().nodename.split(".")[0].lower()
router = FleetRouter()
mc = MCClient(base_url="http://192.168.5.100:60002")
mc.node_name = NODE
repo = os.path.expanduser("~/taylorProjects/HireFlow360")
git = GitOps(repo)

running = True
def stop(s, f):
    global running
    running = False
signal.signal(signal.SIGTERM, stop)

print(f"Sub-agent {NODE}: {len(router.endpoints)} endpoints", flush=True)

def rf(filepath=""):
    f = os.path.join(repo, filepath)
    if not os.path.exists(f): return f"Not found: {filepath}"
    c = open(f).read()
    return c[:4000] if len(c) > 4000 else c

def lf(directory=".", pattern=""):
    full = os.path.join(repo, directory)
    exclude = {"target", "node_modules", ".git", "dist", ".next", "__pycache__"}
    files = []
    for r, d, fn in os.walk(full):
        d[:] = [x for x in d if x not in exclude]
        for f in fn:
            if pattern and not f.endswith(pattern): continue
            files.append(os.path.relpath(os.path.join(r, f), repo))
        if len(files) > 30: break
    return "\n".join(files[:30])

def wf(filepath="", content=""):
    if filepath.startswith("src/") and not filepath.startswith("src/app"):
        return "REJECTED: write to rust-backend/crates/ not src/"
    f = os.path.join(repo, filepath)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    open(f, "w").write(content)
    return f"WRITTEN: {filepath} ({len(content)} chars)"

def rc(command=""):
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60, cwd=repo)
        return (r.stdout + r.stderr)[:3000]
    except Exception as e:
        return str(e)

tools = [
    Tool(name="read_file", description="Read a file", parameters={"type": "object", "properties": {"filepath": {"type": "string"}}, "required": ["filepath"]}, func=rf),
    Tool(name="list_files", description="List files", parameters={"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}, func=lf),
    Tool(name="write_file", description="Write file. Rust goes in rust-backend/crates/", parameters={"type": "object", "properties": {"filepath": {"type": "string"}, "content": {"type": "string"}}, "required": ["filepath", "content"]}, func=wf),
    Tool(name="run_command", description="Run command", parameters={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}, func=rc),
]

tech = {"backend": "Rust+Axum", "frontend": "Next.js+React+TypeScript", "database": "PostgreSQL"}

end_time = time.time() + 36000  # 10 hours
done = 0
fail = 0

while running and time.time() < end_time:
    try:
        tickets = mc.get_claimable()
        buildable = [t for t in tickets if not any(s in t.get("title", "") for s in ["ForgeFleet", "Research", "[EPIC]", "[FEATURE]", "[CRITICAL]"])]
        
        if not buildable:
            time.sleep(60)
            continue
        
        ticket = buildable[0]
        tid = ticket["id"]
        title = ticket["title"]
        mc.claim_ticket(tid)
        print(f"[{NODE}] Building: {title[:50]}", flush=True)
        
        pipeline = SeniorityPipeline(tools=tools, router=router)
        result = pipeline.execute(ticket.get("description", title), tech_stack=tech)
        
        branch = f"feat/hf-{NODE}-{tid[:8]}"
        git.create_branch(branch)
        if git.has_changes():
            git.stage_all()
            git.commit(f"feat: {title[:50]} [{NODE}]")
            push = git.push(branch)
            if push.success:
                mc.update_ticket(tid, "ready_for_review", result=f"Built by {NODE}")
                done += 1
                print(f"[{NODE}] ✅ Done → {branch}", flush=True)
                # Notify via Telegram
                try:
                    subprocess.run(["ssh", "192.168.5.100", f"openclaw message send --target 8496613333 --channel telegram --message '✅ [{NODE}] Built: {title[:40]} → {branch}' --silent"], capture_output=True, timeout=15)
                except: pass
            else:
                mc.update_ticket(tid, "todo")
                fail += 1
        else:
            mc.update_ticket(tid, "todo")
            fail += 1
            print(f"[{NODE}] ⚠️ No changes", flush=True)
            # Notify failure
            try:
                subprocess.run(["ssh", "192.168.5.100", f"openclaw message send --target 8496613333 --channel telegram --message '❌ [{NODE}] No changes: {title[:40]}' --silent"], capture_output=True, timeout=15)
            except: pass
        
        git._run("checkout", "main")
        git._run("pull", "origin", "main")
        
    except Exception as e:
        print(f"[{NODE}] ❌ {e}", flush=True)
        fail += 1
        time.sleep(60)

print(f"[{NODE}] Done: {done}✅ {fail}❌", flush=True)
