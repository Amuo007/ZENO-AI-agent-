/** Dispatched on window when the server says the session is gone, so the app shows sign-in. */
export const SIGNED_OUT_EVENT = 'zeno:signed-out'

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json', ...init.headers } : init?.headers,
  })
  if (response.status === 401) window.dispatchEvent(new Event(SIGNED_OUT_EVENT))
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => (typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)))
      .catch(() => null)
    throw new Error(detail ?? `HTTP ${response.status}`)
  }
  return response.json()
}

export const send = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
})

// --- MCP store ---------------------------------------------------------------------------------

export type Account = { id: string; name: string | null; healthy: boolean; app: string }

export type McpServer = {
  id: string
  kind: 'pipedream' | 'custom'
  name: string
  app_slug: string | null
  icon: string | null
  url: string | null
  enabled: boolean
  tools: string[] | null
  has_token: boolean
  accounts?: Account[]
}

export type McpTool = { name: string; description: string; enabled: boolean }

export type StoreApp = {
  slug: string
  name: string
  description: string
  icon: string | null
  /** False: Pipedream has no ready-made actions for it, so only Bits can use it (via its API). */
  has_tools: boolean
  api: boolean
}

// --- Bits --------------------------------------------------------------------------------------

export type BitStatus =
  | 'waiting'
  | 'starting'
  | 'running'
  | 'asking'
  | 'sleeping'
  | 'paused'
  | 'done'
  | 'failed'
  | 'stopped'

/** A question a Bit asked the user and is waiting on. */
export type Question = {
  id: number
  bit_id: string
  bit_name: string
  bit_title: string
  text: string
  created_at: number
}

export type Bit = {
  id: string
  slot: number
  name: string
  title: string
  task: string
  status: BitStatus
  wait_for: string[]
  activity: string | null
  result: string | null
  error: string | null
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number
  total_tokens: number
  llm_calls: number
  tool_calls: number
  model: string | null
  source: 'zeno' | 'dashboard'
  created_at: number
  started_at: number | null
  finished_at: number | null
  elapsed: number
  question: Question | null
}

export type BitEventKind =
  | 'status'
  | 'think'
  | 'tool'
  | 'output'
  | 'error'
  | 'result'
  | 'question'
  | 'answer'
  | 'message'
  | 'update'

export type BitEvent = { id: number; at: number; kind: BitEventKind; text: string }

export type MessageResult = { result: 'answered' | 'delivered' | 'resumed' | 'started'; text: string; bit: Bit }

export const messageBit = (bitId: string, text: string) =>
  api<MessageResult>(`/bits/${bitId}/message`, send('POST', { text }))

/** A script a Bit saved for every Bit to use. */
export type Tool = {
  name: string
  description: string
  usage: string
  files: string[]
  made_by: string
  bit_id: string | null
  uses: number
  created_at: number
  updated_at: number
}

export type ToolDetail = Tool & { sources: { name: string; size: number; text: string | null }[] }

export type Storage = { used: number; quota: number; folders: { name: string; bytes: number }[] }

export type Sandbox = {
  available: boolean
  detail: string
  image: string
  building: boolean
  network: boolean
  cpus: string
  memory: string
}

export type TokenTotals = { tasks: number; prompt_tokens: number; completion_tokens: number; cached_tokens: number }

export type BitsOverview = {
  bits: Bit[]
  max_bits: number
  model: string
  sandbox: Sandbox
  storage: Storage
  tokens: { today: TokenTotals; all_time: TokenTotals }
}

export type SharedFile = { name: string; bit_name: string; bit_id: string | null; size: number; created_at: number }

export const ACTIVE_STATUSES: BitStatus[] = ['waiting', 'starting', 'running', 'asking', 'sleeping']

// --- Memory ------------------------------------------------------------------------------------

export type MemoryKind = 'profile' | 'preference' | 'work' | 'people' | 'other'

export type Memory = {
  id: string
  kind: MemoryKind
  text: string
  source: 'learned' | 'told' | 'added'
  created_at: number
  updated_at: number
}

export type Conversation = {
  id: string
  started_at: number
  ended_at: number | null
  title: string | null
  summary: string | null
  message_count: number
}

export type ConversationDetail = Conversation & { messages: { role: 'user' | 'assistant'; text: string }[] }

// --- AI models ---------------------------------------------------------------------------------

export type Provider = {
  id: string
  label: string
  kind: 'openai' | 'anthropic' | 'google'
  needs_key: boolean
  editable_url: boolean
  base_url: string | null
  suggested: string | null
  key_url: string | null
}

export type ModelSlot = {
  provider: string
  model: string
  base_url: string | null
  has_key: boolean
  key_hint: string | null
  effort: string
  extra: Record<string, unknown>
  from_env: boolean
}

export type ModelSettings = {
  admin: boolean
  providers: Provider[]
  efforts: string[]
  slots: { zeno: ModelSlot; bits: ModelSlot }
}
