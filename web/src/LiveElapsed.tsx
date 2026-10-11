import { useEffect, useState } from 'react'
import { ACTIVE_STATUSES, type Bit } from './api'
import { formatDuration } from './ui'

/** A Bit's running time, ticking every second between polls. */
export function LiveElapsed({ bit, className = '' }: { bit: Bit; className?: string }) {
  const [, tick] = useState(0)
  const running = ACTIVE_STATUSES.includes(bit.status) && bit.started_at
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => tick((n) => n + 1), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  const seconds = running ? Date.now() / 1000 - (bit.started_at ?? 0) : bit.elapsed
  return <span className={`shrink-0 font-mono text-xs tabular-nums text-muted ${className}`}>{formatDuration(seconds)}</span>
}
