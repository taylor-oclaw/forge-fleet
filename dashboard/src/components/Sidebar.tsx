import { useState } from 'react'
import { NavLink } from 'react-router-dom'

type NavItem = { to: string; label: string; icon: string }
type NavSection = { title: string; icon: string; items: NavItem[] }

const navSections: NavSection[] = [
  {
    title: 'Fleet', icon: '🖥️',
    items: [
      { to: '/', label: 'Overview', icon: '📊' },
      { to: '/topology', label: 'Topology', icon: '🔗' },
      { to: '/model-hub', label: 'Model Hub', icon: '🤖' },
      { to: '/models', label: 'Model Inventory', icon: '📦' },
      { to: '/tools', label: 'Tools', icon: '🔧' },
      { to: '/metrics', label: 'Metrics', icon: '📈' },
    ],
  },
  {
    title: 'Project Management', icon: '📋',
    items: [
      { to: '/mission-control', label: 'Mission Control', icon: '🎯' },
      { to: '/my-tasks', label: 'My Tasks', icon: '✅' },
      { to: '/projects', label: 'Projects', icon: '📁' },
      { to: '/planning', label: 'Planning Hub', icon: '🗓️' },
      { to: '/workflow', label: 'Workflows', icon: '🔄' },
    ],
  },
  {
    title: 'AI Studio', icon: '⚡',
    items: [
      { to: '/chat', label: 'Chat Studio', icon: '💬' },
      { to: '/chats', label: 'Chats', icon: '📝' },
    ],
  },
  {
    title: 'Admin', icon: '⚙️',
    items: [
      { to: '/settings', label: 'Settings', icon: '⚙️' },
      { to: '/config', label: 'Config Editor', icon: '📄' },
      { to: '/llm-proxy', label: 'LLM Proxy', icon: '🔀' },
      { to: '/audit', label: 'Audit Log', icon: '📜' },
      { to: '/updates', label: 'Updates', icon: '🆙' },
      { to: '/onboarding', label: 'Onboarding', icon: '📚' },
    ],
  },
]

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <aside className={`flex-shrink-0 border-b border-zinc-800 bg-[#18181B]/80 transition-all duration-200 md:border-b-0 md:border-r ${
      collapsed ? 'md:w-14' : 'w-full md:w-56'
    } p-2`}>

      <button
        onClick={() => setCollapsed(!collapsed)}
        className="mb-2 hidden w-full rounded p-1 text-xs text-zinc-600 hover:bg-zinc-800 hover:text-zinc-400 md:block"
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        {collapsed ? '▸▸' : '◂◂'}
      </button>

      <nav className="space-y-3">
        {navSections.map((section) => (
          <section key={section.title}>
            {!collapsed && (
              <h2 className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-600">
                {section.icon} {section.title}
              </h2>
            )}
            <ul className="space-y-0.5">
              {section.items.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.to === '/'}
                    className={({ isActive }) =>
                      `flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition ${
                        isActive
                          ? 'bg-violet-500/15 text-violet-300 font-medium'
                          : 'text-zinc-400 hover:bg-zinc-800/70 hover:text-zinc-200'
                      } ${collapsed ? 'justify-center px-0' : ''}`
                    }
                    title={collapsed ? item.label : undefined}
                  >
                    <span className="text-sm flex-shrink-0">{item.icon}</span>
                    {!collapsed && <span className="truncate">{item.label}</span>}
                  </NavLink>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </nav>

      {!collapsed && (
        <div className="mt-3 border-t border-zinc-800 pt-2 px-2 text-[10px] text-zinc-600">
          ForgeFleet v2026.4.7
        </div>
      )}
    </aside>
  )
}
