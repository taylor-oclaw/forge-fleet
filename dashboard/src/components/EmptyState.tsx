import { useNavigate } from 'react-router-dom'

type EmptyStateProps = {
  icon?: string
  title: string
  description: string
  primaryAction?: { label: string; to?: string; onClick?: () => void }
  secondaryAction?: { label: string; to?: string; onClick?: () => void }
}

export function EmptyState({ icon = '📭', title, description, primaryAction, secondaryAction }: EmptyStateProps) {
  const navigate = useNavigate()

  const handleAction = (action: { to?: string; onClick?: () => void }) => {
    if (action.onClick) action.onClick()
    else if (action.to) navigate(action.to)
  }

  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-zinc-800 bg-zinc-900/50 py-16 px-8">
      <span className="text-5xl mb-4">{icon}</span>
      <h3 className="text-lg font-medium text-zinc-100 mb-1">{title}</h3>
      <p className="text-sm text-zinc-500 text-center max-w-md mb-6">{description}</p>
      <div className="flex gap-3">
        {primaryAction && (
          <button
            onClick={() => handleAction(primaryAction)}
            className="rounded-lg bg-violet-500/20 border border-violet-500/30 px-4 py-2 text-sm font-medium text-violet-300 hover:bg-violet-500/30 transition"
          >
            {primaryAction.label}
          </button>
        )}
        {secondaryAction && (
          <button
            onClick={() => handleAction(secondaryAction)}
            className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-300 transition"
          >
            {secondaryAction.label}
          </button>
        )}
      </div>
    </div>
  )
}
