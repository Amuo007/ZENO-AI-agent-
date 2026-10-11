import { useState, type FormEvent } from 'react'
import { ArrowUp, Loader2, MessageCircleQuestion } from 'lucide-react'
import { messageBit, type Bit, type Question } from './api'
import { BitAvatar, type BitState } from './Avatar'
import { Card, CardTitle, errorMessage, Pill, StatusDot, timeAgo } from './ui'

/** How a Bit is doing, in words and as an avatar pose, for every place that shows one. */
export function bitPhase(bit: Bit): { label: string; tone: 'neutral' | 'ok' | 'bad' | 'busy' | 'warn'; state: BitState; line: string } {
  switch (bit.status) {
    case 'asking':
      return { label: 'Needs you', tone: 'warn', state: 'asking', line: bit.question?.text ?? 'Waiting for your answer' }
    case 'sleeping': {
      const watching = bit.activity?.startsWith('Keeping watch') ?? false
      return {
        label: watching ? 'Watching' : 'Waiting',
        tone: 'neutral',
        state: watching ? 'watching' : 'sleeping',
        line: bit.activity ?? (watching ? 'Keeping watch' : 'Waiting'),
      }
    }
    case 'waiting':
      return { label: 'Queued', tone: 'neutral', state: 'waiting', line: `Waiting for ${bit.wait_for.length > 1 ? 'other Bits' : 'another Bit'} to finish` }
    case 'starting':
    case 'running':
      return { label: bit.status === 'starting' ? 'Starting' : 'Working', tone: 'busy', state: 'working', line: bit.activity ?? 'Working' }
    case 'paused':
      return { label: 'Paused', tone: 'neutral', state: 'idle', line: bit.activity ?? 'Paused' }
    case 'done':
      return { label: 'Done', tone: 'ok', state: 'done', line: bit.result ?? '' }
    case 'failed':
      return { label: 'Failed', tone: 'bad', state: 'failed', line: bit.error ?? '' }
    default:
      return { label: 'Stopped', tone: 'neutral', state: 'done', line: bit.error ?? '' }
  }
}

export function PhasePill({ bit }: { bit: Bit }) {
  const phase = bitPhase(bit)
  return (
    <Pill tone={phase.tone}>
      {phase.state === 'working' && <StatusDot tone="ok" pulse />}
      {phase.label}
    </Pill>
  )
}

/**
 * A one-line box for talking to a Bit: answering its question, telling it something while it
 * works, or following up once it's done.
 */
export function BitMessageBox({
  bit,
  placeholder,
  onSent,
  autoFocus = false,
}: {
  bit: Pick<Bit, 'id' | 'name'>
  placeholder: string
  onSent?: (reply: string) => void
  autoFocus?: boolean
}) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    const value = text.trim()
    if (!value || sending) return
    setSending(true)
    setNote(null)
    try {
      const reply = await messageBit(bit.id, value)
      setText('')
      setNote({ text: reply.text, error: false })
      onSent?.(reply.text)
    } catch (error) {
      setNote({ text: errorMessage(error), error: true })
    } finally {
      setSending(false)
    }
  }

  return (
    <form onSubmit={submit}>
      <div className="flex items-center gap-2 rounded-full border border-line bg-surface py-1 pl-4 pr-1 transition focus-within:border-faint focus-within:ring-4 focus-within:ring-[var(--color-glow)]">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={placeholder}
          autoFocus={autoFocus}
          aria-label={placeholder}
          className="h-8 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-faint focus-visible:outline-none"
        />
        <button
          disabled={!text.trim() || sending}
          aria-label={`Send to ${bit.name}`}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-on-primary transition hover:opacity-85 active:scale-95 disabled:opacity-25"
        >
          {sending ? <Loader2 size={15} className="animate-spin" /> : <ArrowUp size={16} strokeWidth={2.4} />}
        </button>
      </div>
      {note && <p className={`mt-1.5 px-1 text-xs ${note.error ? 'text-danger' : 'text-muted'}`}>{note.text}</p>}
    </form>
  )
}

/** "Waiting on you": the questions Bits asked, each with a place to answer. */
export function QuestionsCard({ questions, onAnswered }: { questions: Question[]; onAnswered: () => void }) {
  if (questions.length === 0) return null
  return (
    <Card className="animate-rise overflow-hidden border-warning/30 p-5">
      <CardTitle action={<Pill tone="warn">{questions.length}</Pill>}>
        <span className="flex items-center gap-1.5">
          <MessageCircleQuestion size={14} className="text-warning" />
          Waiting on you
        </span>
      </CardTitle>
      <ul className="flex flex-col gap-4">
        {questions.map((question) => (
          <li key={question.id} className="flex gap-3">
            <BitAvatar state="asking" size={44} />
            <div className="min-w-0 flex-1">
              <p className="text-xs text-muted">
                <span className="font-mono font-semibold text-ink">{question.bit_name}</span> · {question.bit_title} ·{' '}
                {timeAgo(question.created_at)}
              </p>
              <p className="mt-1 text-[15px] leading-snug">{question.text}</p>
              <div className="mt-2.5">
                <BitMessageBox
                  bit={{ id: question.bit_id, name: question.bit_name }}
                  placeholder="Type your answer, or just tell ZENO"
                  onSent={onAnswered}
                />
              </div>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  )
}
