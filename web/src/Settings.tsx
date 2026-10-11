import { useEffect, useState, type ReactNode } from 'react'
import { Check, ExternalLink, Loader2, LogOut, Monitor, Moon, RefreshCw, Sun } from 'lucide-react'
import { usePipecatClient } from '@pipecat-ai/client-react'
import { api, send, type ModelSettings, type ModelSlot, type Provider } from './api'
import { useAuth } from './auth'
import { BitAvatar } from './Avatar'
import { useTheme, type ThemeChoice } from './theme'
import {
  buttonClass,
  Card,
  CardTitle,
  errorMessage,
  inputClass,
  PageHeader,
  Pill,
  Segmented,
  StatusDot,
} from './ui'
import { useHealth, type Health } from './useHealth'
import VoicePicker from './VoicePicker'

export default function SettingsPage() {
  const [settings, setSettings] = useState<ModelSettings | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    try {
      setSettings(await api<ModelSettings>('/models'))
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  useEffect(() => {
    load()
  }, [])

  return (
    <div className="flex flex-col gap-8">
      <PageHeader title="Settings" subtitle="Choose the AI behind ZENO and the Bits, its voice, and how the app looks." />

      <section>
        <SectionTitle>AI models</SectionTitle>
        {error && <p className="mb-3 text-sm text-danger">{error}</p>}
        {!settings ? (
          <div className="flex h-40 items-center justify-center text-muted">
            <Loader2 className="animate-spin" />
          </div>
        ) : (
          <>
            {!settings.admin && (
              <p className="mb-3 text-sm text-muted">Only an admin can change the models and their API keys.</p>
            )}
            <div className="grid gap-6 xl:grid-cols-2">
              <ModelSlotCard
                slot="zeno"
                title="ZENO"
                description="The voice assistant you talk to. Replies are spoken, so pick something fast."
                art={<img src="/avatars/zeno-idle.webp" alt="" className="avatar-art h-14 w-14 object-contain" />}
                settings={settings}
                onSaved={load}
              />
              <ModelSlotCard
                slot="bits"
                title="Bits"
                description="The workers. They plan, write code and use tools on their own, so pick something capable."
                art={<BitAvatar state="done" size={56} />}
                settings={settings}
                onSaved={load}
              />
            </div>
          </>
        )}
      </section>

      <div className="grid gap-6 xl:grid-cols-2">
        <section>
          <SectionTitle>Voice</SectionTitle>
          <VoicePicker />
        </section>
        <section>
          <SectionTitle>Appearance</SectionTitle>
          <Appearance />
        </section>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <section>
          <SectionTitle>System</SectionTitle>
          <SystemStatus />
        </section>
        <section>
          <SectionTitle>Account</SectionTitle>
          <Account />
        </section>
      </div>
    </div>
  )
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <h2 className="mb-3 px-1 text-[13px] font-semibold uppercase tracking-[0.06em] text-muted">{children}</h2>
}

// --- AI models ---------------------------------------------------------------------------------

type Draft = {
  provider: string
  model: string
  base_url: string
  api_key: string
  effort: string
  extra: string
}

function draftFrom(slot: ModelSlot): Draft {
  return {
    provider: slot.provider,
    model: slot.model,
    base_url: slot.base_url ?? '',
    api_key: '',
    effort: slot.effort,
    extra: Object.keys(slot.extra ?? {}).length ? JSON.stringify(slot.extra, null, 2) : '',
  }
}

function ModelSlotCard({
  slot,
  title,
  description,
  art,
  settings,
  onSaved,
}: {
  slot: 'zeno' | 'bits'
  title: string
  description: string
  art: ReactNode
  settings: ModelSettings
  onSaved: () => void
}) {
  const saved = settings.slots[slot]
  const [draft, setDraft] = useState<Draft>(() => draftFrom(saved))
  const [models, setModels] = useState<string[]>([])
  const [loadingModels, setLoadingModels] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null)
  const provider = settings.providers.find((p) => p.id === draft.provider) as Provider
  const readOnly = !settings.admin
  const keySaved = saved.has_key && saved.provider === draft.provider
  const changed = JSON.stringify(draft) !== JSON.stringify(draftFrom(saved))

  useEffect(() => setDraft(draftFrom(saved)), [saved])

  function update(patch: Partial<Draft>) {
    setDraft((d) => ({ ...d, ...patch }))
    setMessage(null)
  }

  function chooseProvider(id: string) {
    const next = settings.providers.find((p) => p.id === id) as Provider
    setModels([])
    update({
      provider: id,
      model: id === saved.provider ? saved.model : next.suggested ?? '',
      base_url: id === saved.provider ? saved.base_url ?? next.base_url ?? '' : next.base_url ?? '',
      api_key: '',
      extra: id === saved.provider ? draftFrom(saved).extra : id === 'lemonade' ? JSON.stringify({ chat_template_kwargs: { enable_thinking: false } }, null, 2) : '',
    })
  }

  function body() {
    let extra: Record<string, unknown> | undefined
    if (provider.kind === 'openai' && draft.extra.trim()) {
      extra = JSON.parse(draft.extra)
    } else if (provider.kind === 'openai') {
      extra = {}
    }
    return {
      provider: draft.provider,
      model: draft.model.trim(),
      base_url: provider.editable_url ? draft.base_url.trim() || null : null,
      api_key: draft.api_key ? draft.api_key : null,
      effort: provider.kind === 'anthropic' ? draft.effort : null,
      extra,
    }
  }

  async function loadModels() {
    setLoadingModels(true)
    setMessage(null)
    try {
      const data = await api<{ models: string[] }>(`/models/${slot}/list`, send('POST', body()))
      setModels(data.models)
      setMessage({ tone: 'ok', text: `${data.models.length} models available` })
    } catch (e) {
      setMessage({ tone: 'bad', text: errorMessage(e) })
    } finally {
      setLoadingModels(false)
    }
  }

  async function test() {
    setTesting(true)
    setMessage(null)
    try {
      const result = await api<{ ok: boolean; reply?: string; latency_ms?: number; error?: string }>(
        `/models/${slot}/test`,
        send('POST', body()),
      )
      setMessage(
        result.ok
          ? { tone: 'ok', text: `Works · replied “${result.reply}” in ${((result.latency_ms ?? 0) / 1000).toFixed(1)}s` }
          : { tone: 'bad', text: result.error ?? 'Failed' },
      )
    } catch (e) {
      setMessage({ tone: 'bad', text: errorMessage(e) })
    } finally {
      setTesting(false)
    }
  }

  async function save() {
    setSaving(true)
    setMessage(null)
    try {
      await api(`/models/${slot}`, send('PUT', body()))
      setMessage({ tone: 'ok', text: `Saved. ${slot === 'zeno' ? 'Applies to the next voice session.' : 'Applies to the next task.'}` })
      onSaved()
    } catch (e) {
      setMessage({ tone: 'bad', text: e instanceof SyntaxError ? 'Extra options must be valid JSON' : errorMessage(e) })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="p-6">
      <div className="flex items-start gap-4">
        <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-[20px] bg-raised">{art}</div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="font-display text-xl font-semibold">{title}</h3>
            {saved.from_env && <Pill>From .env</Pill>}
          </div>
          <p className="mt-0.5 text-sm text-muted">{description}</p>
        </div>
      </div>

      <fieldset disabled={readOnly} className="mt-5 flex flex-col gap-4">
        <div>
          <Label>Provider</Label>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {settings.providers.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => chooseProvider(p.id)}
                className={`rounded-2xl border px-3 py-2.5 text-left text-sm transition ${
                  draft.provider === p.id
                    ? 'border-ink bg-ink text-canvas'
                    : 'border-line bg-surface hover:border-faint'
                }`}
              >
                <span className="block font-medium">{p.label.replace(' (local)', '')}</span>
                <span className={`block text-xs ${draft.provider === p.id ? 'opacity-70' : 'text-muted'}`}>
                  {p.needs_key ? 'Cloud · API key' : p.id === 'custom' ? 'Any OpenAI API' : 'Local · free'}
                </span>
              </button>
            ))}
          </div>
        </div>

        {provider.editable_url && (
          <div>
            <Label>Server URL</Label>
            <input
              value={draft.base_url}
              onChange={(e) => update({ base_url: e.target.value })}
              placeholder={provider.base_url ?? 'http://host:port/v1'}
              className={`${inputClass} font-mono text-[13px]`}
            />
          </div>
        )}

        {(provider.needs_key || provider.id === 'custom' || provider.id === 'openai') && (
          <div>
            <Label
              action={
                provider.key_url && (
                  <a href={provider.key_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-muted hover:text-ink">
                    Get a key <ExternalLink size={11} />
                  </a>
                )
              }
            >
              API key
            </Label>
            <input
              type="password"
              autoComplete="off"
              value={draft.api_key}
              onChange={(e) => update({ api_key: e.target.value })}
              placeholder={keySaved ? `Saved (${saved.key_hint ?? 'hidden'}). Type to replace` : provider.needs_key ? 'Paste your API key' : 'Optional'}
              className={`${inputClass} font-mono text-[13px]`}
            />
            {keySaved && (
              <p className="mt-1.5 flex items-center gap-1 text-xs text-muted">
                <Check size={12} className="text-success" /> Stored encrypted on the server; never shown again.
              </p>
            )}
          </div>
        )}

        <div>
          <Label
            action={
              <button type="button" onClick={loadModels} disabled={loadingModels} className="flex items-center gap-1 text-xs text-muted hover:text-ink">
                {loadingModels ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
                Load models
              </button>
            }
          >
            Model
          </Label>
          <input
            value={draft.model}
            onChange={(e) => update({ model: e.target.value })}
            list={`models-${slot}`}
            placeholder={provider.suggested ?? 'Model name'}
            className={`${inputClass} font-mono text-[13px]`}
          />
          <datalist id={`models-${slot}`}>
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </div>

        {provider.kind === 'anthropic' && (
          <div>
            <Label>Effort</Label>
            <Segmented
              size="sm"
              value={draft.effort}
              onChange={(effort) => update({ effort })}
              options={settings.efforts.map((e) => [e, e[0].toUpperCase() + e.slice(1)] as const)}
            />
            <p className="mt-1.5 text-xs text-muted">
              {slot === 'zeno' ? 'Low keeps spoken replies quick.' : 'High suits multi-step work; Max for the hardest tasks.'}
            </p>
          </div>
        )}

        {provider.kind === 'openai' && (
          <details className="group">
            <summary className="cursor-pointer select-none text-sm text-muted hover:text-ink">Advanced request options</summary>
            <textarea
              value={draft.extra}
              onChange={(e) => update({ extra: e.target.value })}
              rows={3}
              placeholder='{"chat_template_kwargs": {"enable_thinking": false}}'
              className={`${inputClass} mt-2 h-auto py-2 font-mono text-[12px]`}
            />
            <p className="mt-1 text-xs text-muted">Extra JSON fields sent with every request, e.g. to turn off “thinking”.</p>
          </details>
        )}
      </fieldset>

      {message && (
        <p className={`mt-4 text-sm ${message.tone === 'ok' ? 'text-success' : 'text-danger'}`}>{message.text}</p>
      )}

      {!readOnly && (
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={test} disabled={testing || !draft.model} className={buttonClass('outline')}>
            {testing && <Loader2 size={15} className="animate-spin" />}
            Test
          </button>
          <button onClick={save} disabled={saving || !changed || !draft.model} className={buttonClass('primary')}>
            {saving && <Loader2 size={15} className="animate-spin" />}
            Save
          </button>
        </div>
      )}
    </Card>
  )
}

function Label({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-1.5 flex items-center justify-between">
      <span className="text-[13px] font-medium">{children}</span>
      {action}
    </div>
  )
}

// --- Other sections ----------------------------------------------------------------------------

function Appearance() {
  const { choice, choose } = useTheme()
  const options: { id: ThemeChoice; label: string; icon: typeof Sun; swatch: string }[] = [
    { id: 'system', label: 'Automatic', icon: Monitor, swatch: 'linear-gradient(135deg, #f5f5f7 50%, #0a0a0b 50%)' },
    { id: 'light', label: 'Light', icon: Sun, swatch: '#f5f5f7' },
    { id: 'dark', label: 'Dark', icon: Moon, swatch: '#0a0a0b' },
  ]
  return (
    <Card className="p-5">
      <div className="grid grid-cols-3 gap-3">
        {options.map(({ id, label, icon: Icon, swatch }) => (
          <button key={id} onClick={() => choose(id)} className="group flex flex-col items-center gap-2">
            <span
              className={`flex h-20 w-full items-end justify-start rounded-2xl border-2 p-2 transition ${
                choice === id ? 'border-ink' : 'border-line group-hover:border-faint'
              }`}
              style={{ background: swatch }}
            >
              <span className="h-2 w-8 rounded-full bg-[#8e8e93]/60" />
            </span>
            <span className={`flex items-center gap-1.5 text-sm ${choice === id ? 'font-medium' : 'text-muted'}`}>
              <Icon size={14} />
              {label}
            </span>
          </button>
        ))}
      </div>
    </Card>
  )
}

function SystemStatus() {
  const { health, error, loading, refresh } = useHealth()
  const rows: [string, ReactNode][] = health ? statusRows(health) : [['Server', <Status tone="bad" label={error ?? 'Offline'} />]]
  return (
    <Card className="p-5">
      <CardTitle
        action={
          <button onClick={refresh} className={buttonClass('ghost', 'sm')} title="Refresh status">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        }
      >
        Status
      </CardTitle>
      <dl className="space-y-2.5 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-3">
            <dt className="shrink-0 whitespace-nowrap text-muted">{label}</dt>
            <dd className="min-w-0 truncate text-right" title={typeof value === 'string' ? value : undefined}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  )
}

function statusRows(health: Health): [string, ReactNode][] {
  return [
    ['Server', <Status tone="ok" label="Online" />],
    ['ZENO’s model', <Status tone={health.llm.reachable ? 'ok' : 'bad'} label={`${health.llm.provider} · ${health.llm.model}`} />],
    ['Bits’ model', health.bits.model],
    ['Bit sandboxes', <Status tone={health.bits.sandbox ? 'ok' : 'bad'} label={health.bits.sandbox ? 'Docker ready' : 'Docker not running'} />],
    ['Speech-to-text', health.stt.model.split('/').pop()],
    ['Voice', `${health.tts.voice} (${health.tts.engine})`],
    ['Voice sessions', health.active_sessions],
  ]
}

function Status({ tone, label }: { tone: 'ok' | 'bad'; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <StatusDot tone={tone} />
      {label}
    </span>
  )
}

function Account() {
  const { user, signOut } = useAuth()
  const client = usePipecatClient()
  return (
    <Card className="flex items-center gap-4 p-5">
      {user.picture ? (
        <img src={user.picture} alt="" referrerPolicy="no-referrer" className="h-12 w-12 rounded-full" />
      ) : (
        <span className="flex h-12 w-12 items-center justify-center rounded-full bg-raised font-medium">
          {(user.name ?? user.email).slice(0, 1).toUpperCase()}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium">{user.name ?? user.email}</p>
        <p className="truncate text-sm text-muted">
          {user.email}
          {user.admin ? ' · Admin' : ''}
        </p>
      </div>
      <button
        onClick={async () => {
          await client?.disconnect()
          await signOut()
        }}
        className={buttonClass('danger-soft', 'sm')}
      >
        <LogOut size={14} />
        Sign out
      </button>
    </Card>
  )
}
