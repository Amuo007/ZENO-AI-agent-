import { useEffect, useState, type FormEvent } from 'react'
import { Briefcase, ChevronDown, Heart, Loader2, MessageSquare, Plus, Sparkles, Trash2, User, Users } from 'lucide-react'
import { api, send, type Conversation, type ConversationDetail, type Memory, type MemoryKind } from './api'
import {
  buttonClass,
  Card,
  CardTitle,
  EmptyState,
  errorMessage,
  iconButtonClass,
  inputClass,
  PageHeader,
  Pill,
  Switch,
  timeAgo,
} from './ui'

const KINDS: { id: MemoryKind; label: string; icon: typeof User }[] = [
  { id: 'profile', label: 'About you', icon: User },
  { id: 'preference', label: 'Preferences', icon: Heart },
  { id: 'work', label: 'What you’re working on', icon: Briefcase },
  { id: 'people', label: 'People', icon: Users },
  { id: 'other', label: 'Other', icon: Sparkles },
]

const SOURCE_LABEL = { learned: 'Learned', told: 'You asked', added: 'Added' } as const

export default function MemoryPage() {
  const [memories, setMemories] = useState<Memory[] | null>(null)
  const [learning, setLearning] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    try {
      const data = await api<{ learning: boolean; memories: Memory[] }>('/memory')
      setMemories(data.memories)
      setLearning(data.learning)
      setError(null)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function setLearningOn(on: boolean) {
    setLearning(on)
    try {
      await api('/memory/settings', send('PUT', { learning: on }))
    } catch (e) {
      setLearning(!on)
      setError(errorMessage(e))
    }
  }

  async function clearAll() {
    if (!confirm('Forget everything ZENO knows about you? This can’t be undone.')) return
    await api('/memory', send('DELETE'))
    load()
  }

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        title="Memory"
        subtitle="What ZENO remembers about you, so every conversation picks up where the last one left off. It’s yours to edit."
      />

      <div className="grid gap-6 xl:grid-cols-[1fr_420px]">
        <div className="flex flex-col gap-6">
          <Card className="flex items-center gap-4 p-5">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-raised">
              <Sparkles size={20} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[15px] font-medium">Learn from conversations</p>
              <p className="text-[13px] text-muted">
                After each voice session, ZENO notes lasting facts: who you are, what you like, people you mention and what
                you’re working on.
              </p>
            </div>
            <Switch checked={learning} onChange={setLearningOn} label="Learn from conversations" />
          </Card>

          <AddMemory onAdded={load} />
          {error && <p className="text-sm text-danger">{error}</p>}

          {memories === null ? (
            <div className="flex h-32 items-center justify-center text-muted">
              <Loader2 className="animate-spin" />
            </div>
          ) : memories.length === 0 ? (
            <EmptyState title="Nothing remembered yet">
              Talk with ZENO, or say “remember that I prefer morning meetings.” Memories show up here.
            </EmptyState>
          ) : (
            <>
              {KINDS.map((kind) => {
                const group = memories.filter((m) => m.kind === kind.id)
                if (group.length === 0) return null
                return (
                  <section key={kind.id}>
                    <h2 className="mb-2 flex items-center gap-2 px-1 text-[13px] font-semibold uppercase tracking-[0.06em] text-muted">
                      <kind.icon size={14} />
                      {kind.label}
                    </h2>
                    <Card className="divide-y divide-line overflow-hidden">
                      {group.map((memory) => (
                        <MemoryRow key={memory.id} memory={memory} onChange={load} />
                      ))}
                    </Card>
                  </section>
                )
              })}
              <div className="flex justify-end">
                <button onClick={clearAll} className={buttonClass('ghost', 'sm')}>
                  <Trash2 size={14} />
                  Forget everything
                </button>
              </div>
            </>
          )}
        </div>
        <Conversations />
      </div>
    </div>
  )
}

function AddMemory({ onAdded }: { onAdded: () => void }) {
  const [text, setText] = useState('')
  const [kind, setKind] = useState<MemoryKind>('other')
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!text.trim()) return
    try {
      await api('/memory', send('POST', { text, kind }))
      setText('')
      setError(null)
      onAdded()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      <div className="flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Tell ZENO something to remember…"
          className={inputClass}
          maxLength={500}
        />
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as MemoryKind)}
          className={`${inputClass} w-auto pr-8`}
          aria-label="Kind"
        >
          {KINDS.map((k) => (
            <option key={k.id} value={k.id}>
              {k.label}
            </option>
          ))}
        </select>
        <button disabled={!text.trim()} className={buttonClass('primary')} aria-label="Add memory">
          <Plus size={16} />
          <span className="hidden sm:inline">Add</span>
        </button>
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
    </form>
  )
}

function MemoryRow({ memory, onChange }: { memory: Memory; onChange: () => void }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(memory.text)

  async function save() {
    setEditing(false)
    if (text.trim() && text.trim() !== memory.text) {
      await api(`/memory/${memory.id}`, send('PATCH', { text }))
      onChange()
    } else {
      setText(memory.text)
    }
  }

  async function remove() {
    await api(`/memory/${memory.id}`, send('DELETE'))
    onChange()
  }

  return (
    <div className="group flex items-center gap-3 px-5 py-3">
      {editing ? (
        <input
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === 'Enter') save()
            if (e.key === 'Escape') {
              setText(memory.text)
              setEditing(false)
            }
          }}
          className={inputClass}
        />
      ) : (
        <button onClick={() => setEditing(true)} className="min-w-0 flex-1 text-left text-[15px] leading-snug" title="Edit">
          {memory.text}
        </button>
      )}
      <span className="hidden shrink-0 items-center gap-2 sm:flex">
        <Pill>{SOURCE_LABEL[memory.source]}</Pill>
        <span className="w-20 text-right text-xs text-faint">{timeAgo(memory.updated_at)}</span>
      </span>
      <button onClick={remove} className={`${iconButtonClass('sm')} opacity-60 hover:text-danger group-hover:opacity-100`} title="Forget">
        <Trash2 size={15} />
      </button>
    </div>
  )
}

function Conversations() {
  const [conversations, setConversations] = useState<Conversation[] | null>(null)
  const [open, setOpen] = useState<ConversationDetail | null>(null)

  async function load() {
    setConversations(await api<Conversation[]>('/conversations'))
  }

  useEffect(() => {
    load()
  }, [])

  async function toggle(id: string) {
    if (open?.id === id) return setOpen(null)
    setOpen(await api<ConversationDetail>(`/conversations/${id}`))
  }

  async function remove(id: string) {
    await api(`/conversations/${id}`, send('DELETE'))
    if (open?.id === id) setOpen(null)
    load()
  }

  return (
    <Card className="h-fit overflow-hidden">
      <div className="px-5 pt-5">
        <CardTitle>Conversations</CardTitle>
      </div>
      {conversations === null ? (
        <div className="flex h-24 items-center justify-center text-muted">
          <Loader2 className="animate-spin" />
        </div>
      ) : conversations.length === 0 ? (
        <p className="px-5 pb-5 text-sm text-muted">Your voice sessions will appear here, each with a short summary.</p>
      ) : (
        <ul className="divide-y divide-line">
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <button onClick={() => toggle(conversation.id)} className="flex w-full gap-3 px-5 py-3.5 text-left hover:bg-raised/60">
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-raised">
                  <MessageSquare size={15} className="text-muted" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium">{conversation.title ?? 'Conversation'}</span>
                    <span className="shrink-0 text-xs text-faint">{timeAgo(conversation.started_at)}</span>
                  </span>
                  <span className="mt-0.5 line-clamp-2 block text-[13px] text-muted">
                    {conversation.summary ?? `${conversation.message_count} messages`}
                  </span>
                </span>
                <ChevronDown size={15} className={`mt-1 shrink-0 text-faint transition ${open?.id === conversation.id ? 'rotate-180' : ''}`} />
              </button>
              {open?.id === conversation.id && (
                <div className="animate-rise border-t border-line bg-canvas/50 px-5 py-4">
                  <div className="scroll-thin flex max-h-96 flex-col gap-2 overflow-y-auto">
                    {open.messages.map((message, i) => (
                      <div key={i} className={`flex ${message.role === 'user' ? 'justify-end' : ''}`}>
                        <p
                          className={`max-w-[85%] rounded-2xl px-3 py-2 text-[13px] leading-relaxed ${
                            message.role === 'user' ? 'bg-primary text-on-primary' : 'bg-surface'
                          }`}
                        >
                          {message.text}
                        </p>
                      </div>
                    ))}
                  </div>
                  <div className="mt-3 flex justify-end">
                    <button onClick={() => remove(conversation.id)} className={buttonClass('ghost', 'sm')}>
                      <Trash2 size={14} />
                      Delete
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
