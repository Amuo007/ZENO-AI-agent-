import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, type McpServer, type McpTool, type StoreApp } from './api'
import { buttonClass, Card, EmptyState, inputClass, PageHeader, Pill, Switch, toolLabel } from './ui'

export default function Store() {
  const [servers, setServers] = useState<McpServer[]>([])
  const [pipedream, setPipedream] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const data = await api<{ pipedream: boolean; servers: McpServer[] }>('/mcp')
      setServers(data.servers)
      setPipedream(data.pipedream)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  // Accounts are connected in another tab, so refresh when the user comes back.
  useEffect(() => {
    refresh()
    window.addEventListener('focus', refresh)
    return () => window.removeEventListener('focus', refresh)
  }, [refresh])

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Apps"
        subtitle="Connect your apps and MCP servers, then choose which tools ZENO and the Bits may use. Changes apply to the next voice session or task."
      />
      {error && <p className="text-sm text-danger">{error}</p>}

      <section>
        <h2 className="mb-3 text-[15px] font-semibold">My connectors</h2>
        {servers.length === 0 ? (
          <EmptyState title="No apps connected yet">Add Gmail, Outlook, your calendar or any MCP server below.</EmptyState>
        ) : (
          <div className="flex flex-col gap-3">
            {servers.map((server) => (
              <ServerCard key={server.id} server={server} onChange={refresh} />
            ))}
          </div>
        )}
      </section>

      {pipedream && <AppCatalog added={servers} onAdded={refresh} />}
      <CustomServerForm onAdded={refresh} />
    </div>
  )
}

function ServerCard({ server, onChange }: { server: McpServer; onChange: () => void }) {
  const [tools, setTools] = useState<McpTool[] | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const account = server.accounts?.[0]
  const needsAccount = server.kind === 'pipedream' && !account

  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
      onChange()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function loadTools() {
    setError(null)
    try {
      setTools(await api<McpTool[]>(`/mcp/${server.id}/tools`))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  function toggleOpen() {
    setOpen(!open)
    if (!open && !tools) loadTools()
  }

  async function connect() {
    // Open the tab synchronously so the browser doesn't treat it as a blocked popup.
    const tab = window.open('', '_blank')
    try {
      const { url } = await api<{ url: string }>(`/mcp/${server.id}/connect`, { method: 'POST' })
      if (tab) tab.location.href = url
      else window.location.href = url
    } catch (e) {
      tab?.close()
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function setTool(name: string, enabled: boolean) {
    if (!tools) return
    const next = tools.map((t) => (t.name === name ? { ...t, enabled } : t))
    setTools(next)
    await run(() =>
      api(`/mcp/${server.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ tools: next.filter((t) => t.enabled).map((t) => t.name) }),
      }),
    )
  }

  const enabledCount = tools?.filter((t) => t.enabled).length

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center gap-3">
        <AppIcon icon={server.icon} name={server.name} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-medium">{server.name}</span>
            <span className="rounded-md bg-raised px-1.5 py-0.5 text-[11px] text-muted">
              {server.kind === 'pipedream' ? 'Pipedream' : 'Custom'}
            </span>
          </div>
          <p className="truncate text-xs text-muted">
            {server.kind === 'custom'
              ? server.url
              : account
                ? `Connected as ${account.name ?? 'account'}${account.healthy ? '' : ' (needs reconnect)'}`
                : 'No account connected'}
          </p>
        </div>
        <Switch
          checked={server.enabled}
          disabled={busy}
          onChange={(enabled) =>
            run(() => api(`/mcp/${server.id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) }))
          }
        />
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-sm">
        {needsAccount && (
          <button onClick={connect} className={buttonClass('primary', 'sm')}>
            Connect account
          </button>
        )}
        {account && (
          <button
            onClick={() => run(() => api(`/accounts/${account.id}`, { method: 'DELETE' }))}
            disabled={busy}
            className={buttonClass('secondary', 'sm')}
          >
            Disconnect
          </button>
        )}
        <button onClick={toggleOpen} className={buttonClass('secondary', 'sm')}>
          {open ? 'Hide tools' : 'Choose tools'}
          {enabledCount !== undefined && ` · ${enabledCount}/${tools?.length} on`}
        </button>
        <button
          onClick={() => run(() => api(`/mcp/${server.id}`, { method: 'DELETE' }))}
          disabled={busy}
          className={`${buttonClass('ghost', 'sm')} ml-auto hover:text-danger`}
        >
          Remove
        </button>
      </div>

      {error && <p className="mt-2 text-sm text-danger">{error}</p>}

      {open && (
        <div className="mt-4 border-t border-line pt-3">
          {!tools && !error && <p className="text-sm text-muted">Loading tools…</p>}
          {tools && (
            <ul className="flex flex-col gap-1">
              {tools.map((tool) => (
                <li key={tool.name}>
                  <label className="flex cursor-pointer gap-3 rounded-lg p-2 hover:bg-raised">
                    <input
                      type="checkbox"
                      checked={tool.enabled}
                      onChange={(e) => setTool(tool.name, e.target.checked)}
                      className="mt-1 h-4 w-4 accent-[var(--color-ink)]"
                    />
                    <span className="min-w-0">
                      <span className="block text-sm text-ink">{toolLabel(tool.name).split(' · ').pop()}</span>
                      <span className="block text-xs text-muted">{tool.description}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  )
}

function AppCatalog({ added, onAdded }: { added: McpServer[]; onAdded: () => void }) {
  const [query, setQuery] = useState('')
  const [apps, setApps] = useState<StoreApp[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const timer = setTimeout(async () => {
      try {
        const results = await api<StoreApp[]>(`/store/apps?q=${encodeURIComponent(query)}`)
        if (!cancelled) {
          setApps(results)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }, 300)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [query])

  async function add(slug: string) {
    try {
      await api('/mcp/pipedream', { method: 'POST', body: JSON.stringify({ app_slug: slug }) })
      onAdded()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const addedSlugs = new Set(added.map((s) => s.app_slug))

  return (
    <section>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-[15px] font-semibold">Add apps</h2>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search 3,000+ apps…"
          className={`${inputClass} sm:w-72`}
        />
      </div>
      {error && <p className="mb-2 text-sm text-danger">{error}</p>}
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">{query ? 'Search results' : 'Featured'}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {apps?.map((app) => (
          <div key={app.slug} className="flex gap-3 rounded-[22px] border border-line bg-surface p-4 shadow-[var(--shadow-card)]">
            <AppIcon icon={app.icon} name={app.name} />
            <div className="min-w-0 flex-1">
              <p className="flex items-center gap-1.5 text-sm font-medium">
                <span className="truncate">{app.name}</span>
                {app.has_tools === false && (
                  <span title="No ready-made tools for ZENO yet. Once you connect it, Bits can use its API from code.">
                    <Pill>API for Bits</Pill>
                  </span>
                )}
              </p>
              <p className="line-clamp-2 text-xs text-muted">{app.description}</p>
            </div>
            <button
              onClick={() => add(app.slug)}
              disabled={addedSlugs.has(app.slug)}
              className={`${buttonClass('secondary', 'sm')} self-center`}
            >
              {addedSlugs.has(app.slug) ? 'Added' : 'Add'}
            </button>
          </div>
        ))}
        {apps?.length === 0 && <p className="text-sm text-muted">No apps found.</p>}
      </div>
    </section>
  )
}

function CustomServerForm({ onAdded }: { onAdded: () => void }) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    try {
      await api('/mcp/custom', { method: 'POST', body: JSON.stringify({ name, url, token: token || null }) })
      setName('')
      setUrl('')
      setToken('')
      setError(null)
      onAdded()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const input = inputClass

  return (
    <Card className="p-5">
      <h2 className="mb-1 text-[15px] font-semibold">Custom MCP server</h2>
      <p className="mb-3 text-xs text-muted">Any MCP server reachable over streamable HTTP.</p>
      <form onSubmit={submit} className="grid gap-2 sm:grid-cols-2">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" required className={input} />
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://example.com/mcp"
          required
          className={input}
        />
        <input
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Bearer token (optional)"
          type="password"
          className={input}
        />
        <button className={buttonClass('primary')}>
          Add server
        </button>
      </form>
      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
    </Card>
  )
}

function AppIcon({ icon, name }: { icon: string | null; name: string }) {
  return icon ? (
    <img src={icon} alt="" className="h-9 w-9 shrink-0 rounded-lg border border-line bg-white object-contain p-1.5" />
  ) : (
    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-raised text-sm font-medium">
      {name.slice(0, 1).toUpperCase()}
    </div>
  )
}
