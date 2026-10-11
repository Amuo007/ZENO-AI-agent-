import { useCallback, useEffect, useState } from 'react'

export type ThemeChoice = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'zeno:theme'

function readChoice(): ThemeChoice {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

/** Sets <html data-theme> from the saved choice; index.html also runs this before first paint. */
export function applyTheme(choice: ThemeChoice = readChoice()) {
  const root = document.documentElement
  if (choice === 'system') delete root.dataset.theme
  else root.dataset.theme = choice
}

export function isDark(): boolean {
  const theme = document.documentElement.dataset.theme
  if (theme) return theme === 'dark'
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(readChoice)

  const choose = useCallback((next: ThemeChoice) => {
    try {
      if (next === 'system') localStorage.removeItem(STORAGE_KEY)
      else localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // Private mode or blocked storage: the choice still applies for this visit.
    }
    applyTheme(next)
    setChoice(next)
    window.dispatchEvent(new Event('zeno:theme'))
  }, [])

  return { choice, choose }
}

/** Re-renders when the effective theme changes (system change or a manual choice). */
export function useIsDark() {
  const [dark, setDark] = useState(isDark)
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const update = () => setDark(isDark())
    media.addEventListener('change', update)
    window.addEventListener('zeno:theme', update)
    return () => {
      media.removeEventListener('change', update)
      window.removeEventListener('zeno:theme', update)
    }
  }, [])
  return dark
}
