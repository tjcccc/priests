import { useState, useEffect, useRef, useCallback } from 'react'
import { marked } from 'marked'
import { fetchSessions, fetchSession, streamChat, SessionSummary, StreamMeta } from './api'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Profile {
  name: string
  sessions: SessionSummary[]
  expanded: boolean
}

interface Message {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  model?: string
  elapsed_ms?: number
}

// ---------------------------------------------------------------------------
// localStorage helpers (custom session titles + profile emojis)
// ---------------------------------------------------------------------------

const SESSION_META_KEY = 'priests_session_meta'
const PROFILE_META_KEY = 'priests_profile_meta'

function loadMeta<T>(key: string): Record<string, T> {
  try { return JSON.parse(localStorage.getItem(key) ?? '{}') } catch { return {} }
}
function saveMeta<T>(key: string, data: Record<string, T>) {
  localStorage.setItem(key, JSON.stringify(data))
}

function getSessionTitle(id: string, createdAt: string): string {
  const meta = loadMeta<{ title?: string }>(SESSION_META_KEY)
  return meta[id]?.title ?? formatTs(createdAt)
}
function setSessionTitleMeta(id: string, title: string) {
  const meta = loadMeta<{ title?: string }>(SESSION_META_KEY)
  saveMeta(SESSION_META_KEY, { ...meta, [id]: { ...meta[id], title } })
}

function getProfileEmoji(name: string): string {
  const meta = loadMeta<{ emoji?: string }>(PROFILE_META_KEY)
  return meta[name]?.emoji ?? '🙂'
}
function setProfileEmojiMeta(name: string, emoji: string) {
  const meta = loadMeta<{ emoji?: string }>(PROFILE_META_KEY)
  saveMeta(PROFILE_META_KEY, { ...meta, [name]: { ...meta[name], emoji } })
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTs(iso: string): string {
  try {
    const d = new Date(iso)
    const p = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  } catch { return iso }
}

function fmtElapsed(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function groupByProfile(sessions: SessionSummary[]): Profile[] {
  const map = new Map<string, SessionSummary[]>()
  for (const s of sessions) {
    const arr = map.get(s.profile_name) ?? []
    arr.push(s)
    map.set(s.profile_name, arr)
  }
  return Array.from(map.entries()).map(([name, slist]) => ({
    name,
    sessions: slist,
    expanded: true,
  }))
}

function renderMd(text: string): string {
  return String(marked.parse(text))
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function App() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [selectedProfile, setSelectedProfile] = useState<string>('default')
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null)

  // Header: editable session title
  const [sessionTitle, setSessionTitle] = useState<string>('')
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')

  // Header: editable profile emoji
  const [emoji, setEmoji] = useState(() => getProfileEmoji('default'))
  const [editingEmoji, setEditingEmoji] = useState(false)
  const [emojiDraft, setEmojiDraft] = useState('')

  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const titleInputRef = useRef<HTMLInputElement>(null)
  const emojiInputRef = useRef<HTMLInputElement>(null)

  // Load and group sessions; returns the raw list for callers that need it
  const loadSessions = useCallback(async (): Promise<SessionSummary[]> => {
    try {
      const sessions = await fetchSessions()
      setProfiles(prev => {
        const fresh = groupByProfile(sessions)
        return fresh.map(p => {
          const old = prev.find(op => op.name === p.name)
          return old ? { ...p, expanded: old.expanded } : p
        })
      })
      return sessions
    } catch (e) {
      console.error('Failed to load sessions', e)
      return []
    }
  }, [])

  useEffect(() => { loadSessions() }, [loadSessions])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingContent])

  // Keep emoji in sync when profile changes
  useEffect(() => {
    setEmoji(getProfileEmoji(selectedProfile))
  }, [selectedProfile])

  // ---------------------------------------------------------------------------
  // Session selection
  // ---------------------------------------------------------------------------

  const selectSession = async (session: SessionSummary) => {
    setSelectedProfile(session.profile_name)
    setSelectedSessionId(session.id)
    setEmoji(getProfileEmoji(session.profile_name))
    setSessionTitle(getSessionTitle(session.id, session.created_at))
    try {
      const detail = await fetchSession(session.id)
      setMessages(detail.turns.map(t => ({
        role: t.role as 'user' | 'assistant',
        content: t.content,
        timestamp: t.timestamp,
      })))
    } catch (e) {
      console.error('Failed to load session', e)
    }
  }

  const newSession = (profile: string) => {
    setSelectedProfile(profile)
    setSelectedSessionId(null)
    setSessionTitle('')
    setMessages([])
    textareaRef.current?.focus()
  }

  // ---------------------------------------------------------------------------
  // Send message
  // ---------------------------------------------------------------------------

  const sendMessage = async () => {
    const text = input.trim()
    if (!text || streaming) return
    setInput('')

    const isNew = !selectedSessionId
    const sessionId = selectedSessionId ?? crypto.randomUUID()
    if (isNew) setSelectedSessionId(sessionId)

    setMessages(prev => [...prev, { role: 'user', content: text, timestamp: new Date().toISOString() }])
    setStreaming(true)
    setStreamingContent('')

    const t0 = Date.now()
    let accumulated = ''

    await streamChat(
      { prompt: text, session_id: sessionId, profile: selectedProfile, no_think: !thinking },
      delta => {
        accumulated += delta
        setStreamingContent(accumulated)
      },
      (meta: StreamMeta) => {
        const elapsed_ms = Date.now() - t0
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: accumulated,
          timestamp: new Date().toISOString(),
          model: meta.model,
          elapsed_ms,
        }])
        setStreamingContent('')
        setStreaming(false)
        // Reload sessions; after load, resolve new session title from created_at
        loadSessions().then(sessions => {
          if (isNew) {
            const found = sessions.find(s => s.id === sessionId)
            if (found) setSessionTitle(getSessionTitle(sessionId, found.created_at))
          }
        })
      },
      err => {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: `*Error: ${err}*`,
          timestamp: new Date().toISOString(),
          elapsed_ms: Date.now() - t0,
        }])
        setStreamingContent('')
        setStreaming(false)
      },
    )
  }

  // ---------------------------------------------------------------------------
  // Inline edits: title + emoji
  // ---------------------------------------------------------------------------

  const startEditTitle = () => {
    if (!selectedSessionId) return
    setTitleDraft(sessionTitle)
    setEditingTitle(true)
    setTimeout(() => titleInputRef.current?.select(), 0)
  }

  const commitTitle = () => {
    if (!selectedSessionId) return
    const v = titleDraft.trim() || sessionTitle
    setSessionTitle(v)
    setSessionTitleMeta(selectedSessionId, v)
    setEditingTitle(false)
  }

  const startEditEmoji = () => {
    setEmojiDraft(emoji)
    setEditingEmoji(true)
    setTimeout(() => { emojiInputRef.current?.select() }, 0)
  }

  const commitEmoji = () => {
    const v = emojiDraft.trim() || '🙂'
    setEmoji(v)
    setProfileEmojiMeta(selectedProfile, v)
    setEditingEmoji(false)
  }

  const toggleProfile = (name: string) => {
    setProfiles(prev => prev.map(p => p.name === name ? { ...p, expanded: !p.expanded } : p))
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex h-screen bg-[#f5f5f7] font-[system-ui,-apple-system,BlinkMacSystemFont,'SF_Pro','Helvetica_Neue',sans-serif]">

      {/* ── Sidebar ─────────────────────────────────────────────────────── */}
      <aside className="w-[280px] bg-white/80 backdrop-blur-xl border-r border-black/[0.06] flex flex-col shrink-0">
        <div className="px-5 pt-6 pb-4">
          <h1 className="text-[28px] font-semibold tracking-tight text-black">Priests</h1>
        </div>

        <div className="flex-1 overflow-y-auto px-3">
          <div className="text-[11px] font-semibold tracking-wide text-black/40 uppercase px-2 mb-2 mt-2">
            Profiles
          </div>

          {profiles.length === 0 && (
            <p className="px-2 py-3 text-[12px] text-black/40">No sessions yet</p>
          )}

          {profiles.map(profile => (
            <div key={profile.name} className="mb-1">
              <div className="flex items-center gap-1 px-1">
                <button
                  onClick={() => toggleProfile(profile.name)}
                  className="flex items-center gap-2 flex-1 px-2 py-1.5 rounded-lg hover:bg-black/[0.04] transition-colors text-left"
                >
                  <svg
                    className="w-4 h-4 text-black/40 shrink-0 transition-transform duration-150"
                    style={{ transform: profile.expanded ? 'rotate(0deg)' : 'rotate(-90deg)' }}
                    fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"
                  >
                    <path d="M19 9l-7 7-7-7" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  <span className="text-[13px] font-medium text-black">{profile.name}</span>
                </button>
                <button
                  onClick={() => newSession(profile.name)}
                  title="New session"
                  className="w-6 h-6 rounded-md hover:bg-black/[0.06] flex items-center justify-center transition-colors shrink-0"
                >
                  <svg className="w-3.5 h-3.5 text-black/40" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                    <path d="M12 5v14M5 12h14" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
              </div>

              {profile.expanded && (
                <div className="ml-6 mt-1 space-y-0.5">
                  {profile.sessions.map(session => (
                    <button
                      key={session.id}
                      onClick={() => selectSession(session)}
                      className={`w-full text-left px-2 py-1.5 rounded-md text-[12px] transition-colors truncate ${
                        selectedSessionId === session.id
                          ? 'bg-[#007AFF]/10 text-[#007AFF] font-medium'
                          : 'text-black/70 hover:bg-black/[0.04] hover:text-black'
                      }`}
                    >
                      {getSessionTitle(session.id, session.created_at)}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="p-3 border-t border-black/[0.06]">
          <button
            disabled
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-black/30 cursor-not-allowed"
            title="Configuration — coming soon"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
              <path d="M12 3h7a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-7m0-18H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h7m0-18v18" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span className="text-[13px] font-medium">Configuration</span>
          </button>
        </div>
      </aside>

      {/* ── Main ────────────────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0">

        {/* Header */}
        <header className="h-[72px] bg-white/60 backdrop-blur-xl border-b border-black/[0.06] flex items-center justify-between px-6 shrink-0">
          {/* Left: emoji (editable) + profile name */}
          <div className="flex items-center gap-2">
            {editingEmoji ? (
              <input
                ref={emojiInputRef}
                value={emojiDraft}
                onChange={e => setEmojiDraft(e.target.value)}
                onBlur={commitEmoji}
                onKeyDown={e => { if (e.key === 'Enter') commitEmoji() }}
                className="w-9 text-center text-[20px] bg-transparent border-b border-[#007AFF] outline-none"
              />
            ) : (
              <button
                onClick={startEditEmoji}
                title="Click to change profile emoji"
                className="text-[20px] hover:opacity-70 transition-opacity select-none"
              >
                {emoji}
              </button>
            )}
            <span className="text-[17px] font-semibold text-black">{selectedProfile}</span>
          </div>

          {/* Right: editable session title */}
          {selectedSessionId && (
            editingTitle ? (
              <input
                ref={titleInputRef}
                value={titleDraft}
                onChange={e => setTitleDraft(e.target.value)}
                onBlur={commitTitle}
                onKeyDown={e => { if (e.key === 'Enter') commitTitle() }}
                className="text-[13px] text-black/70 font-medium bg-transparent border-b border-[#007AFF] outline-none min-w-[180px] text-right"
              />
            ) : (
              <button
                onClick={startEditTitle}
                title="Click to rename"
                className="text-[13px] text-black/50 font-medium hover:text-black/70 transition-colors"
              >
                {sessionTitle}
              </button>
            )
          )}
        </header>

        {/* Messages */}
        <main className="flex-1 overflow-y-auto px-6 py-6">
          <div className="max-w-[900px] mx-auto space-y-6">
            {messages.length === 0 && !streaming && (
              <div className="flex items-center justify-center min-h-[200px]">
                <p className="text-[15px] text-black/30">Start a conversation</p>
              </div>
            )}

            {messages.map((msg, i) => (
              <div key={i} className={msg.role === 'user' ? 'flex justify-end' : ''}>
                {msg.role === 'user' ? (
                  <div className="bg-[#007AFF] text-white rounded-[18px] px-4 py-3 max-w-[560px] shadow-sm">
                    <p className="text-[15px] leading-[1.4] whitespace-pre-wrap">{msg.content}</p>
                  </div>
                ) : (
                  <div>
                    <div className="bg-black/[0.04] rounded-[18px] px-5 py-4 max-w-[720px]">
                      <div
                        className="chat-content text-[15px] leading-[1.6] text-black/90"
                        dangerouslySetInnerHTML={{ __html: renderMd(msg.content) }}
                      />
                    </div>
                    <div className="flex items-center justify-between mt-2 px-4 max-w-[720px]">
                      <span className="text-[11px] text-black/40">
                        {formatTs(msg.timestamp)}
                        {msg.model && ` · ${msg.model}`}
                        {msg.elapsed_ms != null && ` · ${fmtElapsed(msg.elapsed_ms)}`}
                      </span>
                      <button
                        onClick={() => navigator.clipboard.writeText(msg.content)}
                        className="p-1.5 hover:bg-black/[0.04] rounded-md transition-colors group"
                        title="Copy"
                      >
                        <svg className="w-4 h-4 text-black/30 group-hover:text-black/50" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24">
                          <rect x="8" y="8" width="12" height="12" rx="2" />
                          <path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" />
                        </svg>
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}

            {/* Streaming bubble */}
            {streaming && (
              <div>
                <div className="bg-black/[0.04] rounded-[18px] px-5 py-4 max-w-[720px]">
                  {streamingContent ? (
                    <>
                      <div
                        className="chat-content text-[15px] leading-[1.6] text-black/90"
                        dangerouslySetInnerHTML={{ __html: renderMd(streamingContent) }}
                      />
                      <span className="inline-block w-[2px] h-4 bg-black/50 animate-pulse ml-0.5 align-middle rounded-sm" />
                    </>
                  ) : (
                    <div className="flex gap-1">
                      {[0, 1, 2].map(i => (
                        <div key={i} className="w-2 h-2 rounded-full bg-black/30 animate-bounce"
                          style={{ animationDelay: `${i * 0.15}s` }} />
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </main>

        {/* Input */}
        <div className="px-6 pb-6 shrink-0">
          <div className="max-w-[900px] mx-auto">
            <div className="bg-white/90 backdrop-blur-xl rounded-[20px] border border-black/[0.08] shadow-[0_2px_16px_rgba(0,0,0,0.06)] px-5 pt-4 pb-3">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault()
                    sendMessage()
                  }
                }}
                placeholder="Message  (Ctrl+Enter to send)"
                rows={3}
                className="w-full bg-transparent resize-none outline-none text-[15px] text-black placeholder:text-black/25 min-h-[60px] max-h-[200px]"
              />
              <div className="flex items-center justify-between mt-2 pt-3 border-t border-black/[0.06]">
                {/* Thinking toggle */}
                <label className="flex items-center gap-2 cursor-pointer group">
                  <div className={`w-5 h-5 rounded border transition-all flex items-center justify-center ${
                    thinking ? 'bg-[#007AFF] border-[#007AFF]' : 'bg-white border-black/20 group-hover:border-black/30'
                  }`}>
                    {thinking && (
                      <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 12 12">
                        <path d="M2 6l2.5 2.5L10 3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )}
                  </div>
                  <span className="text-[13px] text-black/70 select-none">Thinking</span>
                  <input type="checkbox" className="sr-only" checked={thinking} onChange={e => setThinking(e.target.checked)} />
                </label>

                <button
                  onClick={sendMessage}
                  disabled={!input.trim() || streaming}
                  className="flex items-center gap-2 bg-[#007AFF] hover:bg-[#0051D5] disabled:bg-black/20 disabled:cursor-not-allowed text-white px-4 py-2 rounded-xl text-[13px] font-medium transition-colors"
                >
                  {streaming ? (
                    <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  ) : (
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                      <path d="M22 2L11 13M22 2L15 22l-4-9-9-4 20-7z" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                  Send
                </button>
              </div>
            </div>
          </div>
        </div>

      </div>
    </div>
  )
}
