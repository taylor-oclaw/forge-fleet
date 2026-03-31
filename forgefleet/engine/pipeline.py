"""Full Engineering Pipeline — the 6-step process for every ticket.

1. Context Gathering — understand the project, related tickets, tech stack
2. Planning — game plan, research, model selection
3. Multi-Perspective Pre-Review — roles analyze the plan BEFORE building
4. Build — decompose, code, test, fix
5. Multi-Perspective Post-Review — roles verify AFTER building
6. Completion — commit, push, unblock dependents
"""
import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .llm import LLM
from .tool import Tool
from .fleet_router import FleetRouter
from .mc_client import MCClient
from .git_ops import GitOps
from .roles import Role, PRE_BUILD_ROLES, POST_BUILD_ROLES
from .repo_map import RepoMap
from .evolution import EvolutionEngine, TaskRecord
from .context_store import ContextStore
from .ownership import OwnershipManager
from .lifecycle_policy import LifecyclePolicy, MergeContext
from .mcp_topology import MCPTopology
from .openclaw_bridge import OpenClawBridge
from .. import config


logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of the full pipeline for one ticket."""
    ticket_id: str
    title: str
    success: bool = False
    phase_results: dict = field(default_factory=dict)
    files_changed: list = field(default_factory=list)
    branch: str = ""
    total_time: float = 0
    pre_review_issues: list = field(default_factory=list)
    post_review_issues: list = field(default_factory=list)
    prerequisite_tickets: list = field(default_factory=list)
    unblocked_tickets: list = field(default_factory=list)
    done_state: str = ""
    final_state: str = ""
    auto_merge_reason: str = ""
    execution_retries: int = 0
    review_loops: int = 0
    topology: dict = field(default_factory=dict)


class EngineeringPipeline:
    """The full 6-step engineering pipeline.
    
    Every ticket goes through all 6 steps.
    Each step uses the right LLM tier and runs perspectives in parallel.
    """
    
    def __init__(self, repo_dir: str, mc_url: str = "",
                 ownership: OwnershipManager | None = None):
        self.base_repo_dir = os.path.abspath(repo_dir)
        self.router = FleetRouter()
        self.mc = MCClient(base_url=mc_url or config.get_mc_url())
        self.evolution = EvolutionEngine()
        self.context_store = ContextStore()
        self.ownership = ownership
        self.lifecycle = LifecyclePolicy()
        self.topology = MCPTopology.from_config()
        self._set_repo_dir(self.base_repo_dir)

    def _set_repo_dir(self, repo_dir: str):
        """Switch active repo context (base checkout or per-ticket worktree)."""
        self.repo_dir = os.path.abspath(repo_dir)
        self.git = GitOps(self.repo_dir)
        self.repo_map = RepoMap(self.repo_dir)
        self.tools = self._build_tools()

    def _safe_ticket_token(self, ticket_id: str) -> str:
        token = "".join(ch for ch in str(ticket_id) if ch.isalnum())
        return (token or "ticket")[:12]

    def _run_base_git(self, *args, timeout: int = 60):
        return GitOps(self.base_repo_dir)._run(*args, timeout=timeout)

    def _create_ticket_worktree(self, ticket_id: str) -> tuple[str, str, str]:
        """Create an isolated git worktree for a ticket execution."""
        token = self._safe_ticket_token(ticket_id)
        branch = f"feat/forgefleet-{token}"
        worktree_root = tempfile.mkdtemp(prefix=f"forgefleet-{token}-")
        worktree_dir = os.path.join(worktree_root, "repo")

        self._run_base_git("fetch", "origin", "main", timeout=90)
        add_result = self._run_base_git(
            "worktree", "add", "-B", branch, worktree_dir, "origin/main", timeout=90
        )
        if not add_result.success:
            add_result = self._run_base_git(
                "worktree", "add", "-B", branch, worktree_dir, "main", timeout=90
            )

        if not add_result.success:
            shutil.rmtree(worktree_root, ignore_errors=True)
            raise RuntimeError(f"Failed to create worktree: {add_result.error or add_result.output}")

        return branch, worktree_root, worktree_dir

    def _cleanup_ticket_worktree(self, worktree_root: str, worktree_dir: str):
        """Clean up per-ticket worktree after execution."""
        if worktree_dir:
            remove_result = self._run_base_git("worktree", "remove", "--force", worktree_dir, timeout=90)
            if not remove_result.success and "not a working tree" not in (remove_result.error or ""):
                logger.warning(
                    "Failed to remove worktree %s: %s",
                    worktree_dir,
                    remove_result.error or remove_result.output,
                )

        self._run_base_git("worktree", "prune", timeout=30)
        if worktree_root:
            shutil.rmtree(worktree_root, ignore_errors=True)

    def _renew_lease(self, ticket_id: str, stage: str):
        if not self.ownership:
            return
        ok, reason = self.ownership.renew_lease(ticket_id)
        if not ok and reason != "no_task":
            logger.warning("Lease renewal failed for %s at %s: %s", ticket_id, stage, reason)

    def _handle_human_escalation(self, ticket_id: str, title: str,
                                 description: str, escalation_reason: str,
                                 branch: str = ""):
        """Trigger real human escalation workflow (Telegram + MC review item)."""
        review_message = (
            "🚨 ForgeFleet escalation reached HUMAN review\n\n"
            f"Ticket: {title}\n"
            f"Ticket ID: {ticket_id}\n"
            f"Reason: {escalation_reason}\n"
            f"Branch: {branch or 'n/a'}"
        )

        try:
            OpenClawBridge().send_message(review_message)
        except Exception as exc:
            logger.warning("Failed to send human escalation Telegram notification: %s", exc)

        review_description = (
            f"Human escalation required for ticket {ticket_id}.\n\n"
            f"Reason: {escalation_reason}\n"
            f"Branch: {branch or 'n/a'}\n\n"
            f"Original description:\n{description[:1500]}"
        )

        existing_review = None
        for ticket in self.mc.get_tickets(limit=200):
            if ticket.get("parent_id") == ticket_id and "[review]" in ticket.get("title", "").lower():
                existing_review = ticket
                break

        if existing_review:
            self.mc.update_ticket(
                existing_review["id"],
                "ready_for_review",
                result=review_description,
                branch=branch,
            )
        else:
            self.mc.create_review_ticket(
                original_ticket_id=ticket_id,
                branch=branch,
                title=title,
                description=review_description,
            )

        self.mc.update_ticket(ticket_id, "ready_for_review", result=review_description, branch=branch)

    def execute(self, ticket: dict) -> PipelineResult:
        """Execute the full pipeline for a ticket."""
        tid = ticket["id"]
        title = ticket.get("title", "")
        desc = ticket.get("description", title)
        task_type = self._detect_task_type(desc)

        result = PipelineResult(ticket_id=tid, title=title)
        start = time.time()
        branch = ""
        worktree_root = ""
        worktree_dir = ""

        print(f"\n{'='*60}", flush=True)
        print(f"🎯 Pipeline: {title[:60]}", flush=True)

        try:
            branch, worktree_root, worktree_dir = self._create_ticket_worktree(tid)
            self._set_repo_dir(worktree_dir)
            result.branch = branch
            self._renew_lease(tid, "worktree_ready")

            topology_validation = self._validate_runtime_topology()
            result.topology = topology_validation
            result.phase_results["topology"] = topology_validation
            if not topology_validation.get("can_proceed", True):
                result.final_state = self.lifecycle.failure_state(blocked=True)
                result.phase_results["error"] = topology_validation.get("summary", "MCP topology blocked execution")
                print(f"  ⛔ {topology_validation.get('summary', 'MCP topology blocked execution')}", flush=True)
                return self._finalize_result(result, task_type=task_type, start_time=start)
            if topology_validation.get("degraded"):
                print(f"  ⚠️ {topology_validation.get('summary', 'MCP topology degraded')}", flush=True)

            # Step 1: CONTEXT GATHERING
            self._renew_lease(tid, "context")
            print(f"\n📚 Step 1: Context Gathering", flush=True)
            context = self._gather_context(ticket)
            result.phase_results["context"] = context
            self._record_stage_model(tid, "context_gathering")

            # Step 2: PLANNING
            self._renew_lease(tid, "planning")
            print(f"\n📋 Step 2: Planning", flush=True)
            plan = self._create_plan(ticket, context)
            result.phase_results["plan"] = plan
            self._record_stage_model(tid, "planning")

            # Step 3: PRE-BUILD MULTI-PERSPECTIVE REVIEW
            self._renew_lease(tid, "pre_review")
            print(f"\n🔍 Step 3: Pre-Build Review ({len(PRE_BUILD_ROLES)} perspectives)", flush=True)
            pre_issues = self._multi_perspective_review(plan, PRE_BUILD_ROLES, "pre")
            result.pre_review_issues = pre_issues
            result.phase_results["pre_review"] = pre_issues
            self._record_stage_model(tid, "pre_review")

            # Add pre-build reviewers as contributors
            if self.ownership:
                for role in PRE_BUILD_ROLES:
                    self.ownership.add_contributor(tid, role.name if hasattr(role, 'name') else str(role))

            # Check for prerequisites
            prereqs = [
                i for i in pre_issues
                if "prerequisite" in i.lower() or "dependency" in i.lower() or "blocked" in i.lower()
            ]
            if prereqs:
                result.prerequisite_tickets = self._create_prerequisite_tickets(tid, prereqs)
                print(f"  ⚠️ Created {len(result.prerequisite_tickets)} prerequisite tickets", flush=True)

            # Step 4: BUILD
            self._renew_lease(tid, "build")
            print(f"\n🔨 Step 4: Build", flush=True)
            build_result = self._build_with_retry(tid, desc, context, plan, result)
            result.phase_results["build"] = build_result
            self._record_stage_model(tid, "build")

            tests_passed = self._tests_passed(build_result)
            if not tests_passed:
                result.final_state = self.lifecycle.failure_state(failed_test=True)
                self.mc.fail_ticket(tid, "Build/test stage did not produce a passing result")
                print(f"  ❌ Build/test stage did not pass lifecycle policy", flush=True)
                return self._finalize_result(result, task_type=task_type, start_time=start)

            # Step 5: POST-BUILD MULTI-PERSPECTIVE REVIEW
            if self.git.has_changes():
                self._renew_lease(tid, "post_review")
                print(f"\n🔬 Step 5: Post-Build Review ({len(POST_BUILD_ROLES)} perspectives)", flush=True)
                post_issues = self._run_post_build_review(tid, desc, context, plan, result)
                result.post_review_issues = post_issues
                result.phase_results["post_review"] = post_issues

                # Add reviewers to ownership tracking
                if self.ownership:
                    for role in POST_BUILD_ROLES:
                        self.ownership.add_reviewer(tid, role.name if hasattr(role, 'name') else str(role))

                if post_issues:
                    result.final_state = self.lifecycle.failure_state(failed_review=True)
                    self.mc.fail_ticket(
                        tid,
                        f"Post-review still found issues after {result.review_loops} retry loops",
                    )
                    print(
                        f"  ❌ Post-review found blocking issues after {result.review_loops} retry loops",
                        flush=True,
                    )
                    return self._finalize_result(result, task_type=task_type, start_time=start)

                # Step 6: COMPLETION
                self._renew_lease(tid, "completion")
                print(f"\n✅ Step 6: Completion", flush=True)
                completion = self._complete_execution(
                    tid=tid,
                    title=title,
                    desc=desc,
                    branch=branch,
                    task_type=task_type,
                    tests_passed=tests_passed,
                    review_passed=not post_issues,
                    result=result,
                    start_time=start,
                )
                result.phase_results["completion"] = completion
                result.success = completion.get("success", False)
                result.done_state = completion.get("done_state", "")
                result.final_state = completion.get("final_state", result.final_state)
                result.auto_merge_reason = completion.get("auto_merge_reason", "")
                if result.success and result.unblocked_tickets:
                    print(f"  🔓 Unblocked {len(result.unblocked_tickets)} dependent tickets", flush=True)
            else:
                result.final_state = self.lifecycle.failure_state(execution_failed=True)
                self.mc.fail_ticket(tid, "No code changes produced")
                print(f"  ⚠️ No changes produced", flush=True)

        except Exception as e:
            result.phase_results["error"] = str(e)
            if not result.final_state:
                result.final_state = self.lifecycle.failure_state(execution_failed=True)
            self.mc.fail_ticket(tid, str(e)[:500])
            print(f"  ❌ Pipeline error: {e}", flush=True)
            # Escalation trigger: if pipeline fails, escalate ownership
            if self.ownership:
                task = self.ownership.get_task(tid)
                if task and task.owner_level != "human":
                    ok, reason = self.ownership.escalate(tid)
                    if ok:
                        print(f"  ⬆️ Escalated to {reason}", flush=True)
                        if reason.endswith("human"):
                            self._handle_human_escalation(
                                ticket_id=tid,
                                title=title,
                                description=desc,
                                escalation_reason=reason,
                                branch=branch,
                            )
        finally:
            self._set_repo_dir(self.base_repo_dir)
            self._cleanup_ticket_worktree(worktree_root, worktree_dir)

        return self._finalize_result(result, task_type=task_type, start_time=start)

    def _finalize_result(self, result: PipelineResult, task_type: str,
                         start_time: float) -> PipelineResult:
        """Finalize timing/evolution bookkeeping exactly once."""
        if result.phase_results.get("_finalized"):
            return result

        result.total_time = time.time() - start_time
        self.evolution.record_task(TaskRecord(
            task_id=result.ticket_id,
            title=result.title,
            task_type=task_type,
            total_time=result.total_time,
            success=result.success,
            pushed=result.success,
            error=result.phase_results.get("error", ""),
        ))
        result.phase_results["_finalized"] = True
        return result

    def _validate_runtime_topology(self) -> dict:
        """Validate MCP runtime links for the active ForgeFleet flow."""
        validation = self.topology.validate(current_service="forgefleet")
        return validation.to_dict()

    def _build_with_retry(self, ticket_id: str, description: str,
                          context: dict, plan: str,
                          result: PipelineResult) -> dict:
        """Run build stage with lifecycle retry limits."""
        attempt = 0
        last_result = {"success": False, "error": "build_not_started"}

        while True:
            self._renew_lease(ticket_id, f"build_attempt_{attempt}")
            try:
                last_result = self._build(description, context, plan)
            except Exception as e:
                last_result = {"success": False, "error": str(e)}

            build_succeeded = self._build_succeeded(last_result)
            if build_succeeded and self.git.has_changes():
                return last_result

            if not self.lifecycle.should_retry_execution(attempt):
                break

            attempt += 1
            result.execution_retries = attempt
            print(
                f"  🔁 Build retry {attempt}/{self.lifecycle.max_execution_retries} "
                f"after unsuccessful execution",
                flush=True,
            )
            self._record_stage_model(ticket_id, f"build_retry_{attempt}")

        if not last_result.get("error") and not self.git.has_changes():
            last_result["error"] = "Build produced no file changes"
        return last_result

    def _run_post_build_review(self, ticket_id: str, description: str, context: dict,
                               plan: str, result: PipelineResult) -> list[str]:
        """Run post-build review and bounded repair loops."""
        review_loops = 0
        self._renew_lease(ticket_id, "post_review_initial")
        issues = self._review_current_changes(description)
        result.phase_results["post_review_attempt_0"] = issues
        self._record_stage_model(ticket_id, "post_review")

        while issues and self.lifecycle.should_retry_review(review_loops):
            review_loops += 1
            result.review_loops = review_loops
            print(
                f"  🔁 Review loop {review_loops}/{self.lifecycle.max_review_loops} "
                f"to address {len(issues)} issue(s)",
                flush=True,
            )
            feedback = "\n".join(issues)
            self._renew_lease(ticket_id, f"post_review_loop_{review_loops}")
            retry_description = (
                f"{description}\n\nAddress these blocking review findings before completion:\n"
                f"{feedback[:3000]}"
            )
            retry_plan = f"{plan}\n\nBlocking review findings to resolve:\n{feedback[:3000]}"
            retry_build = self._build(retry_description, context, retry_plan)
            result.phase_results[f"build_review_loop_{review_loops}"] = retry_build
            self._record_stage_model(ticket_id, f"build_review_loop_{review_loops}")

            if not self._build_succeeded(retry_build):
                break

            issues = self._review_current_changes(description)
            result.phase_results[f"post_review_attempt_{review_loops}"] = issues

        return issues

    def _review_current_changes(self, description: str) -> list[str]:
        """Review the current working tree diff."""
        diff_result = self.git._run("diff")
        diff = diff_result.output if diff_result.success else diff_result.error
        return self._multi_perspective_review(
            f"Code changes:\n{diff[:3000]}\n\nOriginal task: {description}",
            POST_BUILD_ROLES,
            "post",
        )

    def _complete_execution(self, tid: str, title: str, desc: str, branch: str,
                            task_type: str, tests_passed: bool,
                            review_passed: bool, result: PipelineResult,
                            start_time: float) -> dict:
        """Apply lifecycle merge policy and complete the ticket."""
        self._renew_lease(tid, "completion_stage")
        stage_result = self.git.stage_all()
        if not stage_result.success:
            return {
                "success": False,
                "final_state": self.lifecycle.failure_state(execution_failed=True),
                "error": stage_result.error or stage_result.output,
            }

        self._renew_lease(tid, "completion_commit")
        commit_result = self.git.commit(f"feat: {title[:50]} [ForgeFleet Pipeline]")
        if not commit_result.success:
            return {
                "success": False,
                "final_state": self.lifecycle.failure_state(execution_failed=True),
                "error": commit_result.error or commit_result.output,
            }

        self._renew_lease(tid, "completion_push_branch")
        branch_push = self.git.push(branch)
        if not branch_push.success:
            return {
                "success": False,
                "final_state": self.lifecycle.failure_state(execution_failed=True),
                "error": branch_push.error or branch_push.output,
            }

        merge_ctx = MergeContext(
            task_type=task_type,
            tests_passed=tests_passed,
            review_passed=review_passed,
            has_blocking_feedback=not review_passed,
            branch_mergeable=True,
            human_review_required=False,
            blocked_by_policy=False,
        )
        auto_merge_allowed, auto_merge_reason = self.lifecycle.can_auto_merge(merge_ctx)

        merged = False
        completion_message = f"Built by ForgeFleet Pipeline in {time.time() - start_time:.0f}s"
        mc_updated = False

        if auto_merge_allowed:
            self._renew_lease(tid, "completion_merge")
            merged = self._merge_branch_to_main(branch, title)
            if merged:
                response = self.mc.complete_ticket(tid, completion_message, branch)
                mc_updated = "error" not in response
                if mc_updated:
                    result.unblocked_tickets = self._unblock_dependents(tid)
                    print(f"  ✅ Auto-merged via lifecycle policy and pushed {branch}", flush=True)
            else:
                auto_merge_reason = "merge_failed"

        if not merged:
            response = self.mc.update_ticket(
                tid,
                "ready_for_review",
                result=(
                    f"{completion_message}. Awaiting review/merge decision "
                    f"({auto_merge_reason})."
                ),
                branch=branch,
            )
            mc_updated = "error" not in response
            if mc_updated:
                print(f"  ✅ Pushed to {branch} (awaiting review: {auto_merge_reason})", flush=True)

        done_state = self.lifecycle.done_state(
            merged=merged,
            mc_updated=mc_updated,
            review_passed=review_passed,
            tests_passed=tests_passed,
        )
        final_state = done_state if mc_updated else self.lifecycle.failure_state(execution_failed=True)

        return {
            "success": mc_updated and (merged or review_passed),
            "done_state": done_state,
            "final_state": final_state,
            "auto_merge_reason": auto_merge_reason,
            "merged": merged,
            "branch_pushed": branch_push.success,
        }

    def _merge_branch_to_main(self, branch: str, title: str) -> bool:
        """Merge a successful branch back to main and push it."""
        checkout_main = self.git._run("checkout", "main")
        if not checkout_main.success:
            return False

        self.git._run("pull", "--ff-only", "origin", "main", timeout=60)
        merge_result = self.git._run(
            "merge",
            "--no-ff",
            branch,
            "-m",
            f"merge: {title[:50]} [ForgeFleet Pipeline]",
            timeout=60,
        )
        if not merge_result.success:
            return False

        return self.git.push("main").success

    def _build_succeeded(self, build_result: dict) -> bool:
        """Infer whether the build stage succeeded enough to continue."""
        if not isinstance(build_result, dict):
            return bool(build_result)
        if "success" in build_result:
            return bool(build_result.get("success"))
        if "tests_passed" in build_result:
            return bool(build_result.get("tests_passed"))
        if build_result.get("error"):
            return False
        return True

    def _tests_passed(self, build_result: dict) -> bool:
        """Infer test/build pass status from the build stage output."""
        if not isinstance(build_result, dict):
            return bool(build_result)

        for key in ("tests_passed", "tests_ok", "passed", "success"):
            if key in build_result:
                return bool(build_result.get(key))

        return self.git.has_changes() and not build_result.get("error")
    
    def _record_stage_model(self, ticket_id: str, stage: str):
        """Record which model/node was used for a pipeline stage."""
        if not self.ownership:
            return
        # Use the last LLM the router handed out
        for ep in self.router.endpoints:
            if ep.busy:
                self.ownership.record_model(
                    ticket_id=ticket_id, stage=stage,
                    model_name=ep.name, node_name=ep.node, role="executor",
                )
                return
        # Fallback: record the first healthy endpoint
        for ep in self.router.endpoints:
            if ep.healthy:
                self.ownership.record_model(
                    ticket_id=ticket_id, stage=stage,
                    model_name=ep.name, node_name=ep.node, role="executor",
                )
                return

    # ─── Step 1: Context Gathering ──────────────────
    
    def _gather_context(self, ticket: dict) -> dict:
        """Understand the project, related tickets, tech stack."""
        tid = ticket["id"]
        
        # Get related tickets from MC
        all_tickets = self.mc.get_tickets()
        related = [t for t in all_tickets if t.get("parent_id") == tid or t.get("id") == ticket.get("parent_id")]
        
        # Detect tech stack from existing code
        tech_stack = self._detect_tech_stack()
        
        # Get repo map context
        if not self.repo_map.files:
            self.repo_map.build()
        
        relevant_files = self.repo_map.context_for_task(ticket.get("title", ""))
        
        return {
            "tech_stack": tech_stack,
            "related_tickets": len(related),
            "relevant_files": relevant_files[:2000],
            "repo_summary": self.repo_map.summary()[:1000],
        }
    
    def _detect_tech_stack(self) -> dict:
        """Detect project tech stack from existing files."""
        stack = {"backend": "", "frontend": "", "database": "", "language": ""}
        
        if os.path.exists(os.path.join(self.repo_dir, "Cargo.toml")):
            stack["backend"] = "Rust + Axum"
            stack["language"] = "Rust"
        if os.path.exists(os.path.join(self.repo_dir, "package.json")):
            try:
                pkg = json.loads(open(os.path.join(self.repo_dir, "package.json")).read())
                deps = pkg.get("dependencies", {})
                if "next" in deps:
                    stack["frontend"] = "Next.js + React + TypeScript"
                elif "react" in deps:
                    stack["frontend"] = "React + TypeScript"
            except Exception:
                stack["frontend"] = "Node.js"
        
        # Check for database
        for f in ["docker-compose.yml", "docker-compose.yaml"]:
            path = os.path.join(self.repo_dir, f)
            if os.path.exists(path):
                content = open(path).read()
                if "postgres" in content.lower():
                    stack["database"] = "PostgreSQL"
                elif "mysql" in content.lower():
                    stack["database"] = "MySQL"
        
        return stack
    
    # ─── Step 2: Planning ───────────────────────────
    
    def _create_plan(self, ticket: dict, context: dict) -> str:
        """Create a game plan using an LLM."""
        llm = self.router.get_llm(1)
        if not llm:
            return "Plan generation unavailable — no configured LLM endpoints were available"
        
        messages = [
            {"role": "system", "content": "You are a tech lead creating a build plan. Be specific and actionable."},
            {"role": "user", "content": f"""Create a build plan for this ticket:

Title: {ticket.get('title', '')}
Description: {ticket.get('description', '')}

Project tech stack: {json.dumps(context.get('tech_stack', {}))}
Relevant files: {context.get('relevant_files', '')[:1000]}

Output:
1. What files need to be created/modified
2. What the implementation approach should be
3. Any risks or dependencies
4. Estimated complexity (simple/moderate/complex)"""},
        ]
        
        try:
            response = llm.call(messages)
            return response.get("content", "No plan generated")
        except Exception:
            return "Plan generation failed — proceeding with direct implementation"
    
    # ─── Step 3 & 5: Multi-Perspective Review ───────
    
    def _multi_perspective_review(self, content: str, roles: list[Role], phase: str) -> list[str]:
        """Run multiple role perspectives in PARALLEL across the fleet."""
        all_issues = []
        
        # Get available LLMs for parallel execution
        available_llms = []
        for tier in [3, 2, 1]:  # Prefer smarter models for review
            eps = self.router.get_available(tier)
            for ep in eps:
                available_llms.append(LLM(
                    base_url=f"{ep.url}/v1",
                    model=ep.name,
                    timeout=config.get_tier_timeout(tier),
                ))
        
        if not available_llms:
            fallback_llm = self.router.get_llm(3) or self.router.get_llm(2) or self.router.get_llm(1)
            if not fallback_llm:
                return ["[System] No configured LLM endpoints available for review"]
            available_llms = [fallback_llm]
        
        def review_with_role(role: Role, llm: LLM) -> list[str]:
            messages = [
                {"role": "system", "content": role.perspective_prompt},
                {"role": "user", "content": f"""Review this from your perspective as {role.title}:

{content[:4000]}

Questions to answer:
{chr(10).join(f'- {q}' for q in role.review_questions)}

List any issues found. If everything looks good, say \"No issues.\"
Be specific — file names, line references, exact problems."""},
            ]
            try:
                response = llm.call(messages)
                result = response.get("content", "")
                if result and "no issues" not in result.lower()[:50]:
                    return [f"[{role.title}] {result[:500]}"]
            except Exception:
                pass
            return []
        
        # Run all roles in parallel
        with ThreadPoolExecutor(max_workers=min(len(roles), len(available_llms))) as executor:
            futures = {}
            for i, role in enumerate(roles):
                llm = available_llms[i % len(available_llms)]
                future = executor.submit(review_with_role, role, llm)
                futures[future] = role.name
            
            for future in as_completed(futures):
                role_name = futures[future]
                try:
                    issues = future.result()
                    all_issues.extend(issues)
                    if issues:
                        print(f"    ⚠️ {role_name}: found issues", flush=True)
                    else:
                        print(f"    ✅ {role_name}: no issues", flush=True)
                except Exception as e:
                    print(f"    ❌ {role_name}: error — {e}", flush=True)
        
        return all_issues
    
    # ─── Step 4: Build ──────────────────────────────
    
    def _build(self, description: str, context: dict, plan: str) -> dict:
        """Build using Intern→Junior→Senior→Architect seniority chain."""
        from .seniority import SeniorityPipeline
        
        tech_stack = context.get("tech_stack", {})
        
        full_description = (
            f"{description}\n\n"
            f"Plan:\n{plan[:1500]}\n\n"
            f"Relevant files:\n{context.get('relevant_files', '')[:1000]}"
        )
        
        seniority = SeniorityPipeline(tools=self.tools, router=self.router)
        result = seniority.execute(full_description, tech_stack=tech_stack)
        
        return result
    
    # ─── Step 6: Dependency Management ──────────────
    
    def _create_prerequisite_tickets(self, parent_id: str, issues: list) -> list[str]:
        """Create prerequisite tickets from review findings."""
        created = []
        for issue in issues[:3]:
            result = self.mc._request("POST", "/api/work-items", {
                "title": f"[Prerequisite] {issue[:60]}",
                "description": issue,
                "status": "todo",
                "priority": "high",
                "parent_id": parent_id,
            })
            if "error" not in result:
                created.append(issue[:60])
        return created
    
    def _unblock_dependents(self, completed_ticket_id: str) -> list[str]:
        """Find and unblock tickets that were waiting on this one."""
        all_tickets = self.mc.get_tickets(status="blocked")
        unblocked = []
        
        for t in all_tickets:
            desc = t.get("description", "")
            if completed_ticket_id in desc or t.get("parent_id") == completed_ticket_id:
                self.mc.update_ticket(t["id"], "todo")
                unblocked.append(t["title"][:60])
        
        return unblocked
    
    def _detect_task_type(self, description: str) -> str:
        """Detect task type from description."""
        desc_lower = description.lower()
        if any(k in desc_lower for k in ["handler", "endpoint", "api", "route"]):
            return "rust_handler"
        if any(k in desc_lower for k in ["page", "component", "dashboard", "ui"]):
            return "typescript_page"
        if any(k in desc_lower for k in ["model", "struct", "schema"]):
            return "rust_model"
        if any(k in desc_lower for k in ["migration", "table", "database"]):
            return "migration"
        if any(k in desc_lower for k in ["test", "spec"]):
            return "test_writing"
        return "general"
    
    # ─── Tools ──────────────────────────────────────
    
    def _build_tools(self) -> list:
        """Build file tools scoped to the active repo with traversal safeguards."""
        repo = os.path.abspath(self.repo_dir)
        allowed_commands = {
            "git", "python", "python3", "python3.11", "python3.12", "pytest",
            "pip", "pip3", "uv", "ruff", "mypy", "npm", "pnpm", "yarn",
            "node", "npx", "cargo", "go", "make", "just", "ls", "cat",
            "grep", "sed", "find", "echo", "bash", "sh",
        }
        blocked_shell_tokens = ("&&", "||", ";", "|", "`", "$(")

        def resolve_repo_path(path_value: str, allow_missing: bool = False) -> str:
            rel = (path_value or ".").strip() or "."
            candidate = os.path.abspath(os.path.join(repo, rel))
            if os.path.commonpath([repo, candidate]) != repo:
                raise ValueError(f"Path escapes repository root: {path_value}")
            if not allow_missing and not os.path.exists(candidate):
                raise FileNotFoundError(path_value)
            return candidate

        def rf(filepath=""):
            try:
                file_path = resolve_repo_path(filepath)
                if not os.path.isfile(file_path):
                    return f"Not found: {filepath}"
                with open(file_path, encoding="utf-8", errors="ignore") as handle:
                    content = handle.read()
                return content[:4000] if len(content) > 4000 else content
            except Exception as e:
                return f"Error: {e}"

        def lf(directory=".", pattern=""):
            try:
                full = resolve_repo_path(directory or ".")
                if not os.path.isdir(full):
                    return f"Not found: {directory}"
                exclude = {"target", "node_modules", ".git", "dist", ".next", "__pycache__"}
                files = []
                for root, dirs, fnames in os.walk(full):
                    dirs[:] = [d for d in dirs if d not in exclude]
                    for filename in fnames:
                        if pattern and not filename.endswith(pattern):
                            continue
                        files.append(os.path.relpath(os.path.join(root, filename), repo))
                    if len(files) > 30:
                        break
                return "\n".join(files[:30])
            except Exception as e:
                return f"Error: {e}"

        def wf(filepath="", content=""):
            try:
                file_path = resolve_repo_path(filepath, allow_missing=True)
                parent = os.path.dirname(file_path)
                if os.path.commonpath([repo, parent]) != repo:
                    return f"Rejected path: {filepath}"
                os.makedirs(parent, exist_ok=True)
                with open(file_path, "w", encoding="utf-8") as handle:
                    handle.write(content)
                relative = os.path.relpath(file_path, repo)
                return f"WRITTEN: {relative} ({len(content)} chars)"
            except Exception as e:
                return f"Error: {e}"

        def guard_command(command: str) -> tuple[bool, str, list[str]]:
            if not command or not command.strip():
                return False, "Rejected: empty command", []
            if any(token in command for token in blocked_shell_tokens):
                return False, "Rejected: shell operators are blocked", []
            try:
                args = shlex.split(command)
            except ValueError as exc:
                return False, f"Rejected: invalid command syntax ({exc})", []
            if not args:
                return False, "Rejected: empty command", []
            if args[0] not in allowed_commands:
                return False, f"Rejected command '{args[0]}' — not in allowlist", []

            for arg in args[1:]:
                if arg.startswith("/") or arg == ".." or arg.startswith("../"):
                    return False, f"Rejected path argument: {arg}", []
            return True, "", args

        def rc(command=""):
            ok, reason, args = guard_command(command)
            if not ok:
                return reason
            try:
                result = subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=repo,
                    shell=False,
                )
                return (result.stdout + result.stderr)[:3000]
            except Exception as e:
                return str(e)

        return [
            Tool(name="read_file", description="Read a file",
                 parameters={"type": "object", "properties": {"filepath": {"type": "string"}}, "required": ["filepath"]}, func=rf),
            Tool(name="list_files", description="List files",
                 parameters={"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}, func=lf),
            Tool(name="write_file", description="Create/overwrite a file",
                 parameters={"type": "object", "properties": {"filepath": {"type": "string"}, "content": {"type": "string"}}, "required": ["filepath", "content"]}, func=wf),
            Tool(name="run_command", description="Run guarded shell command",
                 parameters={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}, func=rc),
        ]
