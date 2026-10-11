import { useEffect, useState } from 'react'
import { AudioLines, Check, ExternalLink, Loader2 } from 'lucide-react'
import { api, send } from './api'
import { buttonClass, Card, errorMessage, inputClass, Pill, Switch } from './ui'

type VoiceEngineSettings = {
  has_key: boolean
  key_hint: string | null
  tts_model: string
  listen: boolean
  from_env: boolean
  admin: boolean
  models: { id: string; label: string }[]
}

/**
 * ElevenLabs, optional: lifelike cloud voices for ZENO, and Scribe to do the listening. With a
 * key saved, the account's voices show up in the voice picker.
 */
export default function VoiceEngine({ onSaved }: { onSaved: () => void }) {
  const [saved, setSaved] = useState<VoiceEngineSettings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [listen, setListen] = useState(false)
  const [busy, setBusy] = useState<'test' | 'save' | 'remove' | null>(null)
  const [message, setMessage] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null)

  function load(settings: VoiceEngineSettings) {
    setSaved(settings)
    setModel(settings.tts_model)
    setListen(settings.listen)
    setApiKey('')
  }

  useEffect(() => {
    api<VoiceEngineSettings>('/voice-engine')
      .then(load)
      .catch((e) => setMessage({ tone: 'bad', text: errorMessage(e) }))
  }, [])

  if (!saved) {
    return (
      <Card className="flex h-32 items-center justify-center p-5 text-muted">
        <Loader2 className="animate-spin" />
      </Card>
    )
  }

  const readOnly = !saved.admin
  const changed = apiKey !== '' || model !== saved.tts_model || listen !== saved.listen

  async function test() {
    setBusy('test')
    setMessage(null)
    try {
      const result = await api<{ ok: boolean; detail: string }>('/voice-engine/test', send('POST', { api_key: apiKey || null }))
      setMessage({ tone: result.ok ? 'ok' : 'bad', text: result.detail })
    } catch (e) {
      setMessage({ tone: 'bad', text: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }

  async function save(removeKey = false) {
    setBusy(removeKey ? 'remove' : 'save')
    setMessage(null)
    try {
      const body = { api_key: removeKey ? '' : apiKey || null, tts_model: model, listen: removeKey ? false : listen }
      const settings = await api<VoiceEngineSettings>('/voice-engine', send('PUT', body))
      load(settings)
      setMessage({
        tone: 'ok',
        text: removeKey
          ? 'Removed. ZENO uses its local voices again.'
          : settings.has_key
            ? 'Saved. Pick an ElevenLabs voice above; it applies to your next voice session.'
            : 'Saved.',
      })
      onSaved()
    } catch (e) {
      setMessage({ tone: 'bad', text: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card className="p-5">
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-raised">
          <AudioLines size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="font-semibold">ElevenLabs</h3>
            {saved.has_key ? <Pill tone="ok">Connected</Pill> : <Pill>Off</Pill>}
            {saved.from_env && saved.has_key && <Pill>From .env</Pill>}
          </div>
          <p className="mt-0.5 text-sm text-muted">
            Lifelike cloud voices for ZENO, and optionally its listening too. Without it, ZENO speaks and listens with
            local models on the server.
          </p>
        </div>
      </div>

      <fieldset disabled={readOnly} className="mt-4 flex flex-col gap-4">
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[13px] font-medium">API key</span>
            <a
              href="https://elevenlabs.io/app/settings/api-keys"
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1 text-xs text-muted hover:text-ink"
            >
              Get a key <ExternalLink size={11} />
            </a>
          </div>
          <input
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(e) => {
              setApiKey(e.target.value)
              setMessage(null)
            }}
            placeholder={saved.has_key ? `Saved (${saved.key_hint ?? 'hidden'}). Type to replace` : 'Paste your ElevenLabs API key'}
            className={`${inputClass} font-mono text-[13px]`}
          />
          {saved.has_key && (
            <p className="mt-1.5 flex items-center gap-1 text-xs text-muted">
              <Check size={12} className="text-success" /> Stored encrypted on the server; never shown again.
            </p>
          )}
        </div>

        <div>
          <span className="mb-1.5 block text-[13px] font-medium">Voice model</span>
          <div className="flex flex-col gap-1.5">
            {saved.models.map((option) => (
              <label
                key={option.id}
                className={`flex cursor-pointer items-center gap-3 rounded-xl border px-3 py-2 text-sm transition ${
                  model === option.id ? 'border-ink' : 'border-line hover:border-faint'
                }`}
              >
                <input
                  type="radio"
                  name="elevenlabs-model"
                  checked={model === option.id}
                  onChange={() => setModel(option.id)}
                  className="accent-[var(--color-ink)]"
                />
                <span>{option.label}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-[13px] font-medium">Listen with ElevenLabs too</p>
            <p className="text-xs text-muted">Scribe turns your speech into text in the cloud, instead of Whisper on the server.</p>
          </div>
          <Switch checked={listen} onChange={setListen} label="Listen with ElevenLabs Scribe" disabled={readOnly} />
        </div>
      </fieldset>

      {message && <p className={`mt-4 text-sm ${message.tone === 'ok' ? 'text-success' : 'text-danger'}`}>{message.text}</p>}
      {readOnly && <p className="mt-4 text-sm text-muted">Only an admin can change the voice engine.</p>}

      {!readOnly && (
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          {saved.has_key && (
            <button onClick={() => save(true)} disabled={busy !== null} className={buttonClass('ghost')}>
              {busy === 'remove' && <Loader2 size={15} className="animate-spin" />}
              Remove key
            </button>
          )}
          <button onClick={test} disabled={busy !== null || (!apiKey && !saved.has_key)} className={buttonClass('outline')}>
            {busy === 'test' && <Loader2 size={15} className="animate-spin" />}
            Test
          </button>
          <button onClick={() => save()} disabled={busy !== null || !changed} className={buttonClass('primary')}>
            {busy === 'save' && <Loader2 size={15} className="animate-spin" />}
            Save
          </button>
        </div>
      )}
    </Card>
  )
}
