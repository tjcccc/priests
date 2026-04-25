// ---------------------------------------------------------------------------
// Session / UI meta types
// ---------------------------------------------------------------------------

export interface SessionSummary {
  id: string
  profile_name: string
  created_at: string
  updated_at: string
  turn_count: number
  pinned: boolean
}

export interface Turn {
  role: string
  content: string
  timestamp: string
  model?: string
  elapsed_ms?: number
}

export interface SessionDetail {
  id: string
  profile_name: string
  created_at: string
  updated_at: string
  turns: Turn[]
}

export interface UIMeta {
  session_titles: Record<string, string>
  profile_emojis: Record<string, string>
  pinned_sessions: string[]
}

export interface ModelOption {
  provider: string
  model: string
}

export interface ProviderInfo {
  name: string
  label: string
  known_models: string[] | null  // null = dynamic (Ollama), [] = free text
}

export interface ModelsConfig {
  default_provider: string | null
  default_model: string | null
  configured_options: ModelOption[]
  providers: ProviderInfo[]
}

export interface StreamMeta {
  model?: string
}

export interface UploadResult {
  uuid: string
  url: string
}

export interface SessionUploadItem {
  uuid: string
  url: string
  media_type: string
}

export interface SessionUploads {
  by_turn: Record<string, SessionUploadItem[]>
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

export async function fetchSessions(): Promise<SessionSummary[]> {
  const r = await fetch('/v1/sessions')
  if (!r.ok) throw new Error(`Failed to fetch sessions: ${r.status}`)
  return r.json()
}

export async function fetchSession(id: string): Promise<SessionDetail> {
  const r = await fetch(`/v1/sessions/${id}`)
  if (!r.ok) throw new Error(`Failed to fetch session: ${r.status}`)
  return r.json()
}

export async function fetchUIMeta(): Promise<UIMeta> {
  try {
    const r = await fetch('/v1/ui/meta')
    if (!r.ok) return { session_titles: {}, profile_emojis: {}, pinned_sessions: [] }
    return r.json()
  } catch {
    return { session_titles: {}, profile_emojis: {}, pinned_sessions: [] }
  }
}

export async function deleteSession(id: string): Promise<void> {
  await fetch(`/v1/sessions/${id}`, { method: 'DELETE' })
}

export async function pinSession(id: string): Promise<{ pinned: boolean }> {
  const r = await fetch(`/v1/ui/sessions/${id}/pin`, { method: 'PUT' })
  if (!r.ok) throw new Error(`Pin failed: ${r.status}`)
  return r.json()
}

export async function putSessionTitle(id: string, title: string): Promise<void> {
  await fetch(`/v1/ui/sessions/${id}/title`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
}

export async function putProfileEmoji(name: string, emoji: string): Promise<void> {
  await fetch(`/v1/ui/profiles/${name}/emoji`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ emoji }),
  })
}

export async function uploadImage(params: {
  data: string
  media_type: string
  session_id: string
  batch_id: string
}): Promise<UploadResult> {
  const r = await fetch('/v1/uploads', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!r.ok) throw new Error(`Upload failed: ${r.status}`)
  return r.json()
}

export async function fetchSessionUploads(sessionId: string): Promise<SessionUploads> {
  try {
    const r = await fetch(`/v1/sessions/${sessionId}/uploads`)
    if (!r.ok) return { by_turn: {} }
    return r.json()
  } catch {
    return { by_turn: {} }
  }
}

export async function fetchModels(): Promise<ModelsConfig> {
  try {
    const r = await fetch('/v1/ui/models')
    if (!r.ok) return { default_provider: null, default_model: null, configured_options: [], providers: [] }
    return r.json()
  } catch {
    return { default_provider: null, default_model: null, configured_options: [], providers: [] }
  }
}

// ---------------------------------------------------------------------------
// Config API types
// ---------------------------------------------------------------------------

export interface ProviderConfigData {
  base_url: string
  api_key: string   // "" if unset; "••••••" if set (masked)
  use_proxy: boolean
}

export interface ProviderRegistryItem {
  name: string
  label: string
  needs_api_key: boolean
  default_base_url: string
  known_models: string[] | null
}

export interface ConfigData {
  defaults: {
    provider: string | null
    model: string | null
    profile: string
    timeout_seconds: number
    max_output_tokens: number | null
    think: boolean
  }
  providers: Record<string, ProviderConfigData>
  memory: {
    size_limit: number
    context_limit: number
    flat_line_cap: number
  }
  web_search: {
    enabled: boolean
    max_results: number
  }
  service: {
    host: string
    port: number
  }
  paths: {
    profiles_dir: string
    sessions_db: string
    uploads_dir: string
    log_file: string | null
  }
  registry: ProviderRegistryItem[]
}

export async function fetchConfig(): Promise<ConfigData> {
  const r = await fetch('/v1/config')
  if (!r.ok) throw new Error(`Failed to fetch config: ${r.status}`)
  return r.json()
}

export async function patchConfig(updates: Record<string, string>): Promise<{ needs_restart: boolean }> {
  const r = await fetch('/v1/config', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ updates }),
  })
  if (!r.ok) {
    const text = await r.text()
    throw new Error(`Config update failed: ${text}`)
  }
  return r.json()
}

// ---------------------------------------------------------------------------
// Streaming chat
// ---------------------------------------------------------------------------

export interface ChatParams {
  prompt: string
  session_id?: string
  profile?: string
  no_think?: boolean
  provider?: string | null
  model?: string | null
  upload_uuids?: string[]
}

export async function streamChat(
  params: ChatParams,
  onDelta: (delta: string) => void,
  onDone: (meta: StreamMeta) => void,
  onError: (msg: string) => void,
): Promise<void> {
  const body: Record<string, unknown> = {
    prompt: params.prompt,
    profile: params.profile ?? 'default',
    no_think: params.no_think ?? false,
    create_session_if_missing: true,
  }
  if (params.session_id) body.session_id = params.session_id
  if (params.provider) body.provider = params.provider
  if (params.model) body.model = params.model
  if (params.upload_uuids?.length) body.upload_uuids = params.upload_uuids

  let r: Response
  try {
    r = await fetch('/v1/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch (e) {
    onError(String(e))
    return
  }

  if (!r.ok) {
    onError(`HTTP ${r.status}`)
    return
  }

  const reader = r.body!.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let collectedMeta: StreamMeta = {}

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const lines = buf.split('\n')
    buf = lines.pop() ?? ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const payload = line.slice(6)
      if (payload === '[DONE]') {
        onDone(collectedMeta)
        return
      }
      try {
        const obj = JSON.parse(payload) as Record<string, unknown>
        if (typeof obj.delta === 'string') onDelta(obj.delta)
        if (obj.metadata && typeof obj.metadata === 'object') {
          collectedMeta = { ...collectedMeta, ...(obj.metadata as StreamMeta) }
        }
        if (obj.error && typeof obj.error === 'object') {
          const err = obj.error as Record<string, unknown>
          onError(String(err.message ?? 'Unknown error'))
          return
        }
      } catch { /* ignore malformed lines */ }
    }
  }
  onDone(collectedMeta)
}
