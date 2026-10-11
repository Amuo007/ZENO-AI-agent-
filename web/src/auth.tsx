import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, SIGNED_OUT_EVENT } from './api'
import { ZenoAvatar } from './Avatar'
import { isDark } from './theme'

export type User = { id: string; email: string; name: string | null; picture: string | null; admin: boolean }

type AuthValue = { user: User; signOut: () => Promise<void> }

const AuthContext = createContext<AuthValue | null>(null)

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthGate>')
  return value
}

/** Renders its children only for a signed-in user; otherwise shows the sign-in page. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null | undefined>(undefined)

  useEffect(() => {
    api<User>('/auth/me')
      .then(setUser)
      .catch(() => setUser(null))
    const onSignedOut = () => setUser(null)
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut)
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut)
  }, [])

  const signOut = useCallback(async () => {
    await api('/auth/logout', { method: 'POST' })
    window.google?.accounts.id.disableAutoSelect()
    setUser(null)
  }, [])

  if (user === undefined) return null
  if (user === null) return <SignIn onSignedIn={setUser} />
  return <AuthContext.Provider value={{ user, signOut }}>{children}</AuthContext.Provider>
}

function SignIn({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const buttonRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([api<{ googleClientId: string }>('/auth/config'), loadGoogleScript()])
      .then(([{ googleClientId }]) => {
        if (cancelled || !buttonRef.current || !window.google) return
        window.google.accounts.id.initialize({
          client_id: googleClientId,
          callback: async ({ credential }) => {
            try {
              const { user } = await api<{ user: User }>('/auth/google', {
                method: 'POST',
                body: JSON.stringify({ credential }),
              })
              onSignedIn(user)
            } catch (e) {
              setError(e instanceof Error ? e.message : String(e))
            }
          },
        })
        window.google.accounts.id.renderButton(buttonRef.current, {
          theme: isDark() ? 'filled_black' : 'outline',
          size: 'large',
          shape: 'pill',
          text: 'signin_with',
        })
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
    return () => {
      cancelled = true
    }
  }, [onSignedIn])

  return (
    <div className="flex min-h-full flex-col items-center justify-center p-6 text-center">
      <div className="animate-rise flex w-full max-w-sm flex-col items-center">
        <ZenoAvatar mood="idle" size={230} />
        <h1 className="mt-4 font-display text-[40px] font-bold tracking-[0.14em]">ZENO</h1>
        <p className="mt-1 text-[15px] text-muted">Your voice-first AI, with a team of Bits on call.</p>
        <div ref={buttonRef} className="mt-9 flex min-h-11 justify-center" />
        {error && <p className="mt-4 text-sm text-danger">{error}</p>}
        <p className="mt-10 max-w-xs text-xs leading-relaxed text-faint">
          Talk to ZENO, and it hands bigger jobs to Bits: AI workers that run code and use your apps in their own sandboxes.
        </p>
      </div>
    </div>
  )
}

let googleScript: Promise<void> | undefined

function loadGoogleScript(): Promise<void> {
  googleScript ??= new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = 'https://accounts.google.com/gsi/client'
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => reject(new Error("Couldn't load Google sign-in"))
    document.head.appendChild(script)
  })
  return googleScript
}

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize(options: { client_id: string; callback: (response: { credential: string }) => void }): void
          renderButton(element: HTMLElement, options: Record<string, string>): void
          disableAutoSelect(): void
        }
      }
    }
  }
}
