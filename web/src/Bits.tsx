import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import {
  AlertCircle,
  Bell,
  Brain,
  CheckCircle2,
  ChevronDown,
  CornerDownRight,
  Download,
  FileText,
  HardDrive,
  Loader2,
  MessageCircleQuestion,
  MessageSquare,
  Package,
  Play,
  Square,
  Terminal,
  Trash2,
  Upload,
  Wrench,
  Zap,
} from 'lucide-react'
import {
  ACTIVE_STATUSES,
  api,
  send,
  SIGNED_OUT_EVENT,
  type Bit,
  type BitEvent,
  type BitsOverview,
  type Question,
  type SharedFile,
  type Storage,
  type Tool,
  type ToolDetail,
} from './api'
import { BitAvatar } from './Avatar'
import { BitMessageBox, bitPhase, PhasePill, QuestionsCard } from './BitTalk'
import { LiveElapsed } from './LiveElapsed'
import {
  buttonClass,
  Card,
  CardTitle,
  EmptyState,
  errorMessage,
  formatBytes,
  formatTokens,
  iconButtonClass,
  inputClass,
  Meter,
  PageHeader,
  Pill,
  StatusDot,
  timeAgo,
} from './ui'
import { usePolling } from './usePolling'

export default function Bits({
  overview,
  error,
  refresh,
}: {
  overview: BitsOverview | null
  error: string | null
  refresh: () => Promise<void>
}) {
  if (!overview) {
    return (
      <div className="flex h-64 items-center justify-center text-muted">
        {error ? <p className="text-danger">{error}</p> : <Loader2 className="animate-spin" />}
      </div>
    )
  }

  const active = overview.bits.filter((b) => ACTIVE_STATUSES.includes(b.status))
  const questions = active.map((b) => b.question).filter((q): q is Question => q !== null)

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        title="Bits"
        subtitle="Your AI workers. ZENO hands them jobs; each works in its own sandbox, builds the tools it needs, and checks in with you when it has to."
        action={<Pill>{overview.model}</Pill>}
      />
      <QuestionsCard questions={questions} onAnswered={refresh} />
      <Team overview={overview} onChange={refresh} />
      <Stats overview={overview} working={active.length} />
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex min-w-0 flex-col gap-6">
          <NewTask max={overview.max_bits} busy={active.length >= overview.max_bits} onStarted={refresh} />
          <History bits={overview.bits} onChange={refresh} />
        </div>
        <div className="flex min-w-0 flex-col gap-6">
          <Toolbox />
          <Workspace storage={overview.storage} />
        </div>
      </div>
    </div>
  )
}

// --- The team ----------------------------------------------------------------------------------

function Team({ overview, onChange }: { overview: BitsOverview; onChange: () => void }) {
  const slots = Array.from({ length: overview.max_bits }, (_, i) => i + 1)
  const bySlot = new Map(
    overview.bits.filter((b) => ACTIVE_STATUSES.includes(b.status)).map((b) => [b.slot, b] as const),
  )
  const working = bySlot.size

  return (
    <section>
      <div className="flex flex-col items-center">
        <div className="flex items-center gap-4 rounded-[26px] border border-line bg-surface py-3 pl-3 pr-6 shadow-[var(--shadow-card)]">
          <div className="relative h-16 w-16 overflow-hidden rounded-[18px] bg-raised">
            <img src="/avatars/zeno-idle.webp" alt="" className="avatar-art absolute -bottom-3 left-1/2 w-[120%] max-w-none -translate-x-1/2" />
          </div>
          <div>
            <p className="font-display text-lg font-semibold leading-tight">ZENO</p>
            <p className="text-[13px] text-muted">
              The boss · {working === 0 ? 'all Bits free' : `${working} Bit${working > 1 ? 's' : ''} working`}
            </p>
          </div>
        </div>
        {/* Org-chart connectors (wide screens only). */}
        <div className="hidden h-8 w-px bg-line md:block" />
        <div
          className="hidden h-px bg-line md:block"
          style={{ width: `${((slots.length - 1) / slots.length) * 100}%` }}
        />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 md:mt-0 md:grid-cols-3">
        {slots.map((slot) => (
          <div key={slot} className="flex min-w-0 flex-col items-center">
            <div className="hidden h-6 w-px bg-line md:block" />
            <BitCard slot={slot} bit={bySlot.get(slot)} onChange={onChange} />
          </div>
        ))}
      </div>
    </section>
  )
}

function BitCard({ slot, bit, onChange }: { slot: number; bit?: Bit; onChange: () => void }) {
  const [stopping, setStopping] = useState(false)
  const phase = bit ? bitPhase(bit) : null

  async function stop() {
    if (!bit) return
    setStopping(true)
    try {
      await api(`/bits/${bit.id}/stop`, send('POST'))
      onChange()
    } finally {
      setStopping(false)
    }
  }

  return (
    <Card
      className={`w-full p-5 transition ${
        phase?.state === 'asking' ? 'ring-1 ring-warning/40' : phase?.state === 'working' ? 'ring-1 ring-[var(--color-faint)]' : ''
      }`}
    >
      <div className="flex items-start gap-4">
        <BitAvatar slot={slot} state={phase?.state ?? 'idle'} size={76} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <p className="font-mono text-[15px] font-semibold tracking-wide">BIT-{String(slot).padStart(2, '0')}</p>
            {bit ? <PhasePill bit={bit} /> : <Pill>Idle</Pill>}
          </div>
          {bit && phase ? (
            <>
              <p className="mt-1 line-clamp-2 text-sm font-medium leading-snug">{bit.title}</p>
              {phase.state === 'asking' ? (
                <p className="mt-1 text-[13px] leading-snug text-ink">“{phase.line}”</p>
              ) : (
                <p className={`mt-0.5 truncate text-xs ${phase.state === 'working' ? 'shimmer-text' : 'text-muted'}`}>{phase.line}</p>
              )}
            </>
          ) : (
            <p className="mt-1 text-sm text-muted">Ready for work</p>
          )}
        </div>
      </div>
      {bit && phase?.state === 'asking' && (
        <div className="mt-3">
          <BitMessageBox bit={bit} placeholder={`Answer ${bit.name}`} onSent={onChange} />
        </div>
      )}
      {bit && (
        <div className="mt-4 flex items-center gap-3 border-t border-line pt-3 text-xs text-muted">
          <span className="flex items-center gap-1">
            <Zap size={12} />
            {formatTokens(bit.total_tokens)} tokens
          </span>
          <span className="flex items-center gap-1">
            <Wrench size={12} />
            {bit.tool_calls} steps
          </span>
          <LiveElapsed bit={bit} className="ml-auto" />
          <button onClick={stop} disabled={stopping} className={buttonClass('danger-soft', 'sm')} title="Stop this Bit">
            {stopping ? <Loader2 size={13} className="animate-spin" /> : <Square size={11} fill="currentColor" />}
            Stop
          </button>
        </div>
      )}
    </Card>
  )
}

// --- Numbers -----------------------------------------------------------------------------------

function Stat({ label, value, detail, children }: { label: string; value: ReactNode; detail?: ReactNode; children?: ReactNode }) {
  return (
    <Card className="p-5">
      <p className="text-[13px] font-medium text-muted">{label}</p>
      <p className="mt-1 font-display text-[26px] font-semibold tabular-nums tracking-tight">{value}</p>
      {detail && <p className="mt-0.5 text-xs text-muted">{detail}</p>}
      {children}
    </Card>
  )
}

function Stats({ overview, working }: { overview: BitsOverview; working: number }) {
  const today = overview.tokens.today
  const total = overview.tokens.all_time
  const { storage, sandbox } = overview
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Stat
        label="Working now"
        value={
          <>
            {working}
            <span className="text-faint"> / {overview.max_bits}</span>
          </>
        }
        detail={`${today.tasks} task${today.tasks === 1 ? '' : 's'} today · ${total.tasks} in total`}
      />
      <Stat
        label="Tokens today"
        value={formatTokens(today.prompt_tokens + today.completion_tokens)}
        detail={
          <>
            {formatTokens(today.prompt_tokens)} in · {formatTokens(today.completion_tokens)} out
            {today.cached_tokens > 0 && <> · {formatTokens(today.cached_tokens)} cached</>}
          </>
        }
      />
      <Stat label="Storage pool" value={formatBytes(storage.used)} detail={`of ${formatBytes(storage.quota)}, shared by your Bits`}>
        <Meter value={storage.used} max={storage.quota} className="mt-3" />
      </Stat>
      <Stat
        label="Sandbox"
        value={
          <span className="flex items-center gap-2 text-[22px]">
            <StatusDot tone={sandbox.available ? 'ok' : 'warn'} />
            {sandbox.available ? 'Ready' : 'Files only'}
          </span>
        }
        detail={
          sandbox.available
            ? `${sandbox.image} · ${sandbox.cpus} CPU · ${sandbox.memory}${sandbox.network ? ' · online' : ' · offline'}`
            : `${sandbox.detail}. Bits can still use files and your apps.`
        }
      />
    </div>
  )
}

// --- New task ----------------------------------------------------------------------------------

function NewTask({ max, busy, onStarted }: { max: number; busy: boolean; onStarted: () => void }) {
  const [task, setTask] = useState('')
  const [title, setTitle] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!task.trim()) return
    setSending(true)
    setError(null)
    try {
      await api('/bits', send('POST', { task, title }))
      setTask('')
      setTitle('')
      onStarted()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setSending(false)
    }
  }

  return (
    <Card className="p-5">
      <CardTitle>Give a Bit a task</CardTitle>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <textarea
          value={task}
          onChange={(e) => setTask(e.target.value)}
          rows={3}
          placeholder="e.g. Find the five most-cited papers on retrieval-augmented generation from this year and save a summary table as rag-papers.md"
          className={`${inputClass} h-auto resize-none py-2.5 leading-relaxed`}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit(e)
          }}
        />
        <div className="flex flex-wrap items-center gap-3">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Short title (optional)"
            className={`${inputClass} max-w-xs flex-1`}
          />
          <p className="flex-1 text-xs text-muted">Usually ZENO does this for you: just ask by voice.</p>
          <button disabled={sending || !task.trim() || busy} className={buttonClass('primary')}>
            {sending ? <Loader2 size={16} className="animate-spin" /> : <Play size={14} fill="currentColor" />}
            {busy ? `All ${max} busy` : 'Start'}
          </button>
        </div>
        {error && <p className="text-sm text-danger">{error}</p>}
      </form>
    </Card>
  )
}

// --- History and live log ----------------------------------------------------------------------

function History({ bits, onChange }: { bits: Bit[]; onChange: () => void }) {
  const [open, setOpen] = useState<string | null>(null)
  return (
    <Card className="overflow-hidden">
      <div className="px-5 pt-5">
        <CardTitle>Tasks</CardTitle>
      </div>
      {bits.length === 0 ? (
        <div className="px-5 pb-5">
          <EmptyState title="No tasks yet" art={<BitAvatar state="idle" size={64} className="mb-3" />}>
            When ZENO hands off work, or you start a task above, it shows up here with every step it takes.
          </EmptyState>
        </div>
      ) : (
        <ul className="divide-y divide-line">
          {bits.map((bit) => (
            <TaskRow
              key={bit.id}
              bit={bit}
              open={open === bit.id}
              onToggle={() => setOpen(open === bit.id ? null : bit.id)}
              onChange={onChange}
            />
          ))}
        </ul>
      )}
    </Card>
  )
}

function TaskRow({ bit, open, onToggle, onChange }: { bit: Bit; open: boolean; onToggle: () => void; onChange: () => void }) {
  const active = ACTIVE_STATUSES.includes(bit.status)
  const [busy, setBusy] = useState(false)

  async function act(path: string, method: string) {
    setBusy(true)
    try {
      await api(path, send(method))
      onChange()
    } finally {
      setBusy(false)
    }
  }

  return (
    <li>
      <button onClick={onToggle} className="flex w-full items-center gap-3 px-5 py-3.5 text-left transition hover:bg-raised/60">
        <BitAvatar slot={bit.slot} state={bitPhase(bit).state} size={40} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{bit.title}</span>
            <PhasePill bit={bit} />
          </span>
          <span className="mt-0.5 block truncate text-xs text-muted">
            {bit.name} · {bit.source === 'zeno' ? 'from ZENO' : 'from the dashboard'} · {timeAgo(bit.created_at)}
            {bit.model ? ` · ${bit.model}` : ''}
          </span>
        </span>
        <span className="hidden text-right text-xs text-muted sm:block">
          <span className="block font-mono tabular-nums text-ink">{formatTokens(bit.total_tokens)}</span>
          tokens
        </span>
        <span className="hidden w-16 text-right sm:block">
          <LiveElapsed bit={bit} />
        </span>
        <ChevronDown size={16} className={`shrink-0 text-faint transition ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="animate-rise border-t border-line bg-canvas/50 px-5 py-4">
          <p className="text-xs font-medium uppercase tracking-wide text-muted">Task</p>
          <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed">{bit.task}</p>

          {(bit.result || bit.error) && (
            <div
              className={`mt-4 rounded-2xl p-4 text-sm leading-relaxed ${
                bit.result ? 'bg-success/8 text-ink' : 'bg-danger/8 text-danger'
              }`}
            >
              <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide">
                {bit.result ? <CheckCircle2 size={13} className="text-success" /> : <AlertCircle size={13} />}
                {bit.result ? 'Report' : 'What went wrong'}
              </p>
              <p className="whitespace-pre-wrap">{bit.result ?? bit.error}</p>
            </div>
          )}

          <div className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
            <Figure label="Tokens in" value={formatTokens(bit.prompt_tokens)} />
            <Figure label="Tokens out" value={formatTokens(bit.completion_tokens)} />
            <Figure label="From cache" value={formatTokens(bit.cached_tokens)} />
            <Figure label="Model calls · steps" value={`${bit.llm_calls} · ${bit.tool_calls}`} />
          </div>

          <p className="mt-5 text-xs font-medium uppercase tracking-wide text-muted">Steps</p>
          <EventLog bitId={bit.id} live={active} />

          <div className="mt-4">
            <BitMessageBox
              bit={bit}
              placeholder={
                bit.status === 'asking'
                  ? `Answer ${bit.name}: ${bit.question?.text ?? ''}`
                  : active
                    ? `Tell ${bit.name} something while it works`
                    : `Follow up with ${bit.name} (it remembers this job)`
              }
              onSent={onChange}
            />
          </div>

          <div className="mt-4 flex justify-end gap-2">
            {active ? (
              <button onClick={() => act(`/bits/${bit.id}/stop`, 'POST')} disabled={busy} className={buttonClass('danger-soft', 'sm')}>
                <Square size={11} fill="currentColor" />
                Stop
              </button>
            ) : (
              <button onClick={() => act(`/bits/${bit.id}`, 'DELETE')} disabled={busy} className={buttonClass('ghost', 'sm')}>
                <Trash2 size={14} />
                Remove
              </button>
            )}
          </div>
        </div>
      )}
    </li>
  )
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-surface px-3 py-2">
      <p className="text-muted">{label}</p>
      <p className="mt-0.5 font-mono text-sm tabular-nums">{value}</p>
    </div>
  )
}

const eventIcon: Record<BitEvent['kind'], ReactNode> = {
  status: <Play size={11} />,
  think: <Brain size={12} />,
  tool: <Terminal size={12} />,
  output: <FileText size={12} />,
  error: <AlertCircle size={12} />,
  result: <CheckCircle2 size={12} />,
  question: <MessageCircleQuestion size={12} />,
  answer: <CornerDownRight size={12} />,
  message: <MessageSquare size={12} />,
  update: <Bell size={12} />,
}

/** Events that are a conversation with the user, shown as chat-like bubbles. */
const TALK_EVENTS: Partial<Record<BitEvent['kind'], { label: string; className: string }>> = {
  question: { label: 'Asked you', className: 'bg-warning/10 text-ink' },
  answer: { label: 'You answered', className: 'bg-primary text-on-primary' },
  message: { label: 'You said', className: 'bg-primary text-on-primary' },
  update: { label: 'Told you', className: 'bg-raised text-ink' },
}

/** A Bit's steps, streamed by polling for events newer than the last one shown. */
function EventLog({ bitId, live }: { bitId: string; live: boolean }) {
  const [events, setEvents] = useState<BitEvent[]>([])
  const lastId = useRef(0)
  const list = useRef<HTMLOListElement>(null)

  useEffect(() => {
    let cancelled = false
    lastId.current = 0
    setEvents([])
    async function load() {
      const data = await api<{ events: BitEvent[] }>(`/bits/${bitId}?after=${lastId.current}`)
      if (cancelled || data.events.length === 0) return
      lastId.current = data.events[data.events.length - 1].id
      setEvents((previous) => [...previous, ...data.events])
    }
    load()
    const timer = live ? window.setInterval(load, 1500) : undefined
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [bitId, live])

  useEffect(() => {
    if (live) list.current?.lastElementChild?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [events.length, live])

  if (events.length === 0) return <p className="mt-2 text-sm text-muted">No steps yet.</p>

  return (
    <ol ref={list} className="scroll-thin mt-2 max-h-96 space-y-1.5 overflow-y-auto pr-1">
      {events.map((event) => (
        <li key={event.id} className="flex gap-2.5 text-[13px]">
          <span
            className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full ${
              event.kind === 'error'
                ? 'bg-danger/12 text-danger'
                : event.kind === 'question'
                  ? 'bg-warning/15 text-warning'
                  : 'bg-raised text-muted'
            }`}
          >
            {eventIcon[event.kind] ?? <Play size={11} />}
          </span>
          <div className="min-w-0 flex-1">
            {TALK_EVENTS[event.kind] ? (
              <div className={`inline-block max-w-full rounded-2xl px-3 py-1.5 ${TALK_EVENTS[event.kind]!.className}`}>
                <span className="block text-[10px] font-semibold uppercase tracking-wide opacity-60">
                  {TALK_EVENTS[event.kind]!.label}
                </span>
                <span className="whitespace-pre-wrap leading-relaxed">{event.text}</span>
              </div>
            ) : event.kind === 'output' || event.kind === 'tool' ? (
              <pre
                className={`scroll-thin max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg px-2.5 py-1.5 font-mono text-[12px] leading-relaxed ${
                  event.kind === 'tool' ? 'bg-raised text-ink' : 'bg-surface text-muted'
                }`}
              >
                {event.text}
              </pre>
            ) : (
              <p className={`whitespace-pre-wrap leading-relaxed ${event.kind === 'error' ? 'text-danger' : event.kind === 'think' ? 'italic text-muted' : ''}`}>
                {event.text}
              </p>
            )}
          </div>
          <span className="shrink-0 pt-0.5 font-mono text-[11px] text-faint">
            {new Date(event.at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </span>
        </li>
      ))}
    </ol>
  )
}

// --- Toolbox -----------------------------------------------------------------------------------

function Toolbox() {
  const tools = usePolling(() => api<Tool[]>('/toolbox'), 8000)
  const [open, setOpen] = useState<string | null>(null)
  const list = tools.data ?? []

  return (
    <Card className="p-5">
      <CardTitle action={list.length > 0 ? <Pill>{list.length}</Pill> : undefined}>Toolbox</CardTitle>
      {list.length === 0 ? (
        <p className="text-sm leading-relaxed text-muted">
          When a Bit builds something reusable, like a PDF reader or an attachment downloader, it saves it here so every Bit
          can use it.
        </p>
      ) : (
        <ul className="-mx-2 flex flex-col">
          {list.map((tool) => (
            <ToolRow
              key={tool.name}
              tool={tool}
              open={open === tool.name}
              onToggle={() => setOpen(open === tool.name ? null : tool.name)}
              onDeleted={tools.refresh}
            />
          ))}
        </ul>
      )}
    </Card>
  )
}

function ToolRow({ tool, open, onToggle, onDeleted }: { tool: Tool; open: boolean; onToggle: () => void; onDeleted: () => void }) {
  const [detail, setDetail] = useState<ToolDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open || detail) return
    api<ToolDetail>(`/toolbox/${encodeURIComponent(tool.name)}`)
      .then(setDetail)
      .catch((e) => setError(errorMessage(e)))
  }, [open, detail, tool.name])

  async function remove() {
    if (!confirm(`Remove ${tool.name} from the toolbox? Bits won't be able to use it any more.`)) return
    try {
      await api(`/toolbox/${encodeURIComponent(tool.name)}`, send('DELETE'))
      onDeleted()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <li className="rounded-xl">
      <button onClick={onToggle} className="flex w-full items-start gap-3 rounded-xl px-2 py-2 text-left hover:bg-raised">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-raised">
          <Wrench size={15} className="text-muted" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-mono text-[13px] font-semibold">{tool.name}</span>
          <span className="line-clamp-2 block text-xs leading-snug text-muted">{tool.description}</span>
          <span className="mt-0.5 block text-[11px] text-faint">
            by {tool.made_by} · used {tool.uses} time{tool.uses === 1 ? '' : 's'} · {timeAgo(tool.updated_at)}
          </span>
        </span>
        <ChevronDown size={15} className={`mt-1 shrink-0 text-faint transition ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="animate-rise px-2 pb-3 pt-1">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">Run it</p>
          <pre className="scroll-thin mt-1 overflow-x-auto rounded-lg bg-raised px-2.5 py-1.5 font-mono text-[12px]">{tool.usage}</pre>
          {detail?.sources.map((file) => (
            <div key={file.name} className="mt-3">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">
                {file.name} · {formatBytes(file.size)}
              </p>
              {file.text !== null ? (
                <pre className="scroll-thin mt-1 max-h-64 overflow-auto rounded-lg bg-sunken px-2.5 py-2 font-mono text-[11.5px] leading-relaxed">
                  {file.text}
                </pre>
              ) : (
                <p className="mt-1 text-xs text-muted">Binary file</p>
              )}
            </div>
          ))}
          {error && <p className="mt-2 text-xs text-danger">{error}</p>}
          <div className="mt-3 flex justify-end">
            <button onClick={remove} className={buttonClass('ghost', 'sm')}>
              <Trash2 size={14} />
              Remove
            </button>
          </div>
        </div>
      )}
    </li>
  )
}

// --- Workspace ---------------------------------------------------------------------------------

const FOLDER_LABELS: Record<string, string> = {
  shared: 'Shared files',
  tools: 'Toolbox',
  '.local': 'Installed packages',
}

function Workspace({ storage }: { storage: Storage }) {
  const files = usePolling(() => api<{ shared: SharedFile[]; storage: Storage }>('/workspace'), 5000)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const picker = useRef<HTMLInputElement>(null)

  async function upload(list: FileList | null) {
    if (!list?.length) return
    setUploading(true)
    setError(null)
    try {
      for (const file of Array.from(list)) {
        const form = new FormData()
        form.append('file', file)
        const response = await fetch('/api/workspace/shared', { method: 'POST', body: form })
        if (response.status === 401) window.dispatchEvent(new Event(SIGNED_OUT_EVENT))
        if (!response.ok) {
          const body = await response.json().catch(() => null)
          throw new Error(body?.detail ?? `Upload failed (HTTP ${response.status})`)
        }
      }
      files.refresh()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setUploading(false)
      if (picker.current) picker.current.value = ''
    }
  }

  async function remove(name: string) {
    try {
      await api(`/workspace/shared/${encodeURIComponent(name)}`, send('DELETE'))
      files.refresh()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  async function clear(folder: string) {
    if (!confirm(`Delete everything in ${folder}? Installed packages and files there will be gone.`)) return
    try {
      await api(`/workspace/${folder}`, send('DELETE'))
      files.refresh()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const shared = files.data?.shared ?? []
  const pool = files.data?.storage ?? storage

  return (
    <div className="flex flex-col gap-6">
      <Card className="p-5">
        <CardTitle
          action={
            <>
              <input ref={picker} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
              <button onClick={() => picker.current?.click()} disabled={uploading} className={buttonClass('ghost', 'sm')}>
                {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
                Add files
              </button>
            </>
          }
        >
          Shared files
        </CardTitle>
        {shared.length === 0 ? (
          <p className="text-sm text-muted">
            Files your Bits share land here, ready to download. Add photos, PDFs or data for a Bit to work with.
          </p>
        ) : (
          <ul className="-mx-2 flex flex-col">
            {shared.map((file) => (
              <li key={file.name} className="group flex items-center gap-3 rounded-xl px-2 py-2 hover:bg-raised">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-raised group-hover:bg-surface">
                  <FileText size={16} className="text-muted" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{file.name}</span>
                  <span className="block text-xs text-muted">
                    {file.bit_name === 'You' ? 'Added by you' : file.bit_name} · {formatBytes(file.size)} · {timeAgo(file.created_at)}
                  </span>
                </span>
                <a
                  href={`/api/workspace/shared/${encodeURIComponent(file.name)}`}
                  className={iconButtonClass('sm')}
                  title="Download"
                  download
                >
                  <Download size={15} />
                </a>
                <button onClick={() => remove(file.name)} className={`${iconButtonClass('sm')} hover:text-danger`} title="Delete">
                  <Trash2 size={15} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="p-5">
        <CardTitle>Storage pool</CardTitle>
        <div className="flex items-baseline justify-between text-sm">
          <span className="font-medium">{formatBytes(pool.used)} used</span>
          <span className="text-muted">{formatBytes(pool.quota)}</span>
        </div>
        <Meter value={pool.used} max={pool.quota} className="mt-2" />
        <ul className="mt-4 flex flex-col gap-1">
          {pool.folders.map((folder) => (
            <li key={folder.name} className="flex items-center gap-3 rounded-xl py-1.5 text-sm">
              {folder.name === '.local' ? <Package size={15} className="text-faint" /> : <HardDrive size={15} className="text-faint" />}
              <span className="min-w-0 flex-1">
                <span className="block font-mono text-[13px]">/workspace/{folder.name}</span>
                {FOLDER_LABELS[folder.name] && <span className="block text-[11px] text-muted">{FOLDER_LABELS[folder.name]}</span>}
              </span>
              <span className="text-xs tabular-nums text-muted">{formatBytes(folder.bytes)}</span>
              {folder.name.startsWith('bit-') && folder.bytes > 0 && (
                <button onClick={() => clear(folder.name)} className={buttonClass('ghost', 'sm')}>
                  Clear
                </button>
              )}
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs leading-relaxed text-muted">
          Each Bit works in its own folder and can read <span className="font-mono">/workspace/shared</span> and the toolbox.
          Finished files and tools are published through ZENO, so Bits never overwrite each other’s work.
        </p>
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
      </Card>
    </div>
  )
}
