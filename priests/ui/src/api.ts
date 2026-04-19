export interface SessionSummary {
  id: string
  profile_name: string
  created_at: string
  updated_at: string
  turn_count: number
}

export interface Turn {
  role: string
  content: string
  timestamp: string
}

export interface SessionDetail {
  id: string
  profile_name: string
  created_at: string
  updated_at: string
  turns: Turn[]
}

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

export interface ChatParams {
  prompt: string
  session_id?: string
  profile?: string
  no_think?: boolean
}

export async function streamChat(
  params: ChatParams,
  onDelta: (delta: string) => void,
  onDone: () => void,
  onError: (msg: string) => void,
): Promise<void> {
  const body: Record<string, unknown> = {
    prompt: params.prompt,
    profile: params.profile ?? 'default',
    no_think: params.no_think ?? false,
    create_session_if_missing: true,
  }
  if (params.session_id) body.session_id = params.session_id

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
        onDone()
        return
      }
      try {
        const obj = JSON.parse(payload) as Record<string, unknown>
        if (typeof obj.delta === 'string') onDelta(obj.delta)
        if (obj.error && typeof obj.error === 'object') {
          const err = obj.error as Record<string, unknown>
          onError(String(err.message ?? 'Unknown error'))
          return
        }
      } catch { /* ignore malformed SSE lines */ }
    }
  }
  onDone()
}
