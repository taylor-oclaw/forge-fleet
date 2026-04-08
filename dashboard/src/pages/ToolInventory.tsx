import { useMemo, useState } from 'react'

type ToolInfo = {
  name: string
  category: string
  description: string
}

const TOOLS: ToolInfo[] = [
  // File Ops
  { name: 'Bash', category: 'File Ops', description: 'Execute shell commands with persistent state' },
  { name: 'Read', category: 'File Ops', description: 'Read files with line numbers, offset/limit' },
  { name: 'Write', category: 'File Ops', description: 'Create or overwrite files' },
  { name: 'Edit', category: 'File Ops', description: 'Exact string replacement in files' },
  { name: 'Glob', category: 'File Ops', description: 'Find files by pattern (e.g. **/*.rs)' },
  { name: 'Grep', category: 'File Ops', description: 'Search file contents with regex' },
  // Agent
  { name: 'Agent', category: 'Agent', description: 'Spawn sub-agents on fleet nodes' },
  { name: 'SendMessage', category: 'Agent', description: 'Inter-agent messaging' },
  { name: 'Delegate', category: 'Agent', description: 'Route subtask to specialized agent role' },
  // Tasks
  { name: 'TaskCreate', category: 'Tasks', description: 'Create a task to track work' },
  { name: 'TaskGet', category: 'Tasks', description: 'Get task details by ID' },
  { name: 'TaskUpdate', category: 'Tasks', description: 'Update task status or details' },
  { name: 'TaskList', category: 'Tasks', description: 'List all tasks' },
  { name: 'TaskStop', category: 'Tasks', description: 'Cancel a running task' },
  { name: 'TaskOutput', category: 'Tasks', description: 'Get task output/result' },
  // Web
  { name: 'WebFetch', category: 'Web', description: 'Fetch web pages and convert to text' },
  { name: 'WebSearch', category: 'Web', description: 'Search the web via DuckDuckGo' },
  { name: 'HttpRequest', category: 'Web', description: 'Generic HTTP client (GET/POST/PUT/DELETE)' },
  // Research
  { name: 'DeepResearch', category: 'Research', description: 'Multi-source research with summarization' },
  { name: 'WikiLookup', category: 'Research', description: 'Wikipedia article lookup' },
  { name: 'ScholarSearch', category: 'Research', description: 'Academic paper search (Semantic Scholar)' },
  // Git
  { name: 'GitPR', category: 'Git', description: 'GitHub PR management (create/list/merge/review)' },
  { name: 'GitBlame', category: 'Git', description: 'Git blame analysis with porcelain parsing' },
  { name: 'GithubIssues', category: 'Git', description: 'Create/list/manage GitHub issues' },
  { name: 'TestGen', category: 'Git', description: 'Extract code for test generation' },
  { name: 'EnterWorktree', category: 'Git', description: 'Create isolated git worktree' },
  { name: 'ExitWorktree', category: 'Git', description: 'Remove git worktree' },
  // DevOps
  { name: 'Docker', category: 'DevOps', description: 'Container management (ps/build/run/compose)' },
  { name: 'LintFix', category: 'DevOps', description: 'Run linter/formatter/tests with auto-fix' },
  { name: 'DocGen', category: 'DevOps', description: 'Generate documentation (rustdoc/JSDoc)' },
  { name: 'DepCheck', category: 'DevOps', description: 'Audit dependencies for vulnerabilities' },
  { name: 'CronSchedule', category: 'DevOps', description: 'Schedule recurring fleet tasks' },
  // Project Management
  { name: 'ProjectEstimate', category: 'Project Mgmt', description: 'Story points and hour estimates from descriptions' },
  { name: 'VelocityTracker', category: 'Project Mgmt', description: 'Calculate team velocity from sprint history' },
  { name: 'DeadlineProjector', category: 'Project Mgmt', description: 'Project completion date from remaining work' },
  { name: 'SprintPlanner', category: 'Project Mgmt', description: 'Auto-assign items to sprint by priority/capacity' },
  { name: 'RiskAssessor', category: 'Project Mgmt', description: 'Identify blocked items, scope creep, bottlenecks' },
  { name: 'WorkloadBalancer', category: 'Project Mgmt', description: 'Distribute work evenly across assignees' },
  { name: 'DependencyMapper', category: 'Project Mgmt', description: 'Analyze dependency chains, find critical path' },
  // Finance
  { name: 'BudgetTracker', category: 'Finance', description: 'Income/expense tracking with category breakdown' },
  { name: 'ProfitLoss', category: 'Finance', description: 'P&L statement (revenue, COGS, net income)' },
  { name: 'CashFlowForecast', category: 'Finance', description: 'Project N months of cash flow' },
  { name: 'InvoiceGen', category: 'Finance', description: 'Generate professional invoices' },
  // Analytics
  { name: 'StatsCalc', category: 'Analytics', description: 'Mean, median, std dev, percentiles, correlation' },
  { name: 'TimeSeriesAnalysis', category: 'Analytics', description: 'Trend detection, moving averages, outliers' },
  // Fleet Ops
  { name: 'NodeSetup', category: 'Fleet Ops', description: 'Install prerequisites on new machines via SSH' },
  { name: 'NodeEnroll', category: 'Fleet Ops', description: 'Register node in fleet.toml' },
  { name: 'ModelDeploy', category: 'Fleet Ops', description: 'Download and deploy models to fleet nodes' },
  { name: 'FleetInventory', category: 'Fleet Ops', description: 'Scan fleet, report all nodes and models' },
  { name: 'NodeHealthCheck', category: 'Fleet Ops', description: 'Deep health check via SSH' },
  { name: 'BinaryDeploy', category: 'Fleet Ops', description: 'Build and deploy ForgeFleet binary to nodes' },
  // Intelligence
  { name: 'PatternLearner', category: 'Intelligence', description: 'Track successful patterns per task type' },
  { name: 'ModelScorecard', category: 'Intelligence', description: 'Track model quality, generate leaderboards' },
  { name: 'ReviewQueue', category: 'Intelligence', description: 'Queue work for human review' },
  { name: 'RollbackManager', category: 'Intelligence', description: 'Preview/stash/rollback git changes' },
  { name: 'SmartSearch', category: 'Intelligence', description: 'Search across code, memory, git, docs' },
  { name: 'WatchAndReact', category: 'Intelligence', description: 'Event-driven triggers for agent tasks' },
  { name: 'ProjectScaffold', category: 'Intelligence', description: 'Generate new projects from templates' },
  // Media
  { name: 'Screenshot', category: 'Media', description: 'Capture web page screenshots' },
  { name: 'ImageAnalyze', category: 'Media', description: 'Image dimensions, EXIF, OCR' },
  { name: 'VideoDownload', category: 'Media', description: 'Download videos via yt-dlp' },
  { name: 'LinkPreview', category: 'Media', description: 'Fetch OpenGraph metadata from URLs' },
  { name: 'ImageConvert', category: 'Media', description: 'Resize, convert, compress images' },
  // Multimodal
  { name: 'PhotoAnalysis', category: 'Multimodal', description: 'Full photo analysis (OCR, EXIF, colors)' },
  { name: 'VideoAnalysis', category: 'Multimodal', description: 'Video metadata, frame extraction, transcription' },
  { name: 'AudioAnalysis', category: 'Multimodal', description: 'Audio transcription (Whisper), conversion' },
  // Computer
  { name: 'ProcessManager', category: 'Computer', description: 'List/search/kill processes' },
  { name: 'Clipboard', category: 'Computer', description: 'Read/write system clipboard' },
  { name: 'SystemControl', category: 'Computer', description: 'Open apps/URLs, notifications, system info' },
  { name: 'ServiceManager', category: 'Computer', description: 'Manage system services (systemd/launchd)' },
  { name: 'PackageManager', category: 'Computer', description: 'Install/update system packages' },
  // Database & Crypto
  { name: 'DatabaseQuery', category: 'Database', description: 'Run SQL against PostgreSQL/SQLite/MySQL' },
  { name: 'HashGenerator', category: 'Crypto', description: 'SHA256/SHA512/MD5 for strings and files' },
  { name: 'PasswordGen', category: 'Crypto', description: 'Secure random passwords and passphrases' },
  { name: 'TextTransform', category: 'Crypto', description: 'Base64, URL encode/decode, JSON format' },
  { name: 'Calculator', category: 'Crypto', description: 'Evaluate math expressions' },
  // Model Management
  { name: 'ModelBrowser', category: 'Models', description: 'Search HuggingFace, Ollama, fleet models' },
  { name: 'ModelDownloader', category: 'Models', description: 'Download models (Ollama/HF/URL)' },
  { name: 'ModelCompare', category: 'Models', description: 'Side-by-side model comparison' },
  { name: 'ModelDiscovery', category: 'Models', description: 'Discover models from all sources' },
  { name: 'ClusterInference', category: 'Models', description: 'Distributed inference across fleet nodes' },
  // Version
  { name: 'VersionManager', category: 'Version', description: 'Version management, upgrades, fleet deploy' },
  // Utility
  { name: 'Reminder', category: 'Utility', description: 'Set time-based reminders' },
  { name: 'Timer', category: 'Utility', description: 'Benchmark command execution time' },
  { name: 'Regex', category: 'Utility', description: 'Test and debug regex patterns' },
  { name: 'Diagram', category: 'Utility', description: 'Generate Mermaid diagrams' },
  { name: 'Diff', category: 'Utility', description: 'Generate diffs (files, git versions)' },
  { name: 'JsonQuery', category: 'Utility', description: 'Query JSON with jq expressions' },
  { name: 'FileCompress', category: 'Utility', description: 'Zip/tar compress and decompress' },
  { name: 'FileSync', category: 'Utility', description: 'Rsync between local and fleet nodes' },
  { name: 'HealthMonitor', category: 'Utility', description: 'Check URL health with timing' },
  // Automation
  { name: 'SelfHeal', category: 'Automation', description: 'Diagnose and auto-fix fleet failures' },
  { name: 'AutoFleet', category: 'Automation', description: 'Autonomous fleet management' },
  { name: 'TaskDecomposer', category: 'Automation', description: 'Break complex tasks into subtrees' },
  // Builders
  { name: 'ToolBuilder', category: 'Builders', description: 'Create new compiled Rust tools at runtime' },
  { name: 'SkillBuilder', category: 'Builders', description: 'Create loadable SKILL.md skills at runtime' },
  // Planning
  { name: 'AskUserQuestion', category: 'Planning', description: 'Request user input/clarification' },
  { name: 'EnterPlanMode', category: 'Planning', description: 'Switch to read-only planning mode' },
  { name: 'ExitPlanMode', category: 'Planning', description: 'Exit planning, start implementing' },
  // Agentic
  { name: 'VerifyAndRetry', category: 'Agentic', description: 'Run verification, report pass/fail' },
  { name: 'PdfExtract', category: 'Agentic', description: 'Extract text from PDFs' },
  { name: 'SpreadsheetQuery', category: 'Agentic', description: 'Read/query CSV and Excel files' },
  // Content
  { name: 'ChangelogGen', category: 'Content', description: 'Generate changelogs from git history' },
  { name: 'ReportGen', category: 'Content', description: 'Generate structured markdown reports' },
  { name: 'MeetingNotes', category: 'Content', description: 'Structure notes into action items' },
  // Code Quality
  { name: 'CodeComplexity', category: 'Code Quality', description: 'Analyze code complexity and file sizes' },
  { name: 'DuplicateDetector', category: 'Code Quality', description: 'Find duplicate code patterns' },
  { name: 'LogAnalyzer', category: 'Code Quality', description: 'Parse and analyze log files' },
]

const CATEGORIES = [...new Set(TOOLS.map(t => t.category))].sort()

export function ToolInventory() {
  const [search, setSearch] = useState('')
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)

  const filtered = useMemo(() => {
    return TOOLS.filter(t => {
      const matchesSearch = !search || t.name.toLowerCase().includes(search.toLowerCase()) || t.description.toLowerCase().includes(search.toLowerCase())
      const matchesCategory = !selectedCategory || t.category === selectedCategory
      return matchesSearch && matchesCategory
    })
  }, [search, selectedCategory])

  const categoryCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    TOOLS.forEach(t => { counts[t.category] = (counts[t.category] || 0) + 1 })
    return counts
  }, [])

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">Tool Inventory</h2>
          <p className="text-sm text-slate-400">{TOOLS.length} built-in tools across {CATEGORIES.length} categories</p>
        </div>
        <input
          type="text"
          placeholder="Search tools..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 w-64"
        />
      </div>

      <div className="flex gap-2 flex-wrap">
        <button
          onClick={() => setSelectedCategory(null)}
          className={`rounded-full px-3 py-1 text-xs ${!selectedCategory ? 'bg-violet-500/30 text-violet-200 border border-violet-500/50' : 'bg-slate-800 text-slate-400 border border-slate-700'}`}
        >
          All ({TOOLS.length})
        </button>
        {CATEGORIES.map(cat => (
          <button
            key={cat}
            onClick={() => setSelectedCategory(selectedCategory === cat ? null : cat)}
            className={`rounded-full px-3 py-1 text-xs ${selectedCategory === cat ? 'bg-violet-500/30 text-violet-200 border border-violet-500/50' : 'bg-slate-800 text-slate-400 border border-slate-700'}`}
          >
            {cat} ({categoryCounts[cat] || 0})
          </button>
        ))}
      </div>

      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {filtered.map(tool => (
          <article key={tool.name} className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 hover:border-slate-600 transition">
            <div className="flex items-start justify-between">
              <h3 className="font-mono font-semibold text-emerald-400">{tool.name}</h3>
              <span className="rounded-full bg-slate-800 px-2 py-0.5 text-xs text-slate-400">{tool.category}</span>
            </div>
            <p className="mt-2 text-sm text-slate-400">{tool.description}</p>
          </article>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-8 text-center text-slate-500">
          No tools matching "{search}" {selectedCategory ? `in ${selectedCategory}` : ''}
        </div>
      )}
    </section>
  )
}
