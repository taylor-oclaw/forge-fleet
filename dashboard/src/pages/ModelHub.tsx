import { useCallback, useEffect, useState } from 'react'

type FleetModel = {
  node: string
  ip: string
  port: number
  model: string
  online: boolean
  contextWindow: number
}

type HFModel = {
  id: string
  downloads: number
  likes: number
  pipeline_tag?: string
}

export function ModelHub() {
  const [fleetModels, setFleetModels] = useState<FleetModel[]>([])
  const [hfModels, setHfModels] = useState<HFModel[]>([])
  const [hfQuery, setHfQuery] = useState('coding')
  const [loading, setLoading] = useState(false)
  const [activeTab, setActiveTab] = useState<'fleet' | 'huggingface' | 'compare'>('fleet')

  // Load fleet models
  useEffect(() => {
    const nodes = [
      { name: 'Taylor', ip: '192.168.5.100', models: [{ port: 51000, name: 'Gemma-4-31B' }, { port: 51001, name: 'Qwen3-Coder' }] },
      { name: 'Marcus', ip: '192.168.5.102', models: [{ port: 51000, name: 'Qwen2.5-Coder-32B' }] },
      { name: 'Sophie', ip: '192.168.5.103', models: [{ port: 51000, name: 'Qwen2.5-Coder-32B' }] },
      { name: 'Priya', ip: '192.168.5.104', models: [{ port: 51000, name: 'Qwen2.5-Coder-32B' }] },
      { name: 'James', ip: '192.168.5.108', models: [{ port: 51000, name: 'Qwen2.5-72B' }, { port: 51001, name: 'Qwen3.5-9B' }] },
    ]

    const models: FleetModel[] = []
    nodes.forEach(node => {
      node.models.forEach(m => {
        models.push({
          node: node.name, ip: node.ip, port: m.port, model: m.name,
          online: false, contextWindow: 32768,
        })
      })
    })
    setFleetModels(models)

    // Check health
    models.forEach((m, i) => {
      fetch(`http://${m.ip}:${m.port}/health`, { signal: AbortSignal.timeout(3000) })
        .then(r => { if (r.ok) setFleetModels(prev => prev.map((p, j) => j === i ? { ...p, online: true } : p)) })
        .catch(() => {})
    })
  }, [])

  // Search HuggingFace
  const searchHF = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await fetch(`https://huggingface.co/api/models?search=${encodeURIComponent(hfQuery)}&sort=trending&direction=-1&limit=20&filter=text-generation`)
      if (resp.ok) {
        const data = await resp.json() as HFModel[]
        setHfModels(data)
      }
    } catch { /* ignore */ }
    setLoading(false)
  }, [hfQuery])

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">Model Hub</h2>
          <p className="text-sm text-slate-400">Browse, compare, and manage LLM models</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2">
        {(['fleet', 'huggingface', 'compare'] as const).map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            className={`rounded-lg px-4 py-2 text-sm ${activeTab === tab ? 'bg-violet-500/30 text-violet-200 border border-violet-500/50' : 'bg-slate-800 text-slate-400 border border-slate-700'}`}>
            {tab === 'fleet' ? 'Fleet Models' : tab === 'huggingface' ? 'HuggingFace' : 'Compare'}
          </button>
        ))}
      </div>

      {activeTab === 'fleet' && (
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {fleetModels.map((m, i) => (
            <article key={i} className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
              <div className="flex items-center gap-2">
                <span className={`h-2.5 w-2.5 rounded-full ${m.online ? 'bg-emerald-400' : 'bg-rose-400'}`} />
                <h3 className="font-semibold text-slate-200">{m.model}</h3>
              </div>
              <div className="mt-2 space-y-1 text-sm text-slate-400">
                <div>Node: {m.node} ({m.ip}:{m.port})</div>
                <div>Context: {(m.contextWindow / 1024).toFixed(0)}K tokens</div>
                <div>Status: {m.online ? <span className="text-emerald-400">Online</span> : <span className="text-rose-400">Offline</span>}</div>
              </div>
              <div className="mt-3 h-2 rounded-full bg-slate-800">
                <div className="h-2 rounded-full bg-emerald-500" style={{ width: '3%' }} />
              </div>
              <div className="mt-1 text-xs text-slate-500">0 / {(m.contextWindow / 1024).toFixed(0)}K tokens (0%)</div>
            </article>
          ))}
        </div>
      )}

      {activeTab === 'huggingface' && (
        <div className="space-y-4">
          <div className="flex gap-2">
            <input
              type="text" value={hfQuery} onChange={e => setHfQuery(e.target.value)}
              placeholder="Search models..."
              className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
              onKeyDown={e => { if (e.key === 'Enter') void searchHF() }}
            />
            <button onClick={() => void searchHF()}
              className="rounded-md border border-violet-500/40 bg-violet-500/20 px-4 py-2 text-sm text-violet-200 hover:bg-violet-500/30">
              {loading ? 'Searching...' : 'Search'}
            </button>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {hfModels.map(m => (
              <article key={m.id} className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 hover:border-slate-600 transition">
                <h3 className="font-semibold text-slate-200 truncate">{m.id}</h3>
                <div className="mt-2 flex items-center gap-4 text-sm text-slate-400">
                  <span>⬇ {m.downloads >= 1000000 ? `${(m.downloads/1000000).toFixed(1)}M` : m.downloads >= 1000 ? `${(m.downloads/1000).toFixed(1)}K` : m.downloads}</span>
                  <span>❤ {m.likes}</span>
                  {m.pipeline_tag && <span className="rounded bg-slate-800 px-2 py-0.5 text-xs">{m.pipeline_tag}</span>}
                </div>
                <a href={`https://huggingface.co/${m.id}`} target="_blank" rel="noopener noreferrer"
                  className="mt-2 block text-xs text-violet-400 hover:text-violet-300">
                  View on HuggingFace →
                </a>
              </article>
            ))}
          </div>
        </div>
      )}

      {activeTab === 'compare' && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-6">
          <h3 className="text-lg font-semibold text-slate-200 mb-4">Model Comparison</h3>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-700 text-slate-400">
                <th className="py-2 text-left">Model</th>
                <th className="py-2 text-left">Params</th>
                <th className="py-2 text-left">Context</th>
                <th className="py-2 text-left">RAM (Q4)</th>
                <th className="py-2 text-left">Best For</th>
              </tr>
            </thead>
            <tbody className="text-slate-300">
              <tr className="border-b border-slate-800"><td className="py-2">Qwen2.5-Coder-32B</td><td>32B</td><td>32K</td><td>~20GB</td><td>Coding</td></tr>
              <tr className="border-b border-slate-800"><td className="py-2">Qwen2.5-72B</td><td>72B</td><td>32K</td><td>~45GB</td><td>Reasoning</td></tr>
              <tr className="border-b border-slate-800"><td className="py-2">Gemma-4-31B</td><td>31B</td><td>262K</td><td>~20GB</td><td>Multimodal</td></tr>
              <tr className="border-b border-slate-800"><td className="py-2">Llama-3.1-405B</td><td>405B</td><td>128K</td><td>~250GB</td><td>Maximum quality</td></tr>
              <tr className="border-b border-slate-800"><td className="py-2">Qwen3.5-9B</td><td>9B</td><td>32K</td><td>~6GB</td><td>Fast/lightweight</td></tr>
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
