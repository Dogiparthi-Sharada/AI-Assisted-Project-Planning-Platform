import { useEffect, useState } from 'react'
import { useParams } from 'react-router'
import { apiFetch, errorMessage } from '@/lib/api'
import { useStepNav } from '@/lib/steps'

interface Item { id: string; text: string; accepted: boolean; removed: boolean }

type Category = 'objective' | 'requirement' | 'deliverable' | 'constraint' | 'risk' | 'missing_info'
interface SavedItem { id: string; text: string; category: Category; status: 'pending' | 'accepted' | 'removed' }
const SECTION_CATEGORIES: Record<string, Category> = {
  Objectives: 'objective', Requirements: 'requirement', Deliverables: 'deliverable',
  Constraints: 'constraint', Risks: 'risk', 'Missing Information': 'missing_info',
}
const emptySections = (): Record<string, Item[]> => Object.fromEntries(
  Object.keys(SECTION_CATEGORIES).map(section => [section, []]),
)

const SECTION_COLOR: Record<string, string> = {
  Objectives: '#DDE7D0',
  Requirements: '#E3DCCB',
  Deliverables: '#F5E3C8',
  Constraints: '#F3D9D5',
  Risks: '#F5E3C8',
  'Missing Information': '#F3EADB',
}

export default function ScopeReview() {
  const nav = useStepNav()
  const { projectId } = useParams()
  const [sections, setSections] = useState<Record<string, Item[]>>(emptySections)
  const [loadError, setLoadError] = useState('')
  const base = `/api/v1/projects/${encodeURIComponent(projectId ?? '')}/scope-items`

  useEffect(() => {
    if (!projectId) return
    const controller = new AbortController()
    setSections(emptySections())
    apiFetch<SavedItem[]>(base, { signal: controller.signal }).then(items => {
      const grouped = emptySections()
      for (const item of items) {
        const section = Object.keys(SECTION_CATEGORIES).find(key => SECTION_CATEGORIES[key] === item.category)
        if (section) grouped[section].push({ id: item.id, text: item.text, accepted: item.status === 'accepted', removed: item.status === 'removed' })
      }
      setSections(grouped); setLoadError('')
    }).catch(error => { if (!controller.signal.aborted) setLoadError(errorMessage(error)) })
    return () => controller.abort()
  }, [projectId, base])
  const [editing, setEditing] = useState<string | null>(null)
  const [editText, setEditText] = useState('')

  async function update(section: string, id: string, patch: Record<string, string>): Promise<boolean> {
    try {
      const item = await apiFetch<SavedItem>(`${base}/${encodeURIComponent(id)}`, { method: 'PATCH', json: patch })
      setSections(prev => ({ ...prev, [section]: prev[section].map(i => i.id === id ? { ...i, text: item.text, accepted: item.status === 'accepted', removed: item.status === 'removed' } : i) }))
      return true
    } catch (error) { window.alert(errorMessage(error)); return false }
  }
  function accept(section: string, id: string) { void update(section, id, { status: 'accepted' }) }
  function remove(section: string, id: string) { void update(section, id, { status: 'removed' }) }
  function startEdit(id: string, text: string) { setEditing(id); setEditText(text) }
  async function saveEdit(section: string, id: string) {
    if (!editText.trim()) return
    if (await update(section, id, { text: editText.trim() })) setEditing(null)
  }
  async function addItem(section: string) {
    const text = window.prompt(`Add ${section.toLowerCase()} item`)
    if (!text?.trim()) return
    try {
      const item = await apiFetch<SavedItem>(base, { method: 'POST', json: { category: SECTION_CATEGORIES[section], text: text.trim() } })
      setSections(prev => ({ ...prev, [section]: [...prev[section], { id: item.id, text: item.text, accepted: item.status === 'accepted', removed: item.status === 'removed' }] }))
    } catch (error) { window.alert(errorMessage(error)) }
  }
  async function acceptAll() {
    for (const [section, items] of Object.entries(sections)) {
      for (const item of items) {
        if (!item.removed && !item.accepted && !await update(section, item.id, { status: 'accepted' })) return
      }
    }
  }

  const totalAccepted = Object.values(sections).flat().filter(i => i.accepted && !i.removed).length
  const total = Object.values(sections).flat().filter(i => !i.removed).length

  return (
    <div style={{ padding: '32px 36px', maxWidth: 900 }}>
      <Breadcrumb steps={['Dashboard', 'Create Project', 'AI Intake', 'Scope Review']} />

      <div className="flex items-start justify-between" style={{ marginBottom: 24 }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 700 }}>Scope Review</div>
          <div style={{ fontSize: 13, color: '#7A6063', marginTop: 2 }}>
            Review AI-extracted project information. Accept, edit, or remove each item.
          </div>
        </div>
        <div className="flex items-center gap-10">
          <span style={{ fontSize: 13, color: '#7A6063' }}>
            <strong style={{ color: '#2E1A1D' }}>{totalAccepted}</strong> / {total} accepted
          </span>
          <button onClick={acceptAll} style={secondaryBtn}>Accept All</button>
        </div>
      </div>

      {loadError && <div role="alert" style={{ color: '#A83A4A', marginBottom: 12 }}>{loadError}</div>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {Object.entries(sections).map(([section, items]) => {
          const visible = items.filter(i => !i.removed)
          const accepted = visible.filter(i => i.accepted).length
          return (
            <div key={section} style={cardStyle}>
              <div className="flex items-center justify-between" style={{ marginBottom: 12 }}>
                <div className="flex items-center gap-8">
                  <span style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em', color: '#6B5254' }}>{section}</span>
                  <span style={{ fontSize: 10, fontFamily: 'DM Mono, monospace', padding: '2px 8px', background: SECTION_COLOR[section] || '#F3EADB', borderRadius: 10, color: '#6B5254' }}>
                    {accepted}/{visible.length} accepted
                  </span>
                </div>
                <button onClick={() => void addItem(section)} style={{ fontSize: 11, color: '#7A6063', background: 'none', border: 'none', cursor: 'pointer' }}>+ Add item</button>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {visible.map(item => (
                  <div
                    key={item.id}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 10,
                      padding: '8px 10px',
                      background: item.accepted ? '#F7F9F2' : '#FAF5EB',
                      border: `1px solid ${item.accepted ? '#BFD1AE' : '#E8DCC6'}`,
                      borderRadius: 4,
                    }}
                  >
                    <span style={{ fontSize: 14, marginTop: 1, color: item.accepted ? '#4F7A4A' : '#ccc', flexShrink: 0 }}>
                      {item.accepted ? '✓' : '○'}
                    </span>
                    <div style={{ flex: 1, fontSize: 13, lineHeight: 1.5 }}>
                      {editing === item.id ? (
                        <div className="flex gap-6">
                          <input
                            value={editText}
                            onChange={e => setEditText(e.target.value)}
                            style={{ flex: 1, padding: '4px 8px', border: '1px solid #bbb', borderRadius: 3, fontSize: 13, fontFamily: 'inherit' }}
                            autoFocus
                          />
                          <button onClick={() => saveEdit(section, item.id)} style={{ fontSize: 12, padding: '4px 10px', background: '#722F37', color: '#fff', border: 'none', borderRadius: 3, cursor: 'pointer' }}>Save</button>
                          <button onClick={() => setEditing(null)} style={{ fontSize: 12, padding: '4px 8px', border: '1px solid #CDBFAB', borderRadius: 3, cursor: 'pointer', background: '#fff' }}>Cancel</button>
                        </div>
                      ) : (
                        item.text
                      )}
                    </div>
                    {editing !== item.id && (
                      <div className="flex gap-4" style={{ flexShrink: 0 }}>
                        {!item.accepted && (
                          <button onClick={() => accept(section, item.id)} style={miniBtn('#4F7A4A', '#DDE7D0')}>Accept</button>
                        )}
                        <button onClick={() => startEdit(item.id, item.text)} style={miniBtn('#722F37', '#F3EADB')}>Edit</button>
                        <button onClick={() => remove(section, item.id)} style={miniBtn('#A83A4A', '#F3D9D5')}>Remove</button>
                      </div>
                    )}
                  </div>
                ))}
                {visible.length === 0 && (
                  <div style={{ fontSize: 12, color: '#999', padding: '8px 10px', fontStyle: 'italic' }}>No items yet</div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      <div className="flex items-center justify-between" style={{ marginTop: 24 }}>
        <button onClick={() => nav('intake')} style={secondaryBtn}>← Back to AI Intake</button>
        <button onClick={() => nav('wbs')} style={primaryBtn}>Continue to WBS Builder →</button>
      </div>
    </div>
  )
}

function Breadcrumb({ steps }: { steps: string[] }) {
  return (
    <div className="flex items-center gap-2" style={{ fontSize: 12, color: '#7A6063', marginBottom: 20 }}>
      {steps.map((s, i) => (
        <span key={s} className="flex items-center gap-2">
          {i > 0 && <span>›</span>}
          <span style={{ color: i === steps.length - 1 ? '#2E1A1D' : '#7A6063' }}>{s}</span>
        </span>
      ))}
    </div>
  )
}

function miniBtn(color: string, bg: string): React.CSSProperties {
  return { fontSize: 11, padding: '3px 8px', background: bg, color, border: `1px solid ${color}22`, borderRadius: 3, cursor: 'pointer', fontWeight: 500 }
}

const cardStyle: React.CSSProperties = { background: '#fff', border: '1px solid #E8DCC6', borderRadius: 6, padding: '16px 18px' }
const primaryBtn: React.CSSProperties = { padding: '9px 20px', background: '#722F37', color: '#fff', border: 'none', borderRadius: 4, fontSize: 13, fontWeight: 600, cursor: 'pointer' }
const secondaryBtn: React.CSSProperties = { padding: '9px 16px', background: '#fff', color: '#4A2B2F', border: '1px solid #CDBFAB', borderRadius: 4, fontSize: 13, cursor: 'pointer' }
