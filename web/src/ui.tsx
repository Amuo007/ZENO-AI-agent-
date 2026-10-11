import type { ReactNode } from 'react'

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-[22px] border border-line bg-surface shadow-[var(--shadow-card)] ${className}`}>
      {children}
    </section>
  )
}

export function CardTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-3 flex min-h-8 items-center justify-between gap-3">
      <h2 className="text-[13px] font-semibold uppercase tracking-[0.06em] text-muted">{children}</h2>
      {action}
    </div>
  )
}

/** A page's large title, Apple style, with an optional line under it and actions on the right. */
export function PageHeader({ title, subtitle, action }: { title: string; subtitle?: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="font-display text-[34px] font-bold leading-tight tracking-[-0.02em]">{title}</h1>
        {subtitle && <p className="mt-1 max-w-2xl text-[15px] text-muted">{subtitle}</p>}
      </div>
      {action}
    </div>
  )
}

const buttonBase =
  'inline-flex shrink-0 items-center justify-center gap-2 rounded-full text-sm font-medium transition active:scale-[0.97] disabled:pointer-events-none disabled:opacity-40'

const buttonVariants = {
  primary: 'bg-primary text-on-primary hover:opacity-85',
  secondary: 'bg-raised text-ink hover:bg-sunken',
  outline: 'border border-line bg-surface text-ink hover:bg-raised',
  ghost: 'text-muted hover:bg-raised hover:text-ink',
  danger: 'bg-danger text-white hover:opacity-90',
  'danger-soft': 'bg-danger/10 text-danger hover:bg-danger/15',
}

export function buttonClass(variant: keyof typeof buttonVariants = 'secondary', size: 'sm' | 'md' | 'lg' = 'md') {
  const sizing = { sm: 'h-8 px-3.5', md: 'h-10 px-4', lg: 'h-12 px-6 text-[15px]' }[size]
  return `${buttonBase} ${sizing} ${buttonVariants[variant]}`
}

export function iconButtonClass(size: 'sm' | 'md' = 'md') {
  return `${buttonBase} ${size === 'sm' ? 'h-8 w-8' : 'h-10 w-10'} text-muted hover:bg-raised hover:text-ink`
}

export const inputClass =
  'h-10 w-full rounded-xl border border-line bg-surface px-3.5 text-sm text-ink outline-none transition placeholder:text-faint focus:border-faint focus:ring-4 focus:ring-[var(--color-glow)]'

export function StatusDot({ tone, pulse = false }: { tone: 'ok' | 'bad' | 'idle' | 'busy' | 'warn'; pulse?: boolean }) {
  const colors = { ok: 'bg-success', bad: 'bg-danger', idle: 'bg-faint', busy: 'bg-ink', warn: 'bg-warning' }
  return (
    <span className="relative inline-flex h-2 w-2 shrink-0">
      {pulse && <span className={`absolute inset-0 rounded-full ${colors[tone]} animate-ripple`} />}
      <span className={`relative inline-block h-2 w-2 rounded-full ${colors[tone]}`} />
    </span>
  )
}

export function Pill({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'ok' | 'bad' | 'busy' | 'warn' }) {
  const tones = {
    neutral: 'bg-raised text-muted',
    ok: 'bg-success/12 text-success',
    bad: 'bg-danger/12 text-danger',
    busy: 'bg-ink text-canvas',
    warn: 'bg-warning/12 text-warning',
  }
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${tones[tone]}`}>
      {children}
    </span>
  )
}

/** Apple-style segmented control. */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  size = 'md',
  className = '',
}: {
  value: T
  options: readonly (readonly [T, ReactNode])[]
  onChange: (value: T) => void
  size?: 'sm' | 'md'
  className?: string
}) {
  return (
    <div className={`inline-flex rounded-full bg-raised p-1 ${className}`} role="tablist">
      {options.map(([id, label]) => (
        <button
          key={id}
          role="tab"
          aria-selected={value === id}
          onClick={() => onChange(id)}
          className={`flex items-center gap-1.5 rounded-full font-medium transition ${
            size === 'sm' ? 'px-3 py-1 text-[13px]' : 'px-4 py-1.5 text-sm'
          } ${value === id ? 'bg-surface text-ink shadow-[0_1px_3px_rgb(0_0_0/0.12)]' : 'text-muted hover:text-ink'}`}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

export function Switch({
  checked,
  disabled,
  onChange,
  label,
}: {
  checked: boolean
  disabled?: boolean
  onChange: (checked: boolean) => void
  label?: string
}) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label ?? (checked ? 'On' : 'Off')}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-[31px] w-[51px] shrink-0 rounded-full transition-colors disabled:opacity-50 ${
        checked ? 'bg-success' : 'bg-sunken'
      }`}
    >
      <span
        className={`absolute left-0 top-[2px] h-[27px] w-[27px] rounded-full bg-white shadow-[0_2px_4px_rgb(0_0_0/0.2)] transition-transform ${
          checked ? 'translate-x-[22px]' : 'translate-x-[2px]'
        }`}
      />
    </button>
  )
}

export function Meter({ value, max, className = '' }: { value: number; max: number; className?: string }) {
  const percent = max > 0 ? Math.min(100, (value / max) * 100) : 0
  const tone = percent > 90 ? 'bg-danger' : percent > 75 ? 'bg-warning' : 'bg-ink'
  return (
    <div className={`h-1.5 overflow-hidden rounded-full bg-sunken ${className}`}>
      <div className={`h-full rounded-full ${tone} transition-[width] duration-500`} style={{ width: `${Math.max(percent, value > 0 ? 1.5 : 0)}%` }} />
    </div>
  )
}

export function EmptyState({ title, children, art }: { title: string; children?: ReactNode; art?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-[22px] border border-dashed border-line px-6 py-10 text-center">
      {art}
      <p className="text-[15px] font-medium">{title}</p>
      {children && <div className="mt-1 max-w-sm text-sm text-muted">{children}</div>}
    </div>
  )
}

export function ErrorText({ children }: { children: ReactNode }) {
  return children ? <p className="text-sm text-danger">{children}</p> : null
}

/** A tool's readable name: "gmail-find-email" → "Gmail · Find email". */
export function toolLabel(name: string): string {
  const [app, action] = name.includes('-') ? [name.slice(0, name.indexOf('-')), name.slice(name.indexOf('-') + 1)] : ['', name]
  const words = (text: string) => text.replace(/[-_]+/g, ' ').trim()
  const sentence = (text: string) => (text ? text[0].toUpperCase() + text.slice(1) : text)
  const title = (text: string) => words(text).replace(/\b\w/g, (c) => c.toUpperCase())
  return app ? `${title(app)} · ${sentence(words(action))}` : sentence(words(action))
}

export function formatTokens(count: number): string {
  if (count < 1000) return String(count)
  if (count < 1_000_000) return `${(count / 1000).toFixed(count < 10_000 ? 1 : 0)}K`
  return `${(count / 1_000_000).toFixed(2)}M`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit++
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

export function timeAgo(epochSeconds: number): string {
  const diff = Date.now() / 1000 - epochSeconds
  if (diff < 45) return 'just now'
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`
  const date = new Date(epochSeconds * 1000)
  const sameYear = date.getFullYear() === new Date().getFullYear()
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', ...(sameYear ? {} : { year: 'numeric' }) })
}

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}
