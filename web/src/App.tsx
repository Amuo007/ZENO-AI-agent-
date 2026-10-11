import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { RTVIEvent } from '@pipecat-ai/client-js'
import {
  usePipecatClient,
  usePipecatClientMicControl,
  usePipecatClientTransportState,
  usePipecatConversation,
  useRTVIClientEvent,
  type BotOutputText,
  type ConversationMessage,
} from '@pipecat-ai/client-react'
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport'
import {
  ArrowUp,
  AudioLines,
  Blocks,
  Bot,
  Brain,
  Check,
  ChevronRight,
  Info,
  Loader2,
  LogOut,
  Mic,
  MicOff,
  Monitor,
  Moon,
  PhoneOff,
  Settings as SettingsIcon,
  Sun,
  Wrench,
  X,
} from 'lucide-react'
import About from './About'
import { ACTIVE_STATUSES, api, type Bit, type BitsOverview, type Question } from './api'
import { useAuth } from './auth'
import { BitAvatar, VoiceWave, ZenoAvatar, type Mood } from './Avatar'
import Bits from './Bits'
import { bitPhase, QuestionsCard } from './BitTalk'
import MemoryPage from './Memory'
import SettingsPage from './Settings'
import Store from './Store'
import { useTheme, type ThemeChoice } from './theme'
import { LiveElapsed } from './LiveElapsed'
import { buttonClass, Card, CardTitle, errorMessage, Pill, Segmented, StatusDot, toolLabel } from './ui'
import { usePolling } from './usePolling'

type Tab = 'talk' | 'bits' | 'memory' | 'apps' | 'settings' | 'about'

const TABS: { id: Tab; label: string; icon: typeof Mic }[] = [
  { id: 'talk', label: 'Talk', icon: AudioLines },
  { id: 'bits', label: 'Bits', icon: Bot },
  { id: 'memory', label: 'Memory', icon: Brain },
  { id: 'apps', label: 'Apps', icon: Blocks },
  { id: 'settings', label: 'Settings', icon: SettingsIcon },
  { id: 'about', label: 'About', icon: Info },
]

function initialTab(): Tab {
  const hash = window.location.hash.slice(1) as Tab
  return TABS.some((t) => t.id === hash) ? hash : 'talk'
}

export default function App() {
  const [tab, setTabState] = useState<Tab>(initialTab)
  const bits = usePolling(() => api<BitsOverview>('/bits'), tab === 'bits' ? 2000 : 4000)
  const working = bits.data?.bits.filter((b) => ACTIVE_STATUSES.includes(b.status)).length ?? 0

  function setTab(next: Tab) {
    setTabState(next)
    history.replaceState(null, '', `#${next}`)
    window.scrollTo({ top: 0 })
  }

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-30 border-b border-line/70 bg-[var(--color-glass)] backdrop-blur-xl backdrop-saturate-150">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-4 px-4 md:px-8">
          <button onClick={() => setTab('talk')} className="flex items-center gap-2.5">
            <img src="/icons/zeno-64.png" alt="" className="h-8 w-8 rounded-[9px]" />
            <span className="font-display text-[17px] font-semibold tracking-[0.12em]">ZENO</span>
          </button>
          <nav className="hidden md:block">
            <Segmented
              value={tab}
              onChange={setTab}
              options={TABS.map(({ id, label, icon: Icon }) => [
                id,
                <>
                  <Icon size={15} strokeWidth={2} />
                  {label}
                  {id === 'bits' && working > 0 && (
                    <span className="ml-0.5 rounded-full bg-ink px-1.5 text-[11px] font-semibold text-canvas">{working}</span>
                  )}
                </>,
              ] as const)}
            />
          </nav>
          <UserMenu />
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl flex-1 px-4 pb-28 pt-6 md:px-8 md:pb-12">
        {/* The voice session stays mounted while other tabs are open. */}
        <div className={tab === 'talk' ? '' : 'hidden'}>
          <Talk bits={bits.data?.bits ?? []} onOpenBits={() => setTab('bits')} onChange={bits.refresh} />
        </div>
        {tab === 'bits' && <Bits overview={bits.data} error={bits.error} refresh={bits.refresh} />}
        {tab === 'memory' && <MemoryPage />}
        {tab === 'apps' && <Store />}
        {tab === 'settings' && <SettingsPage />}
        {tab === 'about' && <About />}
      </main>

      {/* Phone-sized screens get an iOS-style tab bar instead of the header control. */}
      <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-line/70 bg-[var(--color-glass)] pb-[env(safe-area-inset-bottom)] backdrop-blur-xl md:hidden">
        <div className="mx-auto flex max-w-md justify-around px-2">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`relative flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px] font-medium ${
                tab === id ? 'text-ink' : 'text-faint'
              }`}
            >
              <Icon size={22} strokeWidth={tab === id ? 2.2 : 1.8} />
              {label}
              {id === 'bits' && working > 0 && (
                <span className="absolute right-[22%] top-1 h-2 w-2 rounded-full bg-ink" />
              )}
            </button>
          ))}
        </div>
      </nav>
    </div>
  )
}

function UserMenu() {
  const { user, signOut } = useAuth()
  const client = usePipecatClient()
  const { choice, choose } = useTheme()
  const [open, setOpen] = useState(false)
  const menu = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => !menu.current?.contains(e.target as Node) && setOpen(false)
    const escape = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  async function handleSignOut() {
    await client?.disconnect()
    await signOut()
  }

  return (
    <div ref={menu} className="relative">
      <button onClick={() => setOpen(!open)} className="rounded-full transition active:scale-95" aria-label="Account">
        <UserPicture name={user.name ?? user.email} picture={user.picture} />
      </button>
      {open && (
        <div className="animate-rise absolute right-0 top-12 z-40 w-72 rounded-2xl border border-line bg-surface p-2 shadow-[var(--shadow-float)]">
          <div className="flex items-center gap-3 px-2 py-2">
            <UserPicture name={user.name ?? user.email} picture={user.picture} size={40} />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{user.name ?? user.email}</p>
              <p className="truncate text-xs text-muted">{user.email}</p>
            </div>
          </div>
          <div className="my-1 border-t border-line" />
          <p className="px-2 pb-1.5 pt-2 text-xs font-medium text-muted">Appearance</p>
          <Segmented<ThemeChoice>
            size="sm"
            className="mx-1 flex"
            value={choice}
            onChange={choose}
            options={[
              ['system', <><Monitor size={13} /> Auto</>],
              ['light', <><Sun size={13} /> Light</>],
              ['dark', <><Moon size={13} /> Dark</>],
            ]}
          />
          <div className="my-2 border-t border-line" />
          <button onClick={handleSignOut} className="flex w-full items-center gap-2 rounded-xl px-2 py-2 text-sm text-danger hover:bg-raised">
            <LogOut size={16} />
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}

function UserPicture({ name, picture, size = 32 }: { name: string; picture: string | null; size?: number }) {
  return picture ? (
    <img src={picture} alt="" referrerPolicy="no-referrer" className="rounded-full" style={{ width: size, height: size }} />
  ) : (
    <span className="flex items-center justify-center rounded-full bg-raised text-sm font-medium" style={{ width: size, height: size }}>
      {name.slice(0, 1).toUpperCase()}
    </span>
  )
}

// --- Talk --------------------------------------------------------------------------------------

type Activity = 'idle' | 'listening' | 'thinking' | 'speaking'

function Talk({ bits, onOpenBits, onChange }: { bits: Bit[]; onOpenBits: () => void; onChange: () => void }) {
  const questions = bits
    .filter((b) => ACTIVE_STATUSES.includes(b.status))
    .map((b) => b.question)
    .filter((q): q is Question => q !== null)
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[400px_minmax(0,1fr)]">
      <aside className="flex min-w-0 flex-col gap-6">
        <VoiceStage />
        <QuestionsCard questions={questions} onAnswered={onChange} />
        <BitsAtWork bits={bits} onOpenBits={onOpenBits} />
      </aside>
      <Conversation onBitNews={onChange} />
    </div>
  )
}

const moodLabel: Record<Mood, string> = {
  offline: 'Talk to ZENO',
  idle: 'Listening',
  listening: 'Hearing you',
  thinking: 'Thinking',
  speaking: 'Speaking',
}

function VoiceStage() {
  const client = usePipecatClient()
  const transportState = usePipecatClientTransportState()
  const { enableMic, isMicEnabled } = usePipecatClientMicControl()
  const [activity, setActivity] = useState<Activity>('idle')
  const [error, setError] = useState<string | null>(null)
  // The live audio level of whoever is talking, read by the avatar every frame (no re-renders).
  const level = useRef(0)
  const moodRef = useRef<Mood>('offline')

  const connected = transportState === 'ready' || transportState === 'connected'
  const busy = ['initializing', 'initialized', 'authenticating', 'authenticated', 'connecting', 'disconnecting'].includes(
    transportState,
  )
  const mood: Mood = connected ? activity : 'offline'
  moodRef.current = mood

  useEffect(() => {
    level.current = 0
  }, [mood])

  useRTVIClientEvent(RTVIEvent.UserStartedSpeaking, () => setActivity('listening'))
  useRTVIClientEvent(RTVIEvent.UserStoppedSpeaking, () => setActivity('thinking'))
  useRTVIClientEvent(RTVIEvent.BotStartedSpeaking, () => setActivity('speaking'))
  useRTVIClientEvent(RTVIEvent.BotStoppedSpeaking, () => setActivity('idle'))
  useRTVIClientEvent(RTVIEvent.Disconnected, () => setActivity('idle'))
  useRTVIClientEvent(RTVIEvent.Error, (message) => setError(JSON.stringify(message.data)))
  useRTVIClientEvent(RTVIEvent.LocalAudioLevel, (value: number) => {
    if (moodRef.current === 'listening' || moodRef.current === 'idle') level.current = Math.min(1, value * 2.2)
  })
  useRTVIClientEvent(RTVIEvent.RemoteAudioLevel, (value: number) => {
    if (moodRef.current === 'speaking') level.current = Math.min(1, value * 1.6)
  })

  async function connect() {
    if (!client) return
    setError(null)
    try {
      const config = await api<{ iceServers: RTCIceServer[] }>('/config')
      const transport = client.transport as SmallWebRTCTransport
      transport.iceServers = config.iceServers
      await client.connect({ webrtcRequestParams: { endpoint: '/api/offer' } })
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <Card className="relative overflow-hidden px-6 pb-6 pt-8">
      <div className="flex flex-col items-center text-center">
        <button
          onClick={connected || busy ? undefined : connect}
          className={`relative rounded-[40px] ${connected || busy ? 'cursor-default' : 'cursor-pointer'}`}
          aria-label={connected ? 'ZENO' : 'Start talking to ZENO'}
          tabIndex={connected || busy ? -1 : 0}
        >
          <ZenoAvatar mood={busy ? 'thinking' : mood} level={level} size={250} />
        </button>
        <div className="-mt-1 h-11">
          <VoiceWave level={level} active={connected && (mood === 'speaking' || mood === 'listening')} />
        </div>

        <p className={`mt-1 font-display text-xl font-semibold tracking-tight ${mood === 'thinking' ? 'shimmer-text' : ''}`}>
          {busy ? 'Connecting…' : moodLabel[mood]}
        </p>
        <p className="mt-1 text-sm text-muted">
          {connected
            ? isMicEnabled
              ? mood === 'speaking'
                ? 'Talk any time to interrupt'
                : 'Go ahead, I’m listening'
              : 'Microphone muted'
            : busy
              ? 'Setting up a secure voice line'
              : 'Your voice assistant, with Bits on call'}
        </p>

        <div className="mt-6 flex gap-2.5">
          {connected ? (
            <>
              <button onClick={() => enableMic(!isMicEnabled)} className={buttonClass(isMicEnabled ? 'secondary' : 'primary', 'lg')}>
                {isMicEnabled ? <Mic size={18} /> : <MicOff size={18} />}
                {isMicEnabled ? 'Mute' : 'Unmute'}
              </button>
              <button onClick={() => client?.disconnect()} className={buttonClass('danger-soft', 'lg')}>
                <PhoneOff size={18} />
                End
              </button>
            </>
          ) : (
            <button onClick={connect} disabled={busy} className={buttonClass('primary', 'lg')}>
              {busy ? <Loader2 size={18} className="animate-spin" /> : <Mic size={18} />}
              {busy ? 'Connecting' : 'Start talking'}
            </button>
          )}
        </div>
        {error && <p className="mt-4 break-words text-sm text-danger">{error}</p>}
      </div>
    </Card>
  )
}

function BitsAtWork({ bits, onOpenBits }: { bits: Bit[]; onOpenBits: () => void }) {
  const active = bits.filter((b) => ACTIVE_STATUSES.includes(b.status))
  const recent = bits.filter((b) => !ACTIVE_STATUSES.includes(b.status)).slice(0, 2)

  return (
    <Card className="p-5">
      <CardTitle
        action={
          <button onClick={onOpenBits} className={buttonClass('ghost', 'sm')}>
            All Bits
            <ChevronRight size={15} />
          </button>
        }
      >
        Bits at work
      </CardTitle>
      {active.length === 0 ? (
        <div className="flex items-center gap-3 rounded-2xl bg-raised/60 p-3">
          <div className="flex -space-x-5">
            {[1, 2, 3].map((slot) => (
              <BitAvatar key={slot} state="idle" size={44} />
            ))}
          </div>
          <p className="text-[13px] leading-snug text-muted">
            All Bits are free. Ask ZENO for something bigger, like “research flights to Toronto and save the best three.”
          </p>
        </div>
      ) : (
        <ul className="flex flex-col gap-1">
          {active.map((bit) => {
            const phase = bitPhase(bit)
            return (
              <li key={bit.id}>
                <button onClick={onOpenBits} className="flex w-full items-center gap-3 rounded-2xl p-2 text-left hover:bg-raised">
                  <BitAvatar slot={bit.slot} state={phase.state} size={48} />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{bit.title}</span>
                      {phase.state !== 'working' && <Pill tone={phase.tone}>{phase.label}</Pill>}
                    </span>
                    <span className={`block truncate text-xs ${phase.state === 'working' ? 'shimmer-text' : 'text-muted'}`}>
                      {phase.line}
                    </span>
                  </span>
                  <LiveElapsed bit={bit} />
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {recent.length > 0 && (
        <ul className="mt-3 flex flex-col gap-2 border-t border-line pt-3">
          {recent.map((bit) => (
            <li key={bit.id} className="flex items-start gap-2.5 text-[13px]">
              <span className="mt-0.5">
                <StatusDot tone={bit.status === 'done' ? 'ok' : bit.status === 'failed' ? 'bad' : 'idle'} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="font-medium">{bit.name}</span>
                <span className="text-muted"> · {bit.title}</span>
                <span className="mt-0.5 line-clamp-2 block text-muted">{bit.result ?? bit.error}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

type BitNotice = { type?: string; event?: string; name?: string; title?: string; status?: string; text?: string }

/** What a Bit's news looks like as a small line in the conversation. */
function noticeText(data: BitNotice): string | null {
  switch (data.event ?? 'finished') {
    case 'question':
      return `${data.name} asks: ${data.text}`
    case 'update':
      return `${data.name}: ${data.text}`
    case 'answered':
      return `You answered ${data.name}: ${data.text}`
    case 'finished': {
      const verb = data.status === 'done' ? 'finished' : data.status === 'stopped' ? 'was stopped on' : 'couldn’t finish'
      return `${data.name} ${verb} “${data.title}”`
    }
    default:
      return null
  }
}

function Conversation({ onBitNews }: { onBitNews: () => void }) {
  const client = usePipecatClient()
  const transportState = usePipecatClientTransportState()
  const { messages, injectMessage } = usePipecatConversation()
  const [draft, setDraft] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const ready = transportState === 'ready'

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  // A Bit's news (a question, an update, an answer, its report) shows up as a small notice.
  useRTVIClientEvent(RTVIEvent.ServerMessage, (data: BitNotice) => {
    if (data?.type !== 'bit') return
    onBitNews()
    const text = noticeText(data)
    if (!text) return
    injectMessage({ role: 'system', parts: [{ text, final: true, createdAt: new Date().toISOString() }] })
  })

  async function send(e: FormEvent) {
    e.preventDefault()
    const text = draft.trim()
    if (!text || !client) return
    setDraft('')
    // Typed text isn't echoed back as a transcript, so add it to the conversation ourselves.
    injectMessage({ role: 'user', parts: [{ text, final: true, createdAt: new Date().toISOString() }] })
    await client.sendText(text)
  }

  return (
    <Card className="flex h-[calc(100vh-8.5rem)] min-h-[520px] flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-line px-6 py-3.5">
        <div className="flex items-center gap-2.5">
          <img src="/icons/zeno-64.png" alt="" className="h-7 w-7 rounded-[8px]" />
          <div className="leading-tight">
            <p className="text-sm font-semibold">ZENO</p>
            <p className="flex items-center gap-1.5 text-xs text-muted">
              <StatusDot tone={ready ? 'ok' : 'idle'} />
              {ready ? 'On a call' : 'Offline'}
            </p>
          </div>
        </div>
      </div>
      <div className="scroll-thin flex-1 space-y-4 overflow-y-auto px-5 py-6 md:px-8">
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <p className="font-display text-lg font-semibold">Say hi to ZENO</p>
            <p className="mt-1 max-w-sm text-sm text-muted">
              Start a voice session, then talk or type. Ask about your inbox, your calendar, or hand bigger jobs to a Bit.
            </p>
          </div>
        )}
        {messages.map((message, i) => (
          <Message key={`${message.createdAt}-${i}`} message={message} />
        ))}
        <div ref={bottomRef} />
      </div>
      <form onSubmit={send} className="border-t border-line p-3">
        <div className="flex items-center gap-2 rounded-full border border-line bg-canvas py-1.5 pl-4 pr-1.5 transition focus-within:border-faint">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={!ready}
            placeholder={ready ? 'Message ZENO' : 'Start a voice session to send messages'}
            className="h-8 flex-1 bg-transparent text-[15px] outline-none placeholder:text-faint focus-visible:outline-none disabled:cursor-not-allowed"
          />
          <button
            disabled={!ready || !draft.trim()}
            aria-label="Send"
            className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-on-primary transition hover:opacity-85 active:scale-95 disabled:opacity-25"
          >
            <ArrowUp size={17} strokeWidth={2.4} />
          </button>
        </div>
      </form>
    </Card>
  )
}

const BIT_TOOLS: Record<string, string> = {
  start_bit: 'Handing off to a Bit',
  message_bit: 'Messaging a Bit',
  bit_status: 'Checking on the Bits',
  stop_bit: 'Stopping a Bit',
}

function Message({ message }: { message: ConversationMessage }) {
  if (message.role === 'function_call' && message.functionCall) {
    const call = message.functionCall
    const done = call.status === 'completed'
    return (
      <div className="animate-rise flex items-center gap-2 text-[13px] text-muted">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-raised">
          {BIT_TOOLS[call.function_name ?? ''] ? <Bot size={13} /> : <Wrench size={12} />}
        </span>
        <span>{BIT_TOOLS[call.function_name ?? ''] ?? toolLabel(call.function_name ?? 'tool')}</span>
        {call.cancelled ? (
          <X size={14} className="text-faint" />
        ) : done ? (
          <Check size={14} className="text-success" />
        ) : (
          <Loader2 size={14} className="animate-spin text-faint" />
        )}
      </div>
    )
  }

  const text = message.parts.map((part, i) => (
    <span key={i}>
      {i > 0 && ' '}
      {renderText(part.text)}
    </span>
  ))

  if ((message.role as string) === 'system') {
    return (
      <div className="animate-rise flex justify-center">
        <span className="inline-flex max-w-[88%] items-start gap-1.5 rounded-2xl bg-raised px-3 py-1.5 text-[12.5px] font-medium leading-snug text-muted">
          <Bot size={13} className="mt-px shrink-0" />
          <span>{text}</span>
        </span>
      </div>
    )
  }
  if (message.role === 'user') {
    return (
      <div className="animate-rise flex justify-end">
        <div className="max-w-[78%] rounded-[20px] rounded-br-md bg-primary px-4 py-2.5 text-[15px] leading-relaxed text-on-primary">
          {text}
        </div>
      </div>
    )
  }
  return <div className="animate-rise max-w-[88%] text-[15px] leading-relaxed">{text}</div>
}

function renderText(text: ReactNode | BotOutputText): ReactNode {
  if (text && typeof text === 'object' && 'spoken' in text && 'unspoken' in text) {
    return (
      <>
        {text.spoken}
        <span className="text-muted">{text.unspoken}</span>
      </>
    )
  }
  return text as ReactNode
}
