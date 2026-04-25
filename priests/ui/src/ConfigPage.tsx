import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchConfig, patchConfig, ConfigData, ProviderRegistryItem } from './api'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SectionStatus = 'idle' | 'saving' | 'saved' | 'error'

interface SectionState {
  status: SectionStatus
  error?: string
  needsRestart?: boolean
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function StatusBadge({ state }: { state: SectionState }) {
  if (state.status === 'saving') {
    return <span className="text-[12px] text-black/40">Saving…</span>
  }
  if (state.status === 'saved') {
    return (
      <span className="text-[12px] text-[#34C759] font-medium">
        {state.needsRestart ? 'Saved — restart required' : 'Saved'}
      </span>
    )
  }
  if (state.status === 'error') {
    return <span className="text-[12px] text-red-500">{state.error ?? 'Error'}</span>
  }
  return null
}

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white/90 rounded-2xl border border-black/[0.07] shadow-[0_1px_8px_rgba(0,0,0,0.04)] overflow-hidden mb-5">
      <div className="px-6 py-4 border-b border-black/[0.06]">
        <h2 className="text-[15px] font-semibold text-black">{title}</h2>
      </div>
      <div className="px-6 py-5">{children}</div>
    </div>
  )
}

function Field({ label, note, children }: { label: string; note?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-4 py-2">
      <div className="w-[200px] shrink-0 pt-0.5">
        <span className="text-[13px] text-black/70 font-medium">{label}</span>
        {note && <p className="text-[11px] text-black/35 mt-0.5">{note}</p>}
      </div>
      <div className="flex-1">{children}</div>
    </div>
  )
}

function TextInput({ value, onChange, placeholder, masked }: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  masked?: boolean
}) {
  const [show, setShow] = useState(false)
  const type = masked && !show ? 'password' : 'text'
  return (
    <div className="flex gap-1.5">
      <input
        type={type}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="flex-1 px-3 py-1.5 bg-black/[0.03] rounded-lg border border-black/[0.08] text-[13px] text-black outline-none focus:ring-2 focus:ring-[#007AFF]/25 focus:border-[#007AFF]/40 transition-all"
      />
      {masked && (
        <button
          type="button"
          onClick={() => setShow(v => !v)}
          className="px-2.5 py-1.5 rounded-lg border border-black/[0.08] bg-black/[0.03] text-[12px] text-black/50 hover:bg-black/[0.06] transition-colors shrink-0"
        >
          {show ? 'Hide' : 'Show'}
        </button>
      )}
    </div>
  )
}

function NumberInput({ value, onChange, min }: { value: number; onChange: (v: number) => void; min?: number }) {
  return (
    <input
      type="number"
      value={value}
      min={min}
      onChange={e => onChange(Number(e.target.value))}
      className="w-[120px] px-3 py-1.5 bg-black/[0.03] rounded-lg border border-black/[0.08] text-[13px] text-black outline-none focus:ring-2 focus:ring-[#007AFF]/25 focus:border-[#007AFF]/40 transition-all"
    />
  )
}

function Toggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!value)}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${value ? 'bg-[#007AFF]' : 'bg-black/20'}`}
    >
      <span
        className={`inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${value ? 'translate-x-6' : 'translate-x-1'}`}
      />
    </button>
  )
}

function SaveRow({ onSave, state }: { onSave: () => void; state: SectionState }) {
  return (
    <div className="flex items-center justify-between pt-4 mt-2 border-t border-black/[0.06]">
      <StatusBadge state={state} />
      <button
        onClick={onSave}
        disabled={state.status === 'saving'}
        className="px-4 py-1.5 bg-[#007AFF] hover:bg-[#0066CC] disabled:bg-black/20 disabled:cursor-not-allowed text-white rounded-lg text-[13px] font-medium transition-colors"
      >
        Save
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Provider card
// ---------------------------------------------------------------------------

interface ProviderCardProps {
  name: string
  info: ProviderRegistryItem
  baseUrl: string
  apiKey: string
  useProxy: boolean
  onChange: (field: 'base_url' | 'api_key' | 'use_proxy', value: string | boolean) => void
}

function ProviderCard({ name, info, baseUrl, apiKey, useProxy, onChange }: ProviderCardProps) {
  const isLocal = !info.needs_api_key
  return (
    <div className="border border-black/[0.07] rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-[13px] font-semibold text-black">{info.label}</span>
        {isLocal && (
          <span className="text-[10px] px-1.5 py-0.5 bg-[#34C759]/10 text-[#34C759] rounded font-medium">local</span>
        )}
      </div>
      {(isLocal || name === 'custom' || name === 'openrouter') && (
        <div>
          <p className="text-[11px] text-black/40 mb-1">Base URL</p>
          <TextInput
            value={baseUrl}
            onChange={v => onChange('base_url', v)}
            placeholder={info.default_base_url}
          />
        </div>
      )}
      {info.needs_api_key && (
        <div>
          <p className="text-[11px] text-black/40 mb-1">API Key</p>
          <TextInput
            value={apiKey}
            onChange={v => onChange('api_key', v)}
            placeholder="Enter API key…"
            masked
          />
        </div>
      )}
      {info.needs_api_key && (
        <div className="flex items-center gap-3">
          <Toggle value={useProxy} onChange={v => onChange('use_proxy', v)} />
          <span className="text-[12px] text-black/50">Use proxy</span>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main ConfigPage component
// ---------------------------------------------------------------------------

export default function ConfigPage() {
  const navigate = useNavigate()
  const [config, setConfig] = useState<ConfigData | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  // Local draft state per section
  const [defaults, setDefaults] = useState<ConfigData['defaults'] | null>(null)
  const [providerDrafts, setProviderDrafts] = useState<Record<string, { base_url: string; api_key: string; use_proxy: boolean }>>({})
  const [memory, setMemory] = useState<ConfigData['memory'] | null>(null)
  const [webSearch, setWebSearch] = useState<ConfigData['web_search'] | null>(null)
  const [service, setService] = useState<ConfigData['service'] | null>(null)

  // Section save states
  const [defaultsState, setDefaultsState] = useState<SectionState>({ status: 'idle' })
  const [providersState, setProvidersState] = useState<SectionState>({ status: 'idle' })
  const [memoryState, setMemoryState] = useState<SectionState>({ status: 'idle' })
  const [webSearchState, setWebSearchState] = useState<SectionState>({ status: 'idle' })
  const [serviceState, setServiceState] = useState<SectionState>({ status: 'idle' })

  useEffect(() => {
    fetchConfig()
      .then(data => {
        setConfig(data)
        setDefaults(data.defaults)
        // Build provider drafts — replace masked "••••••" with "" so user knows field is set
        const drafts: typeof providerDrafts = {}
        for (const name of Object.keys(data.providers)) {
          const p = data.providers[name]
          drafts[name] = {
            base_url: p.base_url,
            api_key: p.api_key === '••••••' ? '' : p.api_key,
            use_proxy: p.use_proxy,
          }
        }
        setProviderDrafts(drafts)
        setMemory(data.memory)
        setWebSearch(data.web_search)
        setService(data.service)
        setLoading(false)
      })
      .catch(e => {
        setLoadError(String(e))
        setLoading(false)
      })
  }, [])

  // ---------------------------------------------------------------------------
  // Save handlers
  // ---------------------------------------------------------------------------

  const saveDefaults = async () => {
    if (!defaults) return
    setDefaultsState({ status: 'saving' })
    try {
      const updates: Record<string, string> = {
        'default.provider': defaults.provider ?? '',
        'default.model': defaults.model ?? '',
        'default.profile': defaults.profile,
        'default.timeout_seconds': String(defaults.timeout_seconds),
        'default.think': String(defaults.think),
      }
      if (defaults.max_output_tokens != null) {
        updates['default.max_output_tokens'] = String(defaults.max_output_tokens)
      }
      await patchConfig(updates)
      setDefaultsState({ status: 'saved' })
    } catch (e) {
      setDefaultsState({ status: 'error', error: String(e) })
    }
  }

  const saveProviders = async () => {
    setProvidersState({ status: 'saving' })
    try {
      const updates: Record<string, string> = {}
      if (!config) return
      for (const name of Object.keys(providerDrafts)) {
        const draft = providerDrafts[name]
        const info = config.registry.find(r => r.name === name)
        if (!info) continue
        const isLocal = !info.needs_api_key

        if (isLocal || name === 'custom' || name === 'openrouter') {
          updates[`providers.${name}.base_url`] = draft.base_url || info.default_base_url
        }
        if (info.needs_api_key && draft.api_key) {
          // Only send api_key if user typed something (empty = leave unchanged)
          updates[`providers.${name}.api_key`] = draft.api_key
          // Ensure base_url is set
          if (name !== 'anthropic') {
            updates[`providers.${name}.base_url`] = draft.base_url || info.default_base_url
          }
        }
        if (info.needs_api_key) {
          const wasConfigured = config.providers[name]?.api_key === '••••••'
          if (wasConfigured || draft.api_key) {
            updates[`providers.${name}.use_proxy`] = String(draft.use_proxy)
          }
        }
      }
      if (Object.keys(updates).length === 0) {
        setProvidersState({ status: 'idle' })
        return
      }
      await patchConfig(updates)
      setProvidersState({ status: 'saved' })
    } catch (e) {
      setProvidersState({ status: 'error', error: String(e) })
    }
  }

  const saveMemory = async () => {
    if (!memory) return
    setMemoryState({ status: 'saving' })
    try {
      await patchConfig({
        'memory.size_limit': String(memory.size_limit),
        'memory.context_limit': String(memory.context_limit),
        'memory.flat_line_cap': String(memory.flat_line_cap),
      })
      setMemoryState({ status: 'saved' })
    } catch (e) {
      setMemoryState({ status: 'error', error: String(e) })
    }
  }

  const saveWebSearch = async () => {
    if (!webSearch) return
    setWebSearchState({ status: 'saving' })
    try {
      await patchConfig({
        'web_search.enabled': String(webSearch.enabled),
        'web_search.max_results': String(webSearch.max_results),
      })
      setWebSearchState({ status: 'saved' })
    } catch (e) {
      setWebSearchState({ status: 'error', error: String(e) })
    }
  }

  const saveService = async () => {
    if (!service) return
    setServiceState({ status: 'saving' })
    try {
      const result = await patchConfig({
        'service.host': service.host,
        'service.port': String(service.port),
      })
      setServiceState({ status: 'saved', needsRestart: result.needs_restart })
    } catch (e) {
      setServiceState({ status: 'error', error: String(e) })
    }
  }

  // ---------------------------------------------------------------------------
  // Provider draft helpers
  // ---------------------------------------------------------------------------

  const updateProvider = (name: string, field: 'base_url' | 'api_key' | 'use_proxy', value: string | boolean) => {
    setProviderDrafts(prev => ({
      ...prev,
      [name]: { ...prev[name], [field]: value },
    }))
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex h-screen bg-[#f5f5f7] font-[system-ui,-apple-system,BlinkMacSystemFont,'SF_Pro','Helvetica_Neue',sans-serif]">

      {/* Sidebar — slim back button */}
      <aside className="w-[280px] bg-white/80 backdrop-blur-xl border-r border-black/[0.06] flex flex-col shrink-0">
        <div className="px-5 pt-6 pb-4">
          <h1 className="text-[28px] font-semibold tracking-tight text-black">Priests</h1>
        </div>
        <div className="px-3 mt-2">
          <button
            onClick={() => navigate('/ui')}
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-black/50 hover:bg-black/[0.04] hover:text-black/70 transition-colors text-[13px]"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
              <path d="M19 12H5M12 5l-7 7 7 7" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Back to chat
          </button>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-[72px] bg-white/60 backdrop-blur-xl border-b border-black/[0.06] flex items-center px-8 shrink-0">
          <h2 className="text-[20px] font-semibold text-black">Configuration</h2>
        </header>

        <main className="flex-1 overflow-y-auto px-8 py-8">
          <div className="max-w-[760px] mx-auto">

            {loading && (
              <div className="flex items-center justify-center py-20 text-black/30 text-[14px]">
                Loading configuration…
              </div>
            )}

            {loadError && (
              <div className="bg-red-50 border border-red-200 rounded-xl px-5 py-4 text-[13px] text-red-600">
                Failed to load configuration: {loadError}
              </div>
            )}

            {!loading && !loadError && config && defaults && memory && webSearch && service && (

              <>
                {/* ── Defaults ─────────────────────────────────────── */}
                <SectionCard title="Defaults">
                  <Field label="Provider">
                    <select
                      value={defaults.provider ?? ''}
                      onChange={e => setDefaults(d => d ? { ...d, provider: e.target.value || null } : d)}
                      className="px-3 py-1.5 bg-black/[0.03] rounded-lg border border-black/[0.08] text-[13px] text-black outline-none focus:ring-2 focus:ring-[#007AFF]/25 w-[200px]"
                    >
                      <option value="">— none —</option>
                      {config.registry.map(p => (
                        <option key={p.name} value={p.name}>{p.label}</option>
                      ))}
                    </select>
                  </Field>

                  <Field label="Model">
                    <TextInput
                      value={defaults.model ?? ''}
                      onChange={v => setDefaults(d => d ? { ...d, model: v || null } : d)}
                      placeholder="e.g. gpt-4o"
                    />
                  </Field>

                  <Field label="Default profile">
                    <TextInput
                      value={defaults.profile}
                      onChange={v => setDefaults(d => d ? { ...d, profile: v } : d)}
                      placeholder="default"
                    />
                  </Field>

                  <Field label="Timeout (seconds)">
                    <NumberInput
                      value={defaults.timeout_seconds}
                      onChange={v => setDefaults(d => d ? { ...d, timeout_seconds: v } : d)}
                      min={1}
                    />
                  </Field>

                  <Field label="Max output tokens" note="0 or blank = provider default">
                    <NumberInput
                      value={defaults.max_output_tokens ?? 0}
                      onChange={v => setDefaults(d => d ? { ...d, max_output_tokens: v || null } : d)}
                      min={0}
                    />
                  </Field>

                  <Field label="Thinking mode">
                    <Toggle
                      value={defaults.think}
                      onChange={v => setDefaults(d => d ? { ...d, think: v } : d)}
                    />
                  </Field>

                  <SaveRow onSave={saveDefaults} state={defaultsState} />
                </SectionCard>

                {/* ── Providers ────────────────────────────────────── */}
                <SectionCard title="Providers">
                  <div className="space-y-3">
                    {config.registry.map(info => {
                      const draft = providerDrafts[info.name] ?? { base_url: '', api_key: '', use_proxy: false }
                      return (
                        <ProviderCard
                          key={info.name}
                          name={info.name}
                          info={info}
                          baseUrl={draft.base_url}
                          apiKey={draft.api_key}
                          useProxy={draft.use_proxy}
                          onChange={(field, value) => updateProvider(info.name, field, value)}
                        />
                      )
                    })}
                  </div>
                  <SaveRow onSave={saveProviders} state={providersState} />
                </SectionCard>

                {/* ── Memory ───────────────────────────────────────── */}
                <SectionCard title="Memory">
                  <Field label="Size limit (chars)" note="Max chars in auto_short.md. 0 = unlimited.">
                    <NumberInput
                      value={memory.size_limit}
                      onChange={v => setMemory(m => m ? { ...m, size_limit: v } : m)}
                      min={0}
                    />
                  </Field>

                  <Field label="Context limit (chars)" note="Max chars injected per turn. 0 = unlimited.">
                    <NumberInput
                      value={memory.context_limit}
                      onChange={v => setMemory(m => m ? { ...m, context_limit: v } : m)}
                      min={0}
                    />
                  </Field>

                  <Field label="Flat line cap" note="Soft line cap for user.md / notes.md. 0 = no hint.">
                    <NumberInput
                      value={memory.flat_line_cap}
                      onChange={v => setMemory(m => m ? { ...m, flat_line_cap: v } : m)}
                      min={0}
                    />
                  </Field>

                  <SaveRow onSave={saveMemory} state={memoryState} />
                </SectionCard>

                {/* ── Web Search ───────────────────────────────────── */}
                <SectionCard title="Web Search">
                  <Field label="Enabled">
                    <Toggle
                      value={webSearch.enabled}
                      onChange={v => setWebSearch(ws => ws ? { ...ws, enabled: v } : ws)}
                    />
                  </Field>

                  <Field label="Max results">
                    <NumberInput
                      value={webSearch.max_results}
                      onChange={v => setWebSearch(ws => ws ? { ...ws, max_results: v } : ws)}
                      min={1}
                    />
                  </Field>

                  <SaveRow onSave={saveWebSearch} state={webSearchState} />
                </SectionCard>

                {/* ── Service ──────────────────────────────────────── */}
                <SectionCard title="Service">
                  <Field label="Host">
                    <TextInput
                      value={service.host}
                      onChange={v => setService(s => s ? { ...s, host: v } : s)}
                      placeholder="127.0.0.1"
                    />
                  </Field>

                  <Field label="Port" note="Requires restart">
                    <NumberInput
                      value={service.port}
                      onChange={v => setService(s => s ? { ...s, port: v } : s)}
                      min={1}
                    />
                  </Field>

                  {serviceState.status === 'saved' && serviceState.needsRestart && (
                    <div className="mt-3 px-4 py-2.5 bg-amber-50 border border-amber-200 rounded-lg text-[12px] text-amber-700">
                      Restart the service for this change to take effect.
                    </div>
                  )}

                  <SaveRow onSave={saveService} state={serviceState} />
                </SectionCard>

                {/* ── Paths ────────────────────────────────────────── */}
                <SectionCard title="Paths">
                  <p className="text-[12px] text-black/40 mb-4">Read-only. Edit priests.toml to change.</p>
                  {Object.entries(config.paths).map(([k, v]) => (
                    <Field key={k} label={k.replace(/_/g, ' ')}>
                      <span className="text-[12px] text-black/60 font-mono break-all">{v ?? '—'}</span>
                    </Field>
                  ))}
                </SectionCard>

              </>
            )}

          </div>
        </main>
      </div>
    </div>
  )
}
