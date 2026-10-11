import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

export type Health = {
  server: string
  active_sessions: number
  llm: { provider: string; host: string; model: string; reachable: boolean }
  stt: { engine: string; model: string }
  tts: { engine: string; voice: string }
  vad: string
  transport: string
  bits: { model: string; working: number; max: number; sandbox: boolean }
}

export function useHealth() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setHealth(await api<Health>('/health'))
      setError(null)
    } catch (e) {
      setHealth(null)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { health, error, loading, refresh }
}
