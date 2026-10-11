import { useEffect, useRef, useState } from 'react'
import { Pause, Play } from 'lucide-react'
import { api } from './api'
import { buttonClass, Card, inputClass } from './ui'

type Voice = { id: string; engine: string; name: string; description: string }

/** Lets the user pick and preview ZENO's voice; applies to the next voice session. */
export default function VoicePicker() {
  const [voices, setVoices] = useState<Voice[]>([])
  const [selected, setSelected] = useState<string>('')
  const [previewing, setPreviewing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    api<{ selected: string; voices: Voice[] }>('/voices')
      .then((data) => {
        setVoices(data.voices)
        setSelected(data.selected)
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  async function choose(voice: string) {
    setSelected(voice)
    setError(null)
    try {
      await api('/voice', { method: 'PUT', body: JSON.stringify({ voice }) })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  function preview() {
    audioRef.current?.pause()
    const audio = new Audio(`/api/voices/${encodeURIComponent(selected)}/sample`)
    audioRef.current = audio
    setPreviewing(true)
    audio.onended = audio.onerror = () => setPreviewing(false)
    audio.play().catch(() => setPreviewing(false))
  }

  const engines = [...new Set(voices.map((v) => v.engine))]

  return (
    <Card className="p-5">
      <div className="flex gap-2">
        <select
          value={selected}
          onChange={(e) => choose(e.target.value)}
          aria-label="ZENO's voice"
          className={`${inputClass} min-w-0 flex-1`}
        >
          {engines.map((engine) => (
            <optgroup key={engine} label={engine}>
              {voices
                .filter((v) => v.engine === engine)
                .map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name} — {v.description}
                  </option>
                ))}
            </optgroup>
          ))}
        </select>
        <button
          onClick={preview}
          disabled={!selected || previewing}
          className={buttonClass('outline')}
          aria-label="Preview voice"
          title="Preview voice"
        >
          {previewing ? <Pause size={16} /> : <Play size={16} />}
        </button>
      </div>
      <p className="mt-2 text-xs text-muted">Applies to your next voice session, on web and Android.</p>
      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
    </Card>
  )
}
