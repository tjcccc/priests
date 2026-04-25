import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  fetchConfig, patchConfig, fetchModels, fetchProfiles, fetchProfileFiles,
  putProfileFiles, createProfile, putModelOptions,
  ConfigData, ProviderRegistryItem, ModelsConfig, ProfileFiles,
} from './api'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SectionStatus = 'idle' | 'saving' | 'saved' | 'error'
interface SectionState { status: SectionStatus; error?: string; needsRestart?: boolean }

interface ModelRow { provider: string; model: string }

// ---------------------------------------------------------------------------
// Primitive helpers
// ---------------------------------------------------------------------------

function SectionHeader({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between mb-4">
      <h3 className="text-[11px] font-semibold tracking-widest text-black/40 uppercase">{title}</h3>
      {action}
    </div>
  )
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-white/90 backdrop-blur-xl rounded-[16px] border border-black/[0.08] overflow-hidden">
      {children}
    </div>
  )
}

function StatusBadge({ state }: { state: SectionState }) {
  if (state.status === 'saving') return <span className="text-[12px] text-black/40">Saving…</span>
  if (state.status === 'saved') return (
    <span className="text-[12px] text-[#34C759] font-medium">
      {state.needsRestart ? 'Saved — restart required' : 'Saved'}
    </span>
  )
  if (state.status === 'error') return <span className="text-[12px] text-red-500">{state.error ?? 'Error'}</span>
  return <span />
}

function SaveRow({ onSave, state }: { onSave: () => void; state: SectionState }) {
  return (
    <div className="flex items-center justify-between pt-4 mt-2 border-t border-black/[0.06]">
      <div className="flex-1"><StatusBadge state={state} /></div>
      <button
        onClick={onSave}
        disabled={state.status === 'saving'}
        className="px-4 py-1.5 bg-[#007AFF] hover:bg-[#0051D5] disabled:bg-black/20 disabled:cursor-not-allowed cursor-pointer text-white rounded-lg text-[13px] font-medium transition-colors"
      >
        Save
      </button>
    </div>
  )
}

function Field({ label, note, children }: { label: string; note?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-4 py-2.5">
      <div className="w-[200px] shrink-0 pt-0.5">
        <span className="text-[13px] text-black/70 font-medium">{label}</span>
        {note && <p className="text-[11px] text-black/35 mt-0.5">{note}</p>}
      </div>
      <div className="flex-1">{children}</div>
    </div>
  )
}

const inputCls = "w-full px-3 py-1.5 bg-black/[0.03] rounded-lg border border-black/[0.08] text-[13px] text-black outline-none focus:ring-2 focus:ring-[#007AFF]/25 focus:border-[#007AFF]/40 transition-all"
const selectCls = "px-3 py-1.5 bg-black/[0.03] rounded-lg border border-black/[0.08] text-[13px] text-black outline-none focus:ring-2 focus:ring-[#007AFF]/25 cursor-pointer transition-all"

function TextInput({ value, onChange, placeholder, masked }: {
  value: string; onChange: (v: string) => void; placeholder?: string; masked?: boolean
}) {
  const [show, setShow] = useState(false)
  return (
    <div className="flex gap-1.5">
      <input type={masked && !show ? 'password' : 'text'} value={value}
        onChange={e => onChange(e.target.value)} placeholder={placeholder} className={inputCls} />
      {masked && (
        <button type="button" onClick={() => setShow(v => !v)}
          className="px-2.5 py-1.5 rounded-lg border border-black/[0.08] bg-black/[0.03] text-[12px] text-black/50 hover:bg-black/[0.06] cursor-pointer transition-colors shrink-0">
          {show ? 'Hide' : 'Show'}
        </button>
      )}
    </div>
  )
}

function NumberInput({ value, onChange, min }: { value: number; onChange: (v: number) => void; min?: number }) {
  return (
    <input type="number" value={value} min={min} onChange={e => onChange(Number(e.target.value))}
      className="w-[120px] px-3 py-1.5 bg-black/[0.03] rounded-lg border border-black/[0.08] text-[13px] text-black outline-none focus:ring-2 focus:ring-[#007AFF]/25 focus:border-[#007AFF]/40 transition-all" />
  )
}

function Toggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button type="button" onClick={() => onChange(!value)}
      className={`relative inline-flex h-6 w-11 items-center rounded-full cursor-pointer transition-colors ${value ? 'bg-[#007AFF]' : 'bg-black/20'}`}>
      <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${value ? 'translate-x-6' : 'translate-x-1'}`} />
    </button>
  )
}

// ---------------------------------------------------------------------------
// Provider card
// ---------------------------------------------------------------------------

interface ProviderCardProps {
  name: string; info: ProviderRegistryItem
  baseUrl: string; apiKey: string; useProxy: boolean
  onChange: (field: 'base_url' | 'api_key' | 'use_proxy', value: string | boolean) => void
}

function ProviderCard({ name, info, baseUrl, apiKey, useProxy, onChange }: ProviderCardProps) {
  const isLocal = !info.needs_api_key
  return (
    <div className="border border-black/[0.07] rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-[13px] font-semibold text-black">{info.label}</span>
        {isLocal && <span className="text-[10px] px-1.5 py-0.5 bg-[#34C759]/10 text-[#34C759] rounded font-medium">local</span>}
      </div>
      {(isLocal || name === 'custom' || name === 'openrouter') && (
        <div>
          <p className="text-[11px] text-black/40 mb-1">Base URL</p>
          <TextInput value={baseUrl} onChange={v => onChange('base_url', v)} placeholder={info.default_base_url} />
        </div>
      )}
      {info.needs_api_key && (
        <div>
          <p className="text-[11px] text-black/40 mb-1">API Key</p>
          <TextInput value={apiKey} onChange={v => onChange('api_key', v)} placeholder="Enter API key…" masked />
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
// Model Configuration section
// ---------------------------------------------------------------------------

function ModelConfigSection({ modelsConfig, profileList }: {
  modelsConfig: ModelsConfig; profileList: string[]
}) {
  const [rows, setRows] = useState<ModelRow[]>(() =>
    (modelsConfig.configured_options ?? []).map(o => ({ provider: o.provider, model: o.model }))
  )
  const [addProvider, setAddProvider] = useState(modelsConfig.providers[0]?.name ?? '')
  const [addModel, setAddModel] = useState('')
  const [state, setState] = useState<SectionState>({ status: 'idle' })

  const knownModels = modelsConfig.providers.find(p => p.name === addProvider)?.known_models ?? null

  const addRow = () => {
    const model = addModel.trim()
    if (!model) return
    setRows(r => [...r, { provider: addProvider, model }])
    setAddModel('')
    setState({ status: 'idle' })
  }

  const removeRow = (i: number) => {
    setRows(r => r.filter((_, idx) => idx !== i))
    setState({ status: 'idle' })
  }

  const save = async () => {
    setState({ status: 'saving' })
    try {
      await putModelOptions(rows.map(r => `${r.provider}/${r.model}`))
      setState({ status: 'saved' })
    } catch (e) {
      setState({ status: 'error', error: String(e) })
    }
  }

  // unused but satisfies linter for profileList prop used by Defaults section
  void profileList

  return (
    <section>
      <SectionHeader title="Model Configuration" action={
        <button onClick={addRow}
          className="flex items-center gap-2 bg-[#007AFF] hover:bg-[#0051D5] cursor-pointer text-white px-3 py-1.5 rounded-lg text-[13px] font-medium transition-colors">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
            <path d="M12 5v14M5 12h14" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Add Model
        </button>
      } />

      <Card>
        {/* Add-model row */}
        <div className="px-4 py-3 border-b border-black/[0.06] flex items-center gap-3 flex-wrap">
          <select value={addProvider} onChange={e => { setAddProvider(e.target.value); setAddModel('') }}
            className={selectCls + " w-[160px]"}>
            {modelsConfig.providers.map(p => (
              <option key={p.name} value={p.name}>{p.label}</option>
            ))}
          </select>
          {knownModels && knownModels.length > 0 ? (
            <select value={addModel} onChange={e => setAddModel(e.target.value)}
              className={selectCls + " flex-1 min-w-[180px]"}>
              <option value="">— select model —</option>
              {knownModels.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          ) : (
            <input type="text" value={addModel} onChange={e => setAddModel(e.target.value)}
              placeholder="Model name…"
              onKeyDown={e => e.key === 'Enter' && addRow()}
              className={inputCls + " flex-1 min-w-[180px]"} />
          )}
          <button onClick={addRow} disabled={!addModel.trim()}
            className="px-3 py-1.5 bg-black/[0.05] hover:bg-black/[0.09] disabled:opacity-40 cursor-pointer rounded-lg text-[13px] font-medium transition-colors shrink-0">
            Add
          </button>
        </div>

        {rows.length === 0 ? (
          <div className="px-4 py-6 text-[13px] text-black/30 text-center">
            No models configured. Add one above.
          </div>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-black/[0.06]">
                <th className="text-left px-4 py-3 text-[12px] font-semibold text-black/50">Provider</th>
                <th className="text-left px-4 py-3 text-[12px] font-semibold text-black/50">Model</th>
                <th className="text-right px-4 py-3 text-[12px] font-semibold text-black/50">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i} className={`${i < rows.length - 1 ? 'border-b border-black/[0.06]' : ''} hover:bg-black/[0.02] transition-colors`}>
                  <td className="px-4 py-3 text-[13px] text-black/70">
                    <div className="flex items-center gap-2">
                      <div className={`w-2 h-2 rounded-full shrink-0 ${
                        row.provider === 'ollama' || row.provider === 'llamacpp' || row.provider === 'lmstudio'
                          ? 'bg-[#34C759]' : 'bg-[#007AFF]'}`} />
                      {row.provider}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-[13px] text-black font-medium">{row.model}</td>
                  <td className="px-4 py-3 text-right">
                    <button onClick={() => removeRow(i)}
                      className="text-[#FF3B30] hover:text-[#FF3B30]/70 cursor-pointer text-[13px] font-medium transition-colors">
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="px-4 pb-4">
          <SaveRow onSave={save} state={state} />
        </div>
      </Card>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Profile Configuration section
// ---------------------------------------------------------------------------

function ProfileConfigSection({ profileList, onProfileCreated }: {
  profileList: string[]; onProfileCreated: (name: string) => void
}) {
  const [profiles, setProfiles] = useState<string[]>(profileList)
  const [selected, setSelected] = useState(profileList[0] ?? '')
  const [files, setFiles] = useState<ProfileFiles | null>(null)
  const [loading, setLoading] = useState(false)
  const [state, setState] = useState<SectionState>({ status: 'idle' })
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)

  const loadProfile = useCallback(async (name: string) => {
    setLoading(true)
    setState({ status: 'idle' })
    try {
      const f = await fetchProfileFiles(name)
      setFiles(f)
    } catch {
      setFiles({ profile_md: '', rules_md: '', custom_md: '', memories: true })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (selected) loadProfile(selected)
  }, [selected, loadProfile])

  const save = async () => {
    if (!files || !selected) return
    setState({ status: 'saving' })
    try {
      await putProfileFiles(selected, files)
      setState({ status: 'saved' })
    } catch (e) {
      setState({ status: 'error', error: String(e) })
    }
  }

  const doCreate = async () => {
    const name = newName.trim()
    if (!name) return
    setCreating(true)
    try {
      await createProfile(name)
      const updated = [...profiles, name].sort()
      setProfiles(updated)
      setSelected(name)
      setNewName('')
      onProfileCreated(name)
    } catch (e) {
      alert(String(e))
    } finally {
      setCreating(false)
    }
  }

  return (
    <section>
      <SectionHeader title="Profile Configuration" />
      <Card>
        <div className="flex h-[620px]">
          {/* Left: profile list */}
          <div className="w-[200px] border-r border-black/[0.06] flex flex-col shrink-0">
            <div className="flex-1 overflow-y-auto p-3 space-y-0.5">
              {profiles.map(name => (
                <button key={name} onClick={() => setSelected(name)}
                  className={`w-full text-left px-3 py-2 rounded-lg text-[13px] cursor-pointer transition-colors ${
                    selected === name
                      ? 'bg-[#007AFF]/10 text-[#007AFF] font-medium'
                      : 'text-black/70 hover:bg-black/[0.04]'
                  }`}>
                  {name}
                </button>
              ))}
            </div>
            <div className="p-3 border-t border-black/[0.06] space-y-2">
              <div className="flex gap-1.5">
                <input type="text" value={newName} onChange={e => setNewName(e.target.value)}
                  placeholder="New profile…"
                  onKeyDown={e => e.key === 'Enter' && doCreate()}
                  className="flex-1 min-w-0 px-2 py-1.5 bg-black/[0.03] rounded-lg border border-black/[0.08] text-[12px] outline-none focus:border-[#007AFF]/50 transition-all" />
                <button onClick={doCreate} disabled={!newName.trim() || creating}
                  className="px-2.5 py-1.5 bg-[#007AFF] hover:bg-[#0051D5] disabled:bg-black/20 cursor-pointer text-white rounded-lg text-[12px] font-medium transition-colors shrink-0">
                  Add
                </button>
              </div>
            </div>
          </div>

          {/* Right: file editors */}
          <div className="flex-1 flex flex-col min-w-0">
            {loading || !files ? (
              <div className="flex-1 flex items-center justify-center text-[13px] text-black/30">
                Loading…
              </div>
            ) : (
              <>
                <div className="flex-1 overflow-y-auto p-4 space-y-4">
                  {([ ['PROFILE.md', 'profile_md'], ['RULES.md', 'rules_md'], ['CUSTOM.md', 'custom_md'] ] as const).map(
                    ([label, key]) => (
                      <div key={key}>
                        <div className="flex items-center gap-2 mb-1.5">
                          <svg className="w-3.5 h-3.5 text-black/40" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                            <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" strokeLinecap="round" strokeLinejoin="round"/>
                            <path d="M13 2v7h7" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                          <span className="text-[12px] font-semibold text-black/50">{label}</span>
                        </div>
                        <textarea value={files[key]} onChange={e => setFiles(f => f ? { ...f, [key]: e.target.value } : f)}
                          className="w-full h-[120px] bg-black/[0.03] text-[12px] text-black/80 px-3 py-2 rounded-lg border border-black/[0.08] outline-none focus:border-[#007AFF]/50 transition-colors font-mono resize-none" />
                      </div>
                    )
                  )}

                  <div className="pt-2 border-t border-black/[0.06]">
                    <label className="flex items-center justify-between cursor-pointer">
                      <div>
                        <div className="text-[13px] text-black font-medium">Enable Memories</div>
                        <div className="text-[11px] text-black/40 mt-0.5">Store and recall conversation context</div>
                      </div>
                      <Toggle value={files.memories} onChange={v => setFiles(f => f ? { ...f, memories: v } : f)} />
                    </label>
                  </div>
                </div>

                <div className="px-4 pb-4 border-t border-black/[0.06] pt-2">
                  <SaveRow onSave={save} state={state} />
                </div>
              </>
            )}
          </div>
        </div>
      </Card>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Main ConfigPage
// ---------------------------------------------------------------------------

export default function ConfigPage() {
  const navigate = useNavigate()
  const [config, setConfig] = useState<ConfigData | null>(null)
  const [modelsConfig, setModelsConfig] = useState<ModelsConfig | null>(null)
  const [profileList, setProfileList] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  // Defaults draft
  const [defaults, setDefaults] = useState<ConfigData['defaults'] | null>(null)
  const [defaultsState, setDefaultsState] = useState<SectionState>({ status: 'idle' })

  // Provider drafts
  const [providerDrafts, setProviderDrafts] = useState<Record<string, { base_url: string; api_key: string; use_proxy: boolean }>>({})
  const [providersState, setProvidersState] = useState<SectionState>({ status: 'idle' })

  // Other sections
  const [memory, setMemory] = useState<ConfigData['memory'] | null>(null)
  const [memoryState, setMemoryState] = useState<SectionState>({ status: 'idle' })
  const [webSearch, setWebSearch] = useState<ConfigData['web_search'] | null>(null)
  const [webSearchState, setWebSearchState] = useState<SectionState>({ status: 'idle' })
  const [service, setService] = useState<ConfigData['service'] | null>(null)
  const [serviceState, setServiceState] = useState<SectionState>({ status: 'idle' })

  useEffect(() => {
    Promise.all([fetchConfig(), fetchModels(), fetchProfiles()])
      .then(([cfg, models, profiles]) => {
        setConfig(cfg)
        setDefaults(cfg.defaults)
        setModelsConfig(models)
        setProfileList(profiles)
        const drafts: typeof providerDrafts = {}
        for (const name of Object.keys(cfg.providers)) {
          const p = cfg.providers[name]
          drafts[name] = { base_url: p.base_url, api_key: p.api_key === '••••••' ? '' : p.api_key, use_proxy: p.use_proxy }
        }
        setProviderDrafts(drafts)
        setMemory(cfg.memory)
        setWebSearch(cfg.web_search)
        setService(cfg.service)
        setLoading(false)
      })
      .catch(e => { setLoadError(String(e)); setLoading(false) })
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
          updates[`providers.${name}.api_key`] = draft.api_key
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
      if (Object.keys(updates).length === 0) { setProvidersState({ status: 'idle' }); return }
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
      const result = await patchConfig({ 'service.host': service.host, 'service.port': String(service.port) })
      setServiceState({ status: 'saved', needsRestart: result.needs_restart })
    } catch (e) {
      setServiceState({ status: 'error', error: String(e) })
    }
  }

  const updateProvider = (name: string, field: 'base_url' | 'api_key' | 'use_proxy', value: string | boolean) =>
    setProviderDrafts(prev => ({ ...prev, [name]: { ...prev[name], [field]: value } }))

  // Collect available models for the selected default provider
  const defaultProviderModels = modelsConfig
    ? (modelsConfig.configured_options ?? [])
        .filter(o => o.provider === (defaults?.provider ?? ''))
        .map(o => o.model)
    : []

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex h-screen bg-[#f5f5f7] font-[system-ui,-apple-system,BlinkMacSystemFont,'SF_Pro','Helvetica_Neue',sans-serif]">

      {/* Sidebar */}
      <aside className="w-[280px] bg-white/80 backdrop-blur-xl border-r border-black/[0.06] flex flex-col shrink-0">
        <div className="px-5 pt-6 pb-4">
          <h1 className="text-[28px] font-semibold tracking-tight text-black">Priests</h1>
        </div>
        <div className="px-3 mt-2">
          <button onClick={() => navigate('/ui')}
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-black/50 hover:bg-black/[0.04] hover:text-black/70 cursor-pointer transition-colors text-[13px]">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
              <path d="M19 12H5M12 5l-7 7 7 7" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Back to chat
          </button>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-[72px] bg-white/60 backdrop-blur-xl border-b border-black/[0.06] flex items-center px-8 shrink-0">
          <h2 className="text-[17px] font-semibold text-black">Configuration</h2>
        </header>

        <main className="flex-1 overflow-y-auto px-8 py-8">
          <div className="max-w-[1200px] mx-auto space-y-8">

            {loading && (
              <div className="flex items-center justify-center py-20 text-black/30 text-[14px]">Loading…</div>
            )}
            {loadError && (
              <div className="bg-red-50 border border-red-200 rounded-xl px-5 py-4 text-[13px] text-red-600">
                Failed to load configuration: {loadError}
              </div>
            )}

            {!loading && !loadError && config && defaults && memory && webSearch && service && modelsConfig && (
              <>
                {/* ── Model Configuration ─────────────────────────── */}
                <ModelConfigSection modelsConfig={modelsConfig} profileList={profileList} />

                {/* ── Profile Configuration ────────────────────────── */}
                <ProfileConfigSection
                  profileList={profileList}
                  onProfileCreated={name => setProfileList(p => [...p, name].sort())}
                />

                {/* ── Defaults ─────────────────────────────────────── */}
                <section>
                  <SectionHeader title="Defaults" />
                  <Card>
                    <div className="px-6 py-5 divide-y divide-black/[0.04]">
                      <Field label="Provider">
                        <select value={defaults.provider ?? ''}
                          onChange={e => setDefaults(d => d ? { ...d, provider: e.target.value || null } : d)}
                          className={selectCls + " w-[220px]"}>
                          <option value="">— none —</option>
                          {config.registry.map(p => <option key={p.name} value={p.name}>{p.label}</option>)}
                        </select>
                      </Field>

                      <Field label="Model">
                        {defaultProviderModels.length > 0 ? (
                          <select value={defaults.model ?? ''}
                            onChange={e => setDefaults(d => d ? { ...d, model: e.target.value || null } : d)}
                            className={selectCls + " w-[280px]"}>
                            <option value="">— none —</option>
                            {defaultProviderModels.map(m => <option key={m} value={m}>{m}</option>)}
                          </select>
                        ) : (
                          <TextInput value={defaults.model ?? ''}
                            onChange={v => setDefaults(d => d ? { ...d, model: v || null } : d)}
                            placeholder="Add models above, or type a model name" />
                        )}
                      </Field>

                      <Field label="Default profile">
                        <select value={defaults.profile}
                          onChange={e => setDefaults(d => d ? { ...d, profile: e.target.value } : d)}
                          className={selectCls + " w-[220px]"}>
                          {profileList.map(p => <option key={p} value={p}>{p}</option>)}
                        </select>
                      </Field>

                      <Field label="Timeout (seconds)">
                        <NumberInput value={defaults.timeout_seconds}
                          onChange={v => setDefaults(d => d ? { ...d, timeout_seconds: v } : d)} min={1} />
                      </Field>

                      <Field label="Max output tokens" note="0 or blank = provider default">
                        <NumberInput value={defaults.max_output_tokens ?? 0}
                          onChange={v => setDefaults(d => d ? { ...d, max_output_tokens: v || null } : d)} min={0} />
                      </Field>

                      <Field label="Thinking mode">
                        <Toggle value={defaults.think}
                          onChange={v => setDefaults(d => d ? { ...d, think: v } : d)} />
                      </Field>
                    </div>
                    <div className="px-6 pb-4">
                      <SaveRow onSave={saveDefaults} state={defaultsState} />
                    </div>
                  </Card>
                </section>

                {/* ── Providers ────────────────────────────────────── */}
                <section>
                  <SectionHeader title="Providers" />
                  <Card>
                    <div className="px-6 py-5 space-y-3">
                      {config.registry.map(info => {
                        const draft = providerDrafts[info.name] ?? { base_url: '', api_key: '', use_proxy: false }
                        return (
                          <ProviderCard key={info.name} name={info.name} info={info}
                            baseUrl={draft.base_url} apiKey={draft.api_key} useProxy={draft.use_proxy}
                            onChange={(field, value) => updateProvider(info.name, field, value)} />
                        )
                      })}
                    </div>
                    <div className="px-6 pb-4">
                      <SaveRow onSave={saveProviders} state={providersState} />
                    </div>
                  </Card>
                </section>

                {/* ── Memory ───────────────────────────────────────── */}
                <section>
                  <SectionHeader title="Memory" />
                  <Card>
                    <div className="px-6 py-5 divide-y divide-black/[0.04]">
                      <Field label="Size limit (chars)" note="Max chars in auto_short.md. 0 = unlimited.">
                        <NumberInput value={memory.size_limit}
                          onChange={v => setMemory(m => m ? { ...m, size_limit: v } : m)} min={0} />
                      </Field>
                      <Field label="Context limit (chars)" note="Max chars injected per turn. 0 = unlimited.">
                        <NumberInput value={memory.context_limit}
                          onChange={v => setMemory(m => m ? { ...m, context_limit: v } : m)} min={0} />
                      </Field>
                      <Field label="Flat line cap" note="Soft line cap for user.md / notes.md. 0 = no hint.">
                        <NumberInput value={memory.flat_line_cap}
                          onChange={v => setMemory(m => m ? { ...m, flat_line_cap: v } : m)} min={0} />
                      </Field>
                    </div>
                    <div className="px-6 pb-4">
                      <SaveRow onSave={saveMemory} state={memoryState} />
                    </div>
                  </Card>
                </section>

                {/* ── Web Search ───────────────────────────────────── */}
                <section>
                  <SectionHeader title="Web Search" />
                  <Card>
                    <div className="px-6 py-5 divide-y divide-black/[0.04]">
                      <Field label="Enabled">
                        <Toggle value={webSearch.enabled}
                          onChange={v => setWebSearch(ws => ws ? { ...ws, enabled: v } : ws)} />
                      </Field>
                      <Field label="Max results">
                        <NumberInput value={webSearch.max_results}
                          onChange={v => setWebSearch(ws => ws ? { ...ws, max_results: v } : ws)} min={1} />
                      </Field>
                    </div>
                    <div className="px-6 pb-4">
                      <SaveRow onSave={saveWebSearch} state={webSearchState} />
                    </div>
                  </Card>
                </section>

                {/* ── Service ──────────────────────────────────────── */}
                <section>
                  <SectionHeader title="Service" />
                  <Card>
                    <div className="px-6 py-5 divide-y divide-black/[0.04]">
                      <Field label="Host">
                        <TextInput value={service.host}
                          onChange={v => setService(s => s ? { ...s, host: v } : s)} placeholder="127.0.0.1" />
                      </Field>
                      <Field label="Port" note="Requires restart">
                        <NumberInput value={service.port}
                          onChange={v => setService(s => s ? { ...s, port: v } : s)} min={1} />
                      </Field>
                    </div>
                    {serviceState.status === 'saved' && serviceState.needsRestart && (
                      <div className="mx-6 mb-3 px-4 py-2.5 bg-amber-50 border border-amber-200 rounded-lg text-[12px] text-amber-700">
                        Restart the service for this change to take effect.
                      </div>
                    )}
                    <div className="px-6 pb-4">
                      <SaveRow onSave={saveService} state={serviceState} />
                    </div>
                  </Card>
                </section>

                {/* ── Paths ────────────────────────────────────────── */}
                <section>
                  <SectionHeader title="Paths" />
                  <Card>
                    <div className="px-6 py-5 divide-y divide-black/[0.04]">
                      <p className="text-[12px] text-black/40 pb-4">Read-only. Edit priests.toml to change.</p>
                      {Object.entries(config.paths).map(([k, v]) => (
                        <Field key={k} label={k.replace(/_/g, ' ')}>
                          <span className="text-[12px] text-black/60 font-mono break-all">{v ?? '—'}</span>
                        </Field>
                      ))}
                    </div>
                  </Card>
                </section>

              </>
            )}
          </div>
        </main>
      </div>
    </div>
  )
}
