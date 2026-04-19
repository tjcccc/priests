import { useState, useEffect, useRef, useCallback } from 'react'
import { marked } from 'marked'
import {
  fetchSessions, fetchSession, fetchUIMeta, fetchModels,
  putSessionTitle, putProfileEmoji, streamChat, uploadImage, fetchSessionUploads,
  SessionSummary, UIMeta, ModelsConfig, StreamMeta,
} from './api'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const EMOJIS = [
  '😀','😃','😄','😁','😅','😂','🤣','😊','🙂','🙃',
  '😍','🥰','😘','😜','😏','🤔','🤩','🥳','😎','🧐',
  '😴','🤗','😮','🥹','😢','😤','🫡','🤫','😐','🫠',
  '👋','👍','👎','👏','🙌','🤝','🫶','💪','🤞','✌️',
  '🐶','🐱','🦊','🐻','🐼','🦁','🐯','🐸','🦋','🦄',
  '⭐','🌟','✨','🔥','💫','⚡','🌈','☀️','🌙','❄️',
  '🌸','🌺','🌿','🍀','🎉','🎈','🎁','🏆','🎯','🚀',
  '❤️','🧡','💛','💚','💙','💜','🖤','🤍','💔','💯',
]

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
  imagePreviews?: string[]  // /v1/uploads/{uuid} URLs (or data URLs for optimistic display)
}

interface AttachedImage {
  tempId: string       // local key; never sent to server
  uuid: string | null  // null while upload is in flight
  data: string         // base64 (kept until send for upload payload)
  media_type: string
  preview: string      // data URL — shown immediately before upload completes
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
  return Array.from(map.entries()).map(([name, slist]) => ({ name, sessions: slist, expanded: false }))
}

function renderMd(text: string): string {
  return String(marked.parse(text))
}

// Normalize a timestamp string to ms-since-epoch for reliable comparison
// across different ISO format variations (Z vs +00:00, microseconds, etc.)
function tsMs(iso: string): number {
  return new Date(iso).getTime()
}

// ---------------------------------------------------------------------------
// EmojiPicker component
// ---------------------------------------------------------------------------

function EmojiPicker({ onPick, onClose }: { onPick: (e: string) => void; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  return (
    <div
      ref={ref}
      className="absolute top-full left-0 mt-2 z-50 bg-white/95 backdrop-blur-xl rounded-2xl border border-black/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.12)] p-3 w-[280px]"
    >
      <div className="grid grid-cols-10 gap-0.5">
        {EMOJIS.map(em => (
          <button
            key={em}
            onClick={() => { onPick(em); onClose() }}
            className="text-[20px] w-[26px] h-[26px] flex items-center justify-center rounded-md hover:bg-black/[0.06] transition-colors"
          >
            {em}
          </button>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export default function App() {
  // Session list
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [selectedProfile, setSelectedProfile] = useState<string>('default')
  const [selectedSession, setSelectedSession] = useState<SessionSummary | null>(null)

  // Stable UUID for a new session before first send
  const [pendingSessionId, setPendingSessionId] = useState<string>(() => crypto.randomUUID())

  // Server-side UI meta (titles + emojis)
  const [uiMeta, setUiMeta] = useState<UIMeta>({ session_titles: {}, profile_emojis: {} })

  // Header: title editing
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')

  // Header: emoji picker
  const [showEmojiPicker, setShowEmojiPicker] = useState(false)

  // Chat
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')

  // Files — attached for current turn
  const [attachedImages, setAttachedImages] = useState<AttachedImage[]>([])
  const [isDragOver, setIsDragOver] = useState(false)

  // UUIDs of all uploads sent in this session so far (for image context accumulation)
  const [sessionImageUUIDs, setSessionImageUUIDs] = useState<string[]>([])

  // Model selection
  const [modelsConfig, setModelsConfig] = useState<ModelsConfig | null>(null)
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null)
  const [selectedModel, setSelectedModel] = useState<string | null>(null)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const titleInputRef = useRef<HTMLInputElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // ---------------------------------------------------------------------------
  // Derived values
  // ---------------------------------------------------------------------------

  const currentTitle = selectedSession
    ? (uiMeta.session_titles[selectedSession.id] ?? formatTs(selectedSession.created_at))
    : ''

  const currentEmoji = uiMeta.profile_emojis[selectedProfile] ?? '🙂'

  const configuredProviders = [...new Set((modelsConfig?.configured_options ?? []).map(o => o.provider))]
  if (modelsConfig?.default_provider && !configuredProviders.includes(modelsConfig.default_provider)) {
    configuredProviders.unshift(modelsConfig.default_provider)
  }
  const activeProvider = selectedProvider ?? modelsConfig?.default_provider ?? null
  const modelsForProvider = (modelsConfig?.configured_options ?? [])
    .filter(o => o.provider === activeProvider)
    .map(o => o.model)
  const activeModel = selectedModel ?? modelsForProvider[0] ?? modelsConfig?.default_model ?? null

  const hasUploadingImages = attachedImages.some(img => img.uuid === null)

  // ---------------------------------------------------------------------------
  // Load on mount
  // ---------------------------------------------------------------------------

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

  useEffect(() => {
    loadSessions()
    fetchUIMeta().then(setUiMeta)
    fetchModels().then(setModelsConfig)
  }, [loadSessions])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingContent])

  // ---------------------------------------------------------------------------
  // Session actions
  // ---------------------------------------------------------------------------

  const selectSession = async (session: SessionSummary) => {
    setSelectedProfile(session.profile_name)
    setSelectedSession(session)
    setAttachedImages([])
    setSessionImageUUIDs([])
    try {
      const [detail, uploads] = await Promise.all([
        fetchSession(session.id),
        fetchSessionUploads(session.id),
      ])

      // Build a map: turn_timestamp (ms) → upload URL list
      // Timestamps from the DB use the same clock, so normalizing to ms is safe
      const byTurnMs = new Map<number, string[]>()
      for (const [ts, items] of Object.entries(uploads.by_turn)) {
        if (ts === '__pending__') continue
        const ms = tsMs(ts)
        if (!isNaN(ms)) {
          byTurnMs.set(ms, items.map(u => u.url))
        }
      }

      // Collect all known upload UUIDs as session context
      const allUUIDs = Object.entries(uploads.by_turn)
        .filter(([ts]) => ts !== '__pending__')
        .flatMap(([, items]) => items.map(u => u.uuid))
      setSessionImageUUIDs(allUUIDs)

      setMessages(detail.turns.map(t => ({
        role: t.role as 'user' | 'assistant',
        content: t.content,
        timestamp: t.timestamp,
        imagePreviews: byTurnMs.get(tsMs(t.timestamp)),
      })))
    } catch (e) {
      console.error('Failed to load session', e)
    }
  }

  const newSession = (profile: string) => {
    setSelectedProfile(profile)
    setSelectedSession(null)
    setMessages([])
    setSessionImageUUIDs([])
    setAttachedImages([])
    setPendingSessionId(crypto.randomUUID())
    textareaRef.current?.focus()
  }

  const toggleProfile = (name: string) => {
    setProfiles(prev => prev.map(p => p.name === name ? { ...p, expanded: !p.expanded } : p))
  }

  // ---------------------------------------------------------------------------
  // Title editing
  // ---------------------------------------------------------------------------

  const startEditTitle = () => {
    if (!selectedSession) return
    setTitleDraft(currentTitle)
    setEditingTitle(true)
    setTimeout(() => titleInputRef.current?.select(), 0)
  }

  const commitTitle = () => {
    if (!selectedSession) return
    const v = titleDraft.trim() || currentTitle
    setUiMeta(prev => ({
      ...prev,
      session_titles: { ...prev.session_titles, [selectedSession.id]: v },
    }))
    putSessionTitle(selectedSession.id, v).catch(console.error)
    setEditingTitle(false)
  }

  // ---------------------------------------------------------------------------
  // Emoji picker
  // ---------------------------------------------------------------------------

  const pickEmoji = (em: string) => {
    setUiMeta(prev => ({
      ...prev,
      profile_emojis: { ...prev.profile_emojis, [selectedProfile]: em },
    }))
    putProfileEmoji(selectedProfile, em).catch(console.error)
  }

  // ---------------------------------------------------------------------------
  // File attachment — uploads to server immediately for persistence
  // ---------------------------------------------------------------------------

  const processImageFiles = useCallback((files: File[]) => {
    const sessionId = selectedSession?.id ?? pendingSessionId
    const batchId = crypto.randomUUID()
    const imageFiles = Array.from(files).filter(f => f.type.startsWith('image/'))

    imageFiles.forEach(file => {
      const tempId = crypto.randomUUID()
      const reader = new FileReader()
      reader.onload = ev => {
        const dataUrl = ev.target?.result as string
        const media_type = file.type || 'image/jpeg'
        const data = dataUrl.split(',')[1] ?? ''

        setAttachedImages(prev => [...prev, { tempId, uuid: null, data, media_type, preview: dataUrl }])

        uploadImage({ data, media_type, session_id: sessionId, batch_id: batchId })
          .then(result => {
            setAttachedImages(prev =>
              prev.map(img => img.tempId === tempId ? { ...img, uuid: result.uuid } : img)
            )
          })
          .catch(err => {
            console.error('Upload failed', err)
            // Remove the image if upload fails
            setAttachedImages(prev => prev.filter(img => img.tempId !== tempId))
          })
      }
      reader.readAsDataURL(file)
    })
  }, [selectedSession, pendingSessionId])

  const handleFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    processImageFiles(Array.from(e.target.files ?? []))
    e.target.value = ''
  }

  const removeImage = (tempId: string) => {
    setAttachedImages(prev => prev.filter(img => img.tempId !== tempId))
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(true)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
    processImageFiles(Array.from(e.dataTransfer.files))
  }

  // ---------------------------------------------------------------------------
  // Send
  // ---------------------------------------------------------------------------

  const sendMessage = async () => {
    const text = input.trim()
    if (!text || streaming || hasUploadingImages) return
    setInput('')

    const isNew = !selectedSession
    const sessionId = selectedSession?.id ?? pendingSessionId

    const newUUIDs = attachedImages.map(img => img.uuid).filter(Boolean) as string[]
    const allUUIDs = [...sessionImageUUIDs, ...newUUIDs]
    const newPreviews = attachedImages.map(img => `/v1/uploads/${img.uuid}`)
    setAttachedImages([])

    const userTs = new Date().toISOString()

    setMessages(prev => [...prev, {
      role: 'user',
      content: text,
      timestamp: userTs,
      imagePreviews: newPreviews.length ? newPreviews : undefined,
    }])
    setStreaming(true)
    setStreamingContent('')

    const t0 = Date.now()
    let accumulated = ''

    await streamChat(
      {
        prompt: text,
        session_id: sessionId,
        profile: selectedProfile,
        no_think: !thinking,
        provider: activeProvider,
        model: activeModel,
        upload_uuids: allUUIDs.length ? allUUIDs : undefined,
      },
      delta => {
        accumulated += delta
        setStreamingContent(accumulated)
      },
      (meta: StreamMeta) => {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: accumulated,
          timestamp: new Date().toISOString(),
          model: meta.model,
          elapsed_ms: Date.now() - t0,
        }])
        setStreamingContent('')
        setStreaming(false)
        if (newUUIDs.length) {
          setSessionImageUUIDs(prev => [...prev, ...newUUIDs])
        }
        loadSessions().then(sessions => {
          if (isNew) {
            const found = sessions.find(s => s.id === sessionId)
            if (found) {
              setSelectedSession(found)
              // pendingSessionId stays; newSession() will reset it when needed
            }
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
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex h-screen bg-[#f5f5f7] font-[system-ui,-apple-system,BlinkMacSystemFont,'SF_Pro','Helvetica_Neue',sans-serif]">

      {/* ── Sidebar ─────────────────────────────────────────────────── */}
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
                        selectedSession?.id === session.id
                          ? 'bg-[#007AFF]/10 text-[#007AFF] font-medium'
                          : 'text-black/70 hover:bg-black/[0.04] hover:text-black'
                      }`}
                    >
                      {uiMeta.session_titles[session.id] ?? formatTs(session.created_at)}
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

      {/* ── Main ────────────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0">

        {/* Header */}
        <header className="h-[72px] bg-white/60 backdrop-blur-xl border-b border-black/[0.06] flex items-center justify-between px-6 shrink-0">
          <div className="flex items-center gap-2 relative">
            <button
              onClick={() => setShowEmojiPicker(v => !v)}
              title="Change profile emoji"
              className="text-[22px] leading-none hover:opacity-70 transition-opacity select-none"
            >
              {currentEmoji}
            </button>
            {showEmojiPicker && (
              <EmojiPicker
                onPick={pickEmoji}
                onClose={() => setShowEmojiPicker(false)}
              />
            )}
            <span className="text-[17px] font-semibold text-black">{selectedProfile}</span>
          </div>

          {selectedSession && (
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
                {currentTitle}
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
                    {msg.imagePreviews && msg.imagePreviews.length > 0 && (
                      <div className="flex flex-wrap gap-2 mb-2">
                        {msg.imagePreviews.map((src, j) => (
                          <img key={j} src={src} alt="" className="max-h-40 rounded-xl object-cover" />
                        ))}
                      </div>
                    )}
                    {msg.content && (
                      <p className="text-[15px] leading-[1.4] whitespace-pre-wrap">{msg.content}</p>
                    )}
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
            <div
              className={`bg-white/90 backdrop-blur-xl rounded-[20px] border shadow-[0_2px_16px_rgba(0,0,0,0.06)] px-5 pt-4 pb-3 transition-colors ${
                isDragOver ? 'border-[#007AFF]/60 bg-[#007AFF]/[0.03]' : 'border-black/[0.08]'
              }`}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
            >
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

              {/* Image previews */}
              {attachedImages.length > 0 && (
                <div className="flex flex-wrap gap-2 mb-3">
                  {attachedImages.map(img => (
                    <div key={img.tempId} className="relative group">
                      <img
                        src={img.preview}
                        alt=""
                        className={`w-16 h-16 object-cover rounded-lg border border-black/[0.08] transition-opacity ${img.uuid === null ? 'opacity-50' : ''}`}
                      />
                      {img.uuid === null && (
                        <div className="absolute inset-0 flex items-center justify-center rounded-lg">
                          <svg className="w-4 h-4 text-black/50 animate-spin" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                          </svg>
                        </div>
                      )}
                      {img.uuid !== null && (
                        <button
                          onClick={() => removeImage(img.tempId)}
                          className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-black/60 text-white rounded-full text-[11px] flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                        >
                          ✕
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* Controls row */}
              <div className="flex items-center justify-between mt-2 pt-3 border-t border-black/[0.06]">
                <div className="flex items-center gap-3">
                  {/* Add files */}
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    title="Attach image"
                    className="w-7 h-7 rounded-lg hover:bg-black/[0.05] flex items-center justify-center transition-colors"
                  >
                    <svg className="w-5 h-5 text-black/40" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                      <path d="M12 5v14M5 12h14" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </button>
                  <input ref={fileInputRef} type="file" accept="image/*" multiple className="hidden" onChange={handleFiles} />

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
                </div>

                <div className="flex items-center gap-2">
                  {/* Provider dropdown */}
                  {configuredProviders.length > 0 && (
                    <select
                      value={activeProvider ?? ''}
                      onChange={e => {
                        const p = e.target.value
                        setSelectedProvider(p)
                        setSessionImageUUIDs([])
                        const first = (modelsConfig?.configured_options ?? [])
                          .find(o => o.provider === p)?.model ?? null
                        setSelectedModel(first)
                      }}
                      className="bg-black/[0.04] hover:bg-black/[0.06] text-[12px] text-black/70 px-2.5 py-1.5 rounded-lg border-none outline-none cursor-pointer transition-colors max-w-[110px] truncate"
                    >
                      {configuredProviders.map(p => (
                        <option key={p} value={p}>{p}</option>
                      ))}
                    </select>
                  )}

                  {/* Model dropdown */}
                  {modelsForProvider.length > 0 && (
                    <select
                      value={activeModel ?? ''}
                      onChange={e => setSelectedModel(e.target.value)}
                      className="bg-black/[0.04] hover:bg-black/[0.06] text-[12px] text-black/70 px-2.5 py-1.5 rounded-lg border-none outline-none cursor-pointer transition-colors max-w-[160px] truncate"
                    >
                      {modelsForProvider.map(m => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  )}

                  {/* Send */}
                  <button
                    onClick={sendMessage}
                    disabled={!input.trim() || streaming || hasUploadingImages}
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
    </div>
  )
}
