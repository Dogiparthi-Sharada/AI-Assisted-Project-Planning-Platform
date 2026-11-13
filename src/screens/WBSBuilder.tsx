import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router'
import { apiFetch, errorMessage } from '@/lib/api'
import { useStepNav } from '@/lib/steps'

interface WBSNode {
  id: string
  parentId: string | null
  level: 1 | 2 | 3
  sortOrder: number
  name: string
  durationDays: number | null
  effortHours: number | null
  ownerEmployeeId: string | null
}

interface WBSItem extends WBSNode {
  code: string
  duration?: string
  owner?: string
  expanded?: boolean
}

function renderTree(nodes: WBSNode[], previous: WBSItem[]): WBSItem[] {
  const result: WBSItem[] = []
  const children = (parentId: string | null) => nodes.filter(node => node.parentId === parentId)
    .sort((a, b) => a.sortOrder - b.sortOrder)
  const walk = (parentId: string | null, prefix: string) => {
    children(parentId).forEach((node, index) => {
      const code = node.level === 1 ? '1' : node.level === 2 ? `1.${index + 1}` : `${prefix}.${index + 1}`
      result.push({ ...node, code, duration: node.durationDays == null ? '—' : `${node.durationDays}d`,
        owner: node.ownerEmployeeId ? 'Assigned' : 'Unassigned',
        expanded: previous.find(item => item.id === node.id)?.expanded ?? true })
      walk(node.id, code)
    })
  }
  walk(null, '')
  return result
}

export default function WBSBuilder() {
  const nav = useStepNav()
  const { projectId } = useParams()
  const path = `/api/v1/projects/${encodeURIComponent(projectId ?? '')}/wbs/nodes`
  const query = useQuery({ queryKey: ['projects', projectId, 'wbs', 'nodes'],
    queryFn: ({ signal }) => apiFetch<WBSNode[]>(path, { signal }), enabled: !!projectId })
  const [wbs, setWbs] = useState<WBSItem[]>([])
  const [selectedCode, setSelectedCode] = useState('')
  const [draggedCode, setDraggedCode] = useState<string | null>(null)
  const [dropTarget, setDropTarget] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const [view, setView] = useState<'chart' | 'list'>('chart')

  useEffect(() => { setWbs([]); setSelectedCode(''); setActionError('') }, [projectId])
  useEffect(() => { if (query.data) setWbs(prev => renderTree(query.data, prev)) }, [query.data])

  async function change(action: () => Promise<void>) {
    if (busy || !projectId) return
    setBusy(true)
    setActionError('')
    try { await action() } catch (error) { setActionError(errorMessage(error)) }
    finally { await query.refetch(); setBusy(false) }
  }

  function toggleExpand(code: string) {
    setWbs(prev => prev.map(i => i.code === code ? { ...i, expanded: !i.expanded } : i))
  }

  function isVisible(item: WBSItem): boolean {
    if (item.level === 1) return true
    const project = wbs.find(i => i.level === 1)
    if (item.level === 2) return !!project?.expanded
    const parentCode = item.code.split('.').slice(0, 2).join('.')
    return !!project?.expanded && !!wbs.find(i => i.code === parentCode)?.expanded
  }

  function askItem(existing?: WBSItem): { name: string; durationDays: number | null; effortHours: number | null } | null {
    const input = window.prompt('Name', existing?.name ?? '')
    if (input === null) return null
    const name = input.trim()
    if (!name || name.length > 300) { setActionError('Enter a name between 1 and 300 characters.'); return null }
    const days = window.prompt('Duration in working days (optional; required to optimize)', existing?.durationDays?.toString() ?? '')
    if (days === null) return null
    const durationDays = days.trim() === '' ? null : Number(days)
    if (durationDays !== null && (!Number.isInteger(durationDays) || durationDays < 1 || durationDays > 3650)) {
      setActionError('Duration must be a whole number from 1 to 3650 days.'); return null
    }
    const hours = window.prompt('Effort in hours (optional)', existing?.effortHours?.toString() ?? '')
    if (hours === null) return null
    const effortHours = hours.trim() === '' ? null : Number(hours)
    if (effortHours !== null && (!Number.isFinite(effortHours) || effortHours <= 0 || effortHours > 9999999.9 || Math.abs(Math.round(effortHours * 10) - effortHours * 10) > 1e-7)) {
      setActionError('Effort must be a positive number with at most one decimal place.'); return null
    }
    return { name, durationDays, effortHours }
  }

  function addDeliverable() {
    const values = askItem()
    if (!values) return
    void change(async () => {
      let root = wbs.find(item => item.level === 1)
      if (!root) {
        const project = await apiFetch<{ name: string }>(`/api/v1/projects/${encodeURIComponent(projectId!)}`)
        const created = await apiFetch<WBSNode>(path, { method: 'POST', json: { name: project.name, level: 1, parentId: null, sortOrder: 0 } })
        root = { ...created, code: '1' }
      }
      const siblings = wbs.filter(item => item.parentId === root!.id)
      await apiFetch(path, { method: 'POST', json: { ...values, level: 2, parentId: root.id,
        sortOrder: siblings.length ? Math.max(...siblings.map(item => item.sortOrder)) + 1 : 0 } })
    })
  }

  function addWorkPackage(parent?: WBSItem) {
    const selected = parent ?? wbs.find(item => item.code === selectedCode)
    const deliverable = selected?.level === 2 ? selected : selected?.level === 3
      ? wbs.find(item => item.id === selected.parentId) : wbs.find(item => item.level === 2)
    if (!deliverable) { setActionError('Add a deliverable first, then select it to add a work package.'); return }
    const values = askItem()
    if (!values) return
    void change(async () => {
      const siblings = wbs.filter(item => item.parentId === deliverable.id)
      await apiFetch(path, { method: 'POST', json: { ...values, level: 3, parentId: deliverable.id,
        sortOrder: siblings.length ? Math.max(...siblings.map(item => item.sortOrder)) + 1 : 0 } })
    })
  }

  function editItem(item: WBSItem) {
    const values = askItem(item)
    if (!values) return
    void change(async () => { await apiFetch(`${path}/${encodeURIComponent(item.id)}`, { method: 'PATCH', json: values }) })
  }

  function deleteItem(item: WBSItem) {
    if (!window.confirm(`Delete "${item.name}" and any items beneath it? Dependencies and related data for deleted items will also be removed.`)) return
    void change(async () => {
      await apiFetch(`${path}/${encodeURIComponent(item.id)}`, { method: 'DELETE' })
      setSelectedCode('')
    })
  }

  function validateWbs() { setActionError('AI validation is not available yet.') }

  function moveItem(sourceCode: string, targetCode: string) {
    const source = wbs.find(item => item.code === sourceCode)
    const target = wbs.find(item => item.code === targetCode)
    if (!source || !target || source.id === target.id || source.level === 1 || source.parentId !== target.parentId) return
    const siblings = wbs.filter(item => item.parentId === source.parentId)
    const reordered = siblings.filter(item => item.id !== source.id)
    reordered.splice(reordered.findIndex(item => item.id === target.id), 0, source)
    void change(async () => {
      for (let i = 0; i < reordered.length; i++) {
        await apiFetch(`${path}/${encodeURIComponent(reordered[i].id)}`, { method: 'PATCH', json: { sortOrder: i } })
      }
      setSelectedCode('')
    })
  }

  function onRowKeyDown(event: React.KeyboardEvent, item: WBSItem) {
    if (!event.altKey || item.level === 1 || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return
    event.preventDefault()
    const siblings = wbs.filter(i => i.parentId === item.parentId)
    const index = siblings.findIndex(i => i.id === item.id)
    if (event.key === 'ArrowUp' && index > 0) moveItem(item.code, siblings[index - 1].code)
    if (event.key === 'ArrowDown' && index < siblings.length - 1) moveItem(item.code, siblings[index + 1].code)
  }

  return (
    <div style={{ padding: '32px 36px' }}>
      <Breadcrumb steps={['Dashboard', 'Create Project', 'AI Intake', 'Scope Review', 'WBS Builder']} />

      <div className="flex items-center justify-between" style={{ marginBottom: 24 }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 700 }}>WBS Builder</div>
          <div style={{ fontSize: 13, color: '#7A6063', marginTop: 2 }}>
            Review and refine the hierarchical Work Breakdown Structure.
          </div>
        </div>
        <div className="flex gap-8">
          <div className="flex" style={{ border: '1px solid #CDBFAB', borderRadius: 4, overflow: 'hidden', marginRight: 8 }}>
            {(['chart', 'list'] as const).map(v => (
              <button key={v} onClick={() => setView(v)} style={{ padding: '9px 14px', fontSize: 13, border: 'none', cursor: 'pointer', background: view === v ? '#E8DCC6' : '#fff', color: view === v ? '#722F37' : '#4A2B2F', fontWeight: view === v ? 600 : 400 }}>
                {v === 'chart' ? 'Org Chart' : 'List'}
              </button>
            ))}
          </div>
          <button onClick={addDeliverable} disabled={busy || query.isLoading || query.isError} style={secondaryBtn}>+ Add Deliverable</button>
          <button onClick={() => addWorkPackage()} disabled={busy || query.isLoading || query.isError} style={secondaryBtn}>+ Add Work Package</button>
          <button onClick={validateWbs} style={primaryBtn}>
            AI Validate
          </button>
        </div>
      </div>

      {query.isLoading && <div role="status">Loading WBS…</div>}
      {query.isError && <div role="alert">Could not load WBS: {errorMessage(query.error)} <button onClick={() => void query.refetch()} style={secondaryBtn}>Retry</button></div>}
      {actionError && <div role="alert" style={{ color: '#A83A4A', marginBottom: 12 }}>{actionError}</div>}
      {busy && <div role="status">Saving changes…</div>}
      <div className="grid" style={{ gridTemplateColumns: '1fr 280px', gap: 20 }}>
        {view === 'chart' && (
          <div style={{ ...cardStyle, overflow: 'auto', minWidth: 0 }}>
            {wbs.length > 0 ? <OrgChart wbs={wbs} selectedCode={selectedCode} onSelect={setSelectedCode} onToggle={toggleExpand} /> : <div>{query.isLoading ? 'Loading WBS…' : 'No items yet. Add a deliverable to get started.'}</div>}
          </div>
        )}
        {/* WBS tree */}
        <div style={{ ...cardStyle, display: view === 'list' ? 'block' : 'none' }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#7A6063', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 14, paddingBottom: 8, borderBottom: '1px solid #EFE5D2', display: 'grid', gridTemplateColumns: '120px 1fr 80px 100px 150px', gap: 10 }}>
            <span>WBS Code</span>
            <span>Name</span>
            <span>Duration</span>
            <span>Owner</span>
            <span>Actions</span>
          </div>
          {wbs.filter(isVisible).map(item => (
            <div
              key={item.code}
              draggable={item.level > 1 && !busy}
              onDragStart={(event) => {
                setDraggedCode(item.code)
                event.dataTransfer.effectAllowed = 'move'
              }}
              onDragEnd={() => {
                setDraggedCode(null)
                setDropTarget(null)
              }}
              onDragOver={(event) => {
                if (!draggedCode || draggedCode === item.code) return
                event.preventDefault()
                setDropTarget(item.code)
              }}
              onDrop={(event) => {
                event.preventDefault()
                if (draggedCode) moveItem(draggedCode, item.code)
                setDraggedCode(null)
                setDropTarget(null)
              }}
              onClick={() => setSelectedCode(item.code)}
              onKeyDown={(event) => onRowKeyDown(event, item)}
              tabIndex={item.level > 1 ? 0 : undefined}
              aria-keyshortcuts={item.level > 1 ? 'Alt+ArrowUp Alt+ArrowDown' : undefined}
              style={{
                display: 'grid',
                gridTemplateColumns: '120px 1fr 80px 100px 150px',
                gap: 10,
                alignItems: 'center',
                padding: '7px 0',
                paddingLeft: item.level === 2 ? 16 : item.level === 3 ? 32 : 0,
                borderBottom: '1px solid #F3EADB',
                borderLeft: item.level === 2 ? '2px solid #CDBFAB' : item.level === 3 ? '2px solid #E8DCC6' : 'none',
                marginLeft: item.level === 2 ? 16 : item.level === 3 ? 32 : 0,
                background: item.level === 1 ? '#FAF5EB' : 'transparent',
                boxShadow: dropTarget === item.code ? 'inset 0 2px #7A6063' : 'none',
                opacity: draggedCode === item.code ? 0.45 : 1,
                cursor: item.level > 1 ? 'grab' : 'default',
              }}
            >
              <span className="flex items-center gap-2" style={{ fontFamily: 'DM Mono, monospace', fontSize: 12, color: '#7A6063' }}>
                <span
                  aria-hidden="true"
                  title={item.level > 1 ? 'Drag (or Alt+↑/↓) to reorder' : undefined}
                  style={{ color: item.level > 1 ? '#999' : 'transparent', letterSpacing: -2, userSelect: 'none' }}
                >
                  ⋮⋮
                </span>
                {item.code}
              </span>
              <span
                style={{
                  fontSize: item.level === 1 ? 14 : item.level === 2 ? 13 : 13,
                  fontWeight: item.level === 1 ? 700 : item.level === 2 ? 600 : 400,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                  cursor: item.level < 3 ? 'pointer' : 'default',
                }}
                onClick={() => item.level < 3 && toggleExpand(item.code)}
              >
                {item.level < 3 && (
                  <span style={{ fontSize: 10, color: '#999', userSelect: 'none' }}>
                    {item.expanded ? '▼' : '▶'}
                  </span>
                )}
                {item.name}
              </span>
              <span style={{ fontFamily: 'DM Mono, monospace', fontSize: 12, color: '#7A6063' }}>{item.duration || '—'}</span>
              <span style={{ fontSize: 12, color: '#6B5254' }}>{item.owner || '—'}</span>
              <div className="flex gap-4">
                <button onClick={(event) => { event.stopPropagation(); editItem(item) }} disabled={busy} style={{ fontSize: 11, color: '#6B5254', border: '1px solid #e0e0e0', borderRadius: 3, padding: '2px 7px', background: '#FAF5EB', cursor: 'pointer' }}>Edit</button>
                {item.level < 3 && (
                  <button onClick={(event) => { event.stopPropagation(); item.level === 1 ? addDeliverable() : addWorkPackage(item) }} disabled={busy} style={{ fontSize: 11, color: '#6B5254', border: '1px solid #e0e0e0', borderRadius: 3, padding: '2px 7px', background: '#FAF5EB', cursor: 'pointer' }}>+ Sub</button>
                )}
                <button onClick={(event) => { event.stopPropagation(); deleteItem(item) }} disabled={busy} style={{ fontSize: 11, color: '#A83A4A', border: '1px solid #f0d4d4', borderRadius: 3, padding: '2px 7px', background: '#F3D9D5', cursor: 'pointer' }}>Delete</button>
              </div>
            </div>
          ))}
        </div>

        {/* AI Validation panel */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={cardStyle}>
            <div className="flex items-center justify-between" style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>AI Validation</div>
              <span style={{ fontSize: 10, color: '#7A6063' }}>Not available</span>
            </div>
            <div style={{ fontSize: 12, color: '#7A6063', marginBottom: 10 }}>AI validation is not available yet.</div>
            <button onClick={validateWbs} style={{ ...secondaryBtn, width: '100%', marginTop: 12, fontSize: 12 }}>
              AI Validate
            </button>
          </div>

          <div style={cardStyle}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>WBS Summary</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12, color: '#6B5254' }}>
              {[
                ['Level 1 (Project)', String(wbs.filter(i => i.level === 1).length)],
                ['Level 2 (Deliverables)', String(wbs.filter(i => i.level === 2).length)],
                ['Level 3 (Work Packages)', String(wbs.filter(i => i.level === 3).length)],
                ['Total Work Packages', String(wbs.filter(i => i.level === 3 || (i.level === 2 && !wbs.some(child => child.parentId === i.id))).length)],
                ['Unassigned Packages', String(wbs.filter(i => (i.level === 3 || (i.level === 2 && !wbs.some(child => child.parentId === i.id))) && !i.ownerEmployeeId).length)],
                ['Total package duration', `${wbs.filter(i => i.level === 3 || (i.level === 2 && !wbs.some(child => child.parentId === i.id))).reduce((sum, item) => sum + (item.durationDays ?? 0), 0)} days`],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between" style={{ paddingBottom: 5, borderBottom: '1px solid #EFE5D2' }}>
                  <span>{k}</span>
                  <span style={{ fontFamily: 'DM Mono, monospace', fontWeight: 500, color: '#2E1A1D' }}>{v}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="flex justify-between" style={{ marginTop: 24 }}>
        <button onClick={() => nav('scope')} style={secondaryBtn}>← Back to Scope Review</button>
        <button onClick={() => nav('constraints')} style={primaryBtn}>Continue to Constraints →</button>
      </div>
    </div>
  )
}

const LINE = '#B9A48C'

function OrgChart({ wbs, selectedCode, onSelect, onToggle }: {
  wbs: WBSItem[]
  selectedCode: string
  onSelect: (c: string) => void
  onToggle: (c: string) => void
}) {
  const root = wbs.find(i => i.level === 1)!
  const deliverables = wbs.filter(i => i.level === 2)
  const packages = (code: string) => wbs.filter(i => i.level === 3 && i.code.startsWith(code + '.'))
  const unassigned = (i: WBSItem) => !i.owner || i.owner === 'Unassigned'

  const selectable = (code: string) => ({
    role: 'button',
    tabIndex: 0,
    'aria-pressed': selectedCode === code,
    onClick: () => onSelect(code),
    onKeyDown: (event: React.KeyboardEvent) => {
      if (event.target !== event.currentTarget || (event.key !== 'Enter' && event.key !== ' ')) return
      event.preventDefault()
      onSelect(code)
    },
  })

  const nodeBase: React.CSSProperties = { borderRadius: 8, padding: '8px 10px', textAlign: 'center', cursor: 'pointer', position: 'relative' }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 'max-content', padding: '4px 8px 12px' }}>
      <div
        {...selectable(root.code)}
        style={{ ...nodeBase, background: '#722F37', color: '#fff', width: 220, outline: selectedCode === root.code ? '3px solid #E8C9C4' : 'none' }}
      >
        <div className="mono" style={{ fontSize: 10, opacity: 0.8 }}>{root.code}</div>
        <div style={{ fontSize: 13, fontWeight: 700 }}>{root.name}</div>
      </div>

      {root.expanded && (
        <>
          <div style={{ width: 2, height: 22, background: LINE }} />
          <div style={{ display: 'flex' }}>
            {deliverables.map((d, idx) => {
              const kids = packages(d.code)
              const first = idx === 0
              const last = idx === deliverables.length - 1
              return (
                <div key={d.code} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '0 7px', position: 'relative', width: 178 }}>
                  <div style={{ position: 'absolute', top: 0, left: first ? '50%' : 0, right: last ? '50%' : 0, height: 2, background: LINE, display: deliverables.length === 1 ? 'none' : 'block' }} />
                  <div style={{ width: 2, height: 20, background: LINE }} />
                  <div
                    {...selectable(d.code)}
                    style={{ ...nodeBase, background: '#E8DCC6', color: '#2E1A1D', width: '100%', outline: selectedCode === d.code ? '3px solid #722F3755' : 'none', border: '1px solid #D8CAB2' }}
                  >
                    <div className="mono" style={{ fontSize: 10, color: '#7A6063' }}>{d.code}</div>
                    <div style={{ fontSize: 12, fontWeight: 600, lineHeight: 1.3 }}>{d.name}</div>
                    <button
                      onClick={(e) => { e.stopPropagation(); onToggle(d.code) }}
                      style={{ marginTop: 4, fontSize: 10, color: '#722F37', background: '#fff', border: '1px solid #D8CAB2', borderRadius: 10, padding: '1px 8px', cursor: 'pointer' }}
                    >
                      {d.expanded ? '− ' : '+ '}{kids.length} packages
                    </button>
                  </div>

                  {d.expanded && kids.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', width: '100%', paddingLeft: 14, marginTop: 0, position: 'relative' }}>
                      <div style={{ position: 'absolute', left: 6, top: 0, bottom: 22, width: 2, background: LINE }} />
                      {kids.map(k => (
                        <div key={k.code} style={{ display: 'flex', alignItems: 'center', marginTop: 10, position: 'relative' }}>
                          <div style={{ position: 'absolute', left: -8, width: 10, height: 2, background: LINE }} />
                          <div
                            {...selectable(k.code)}
                            style={{ ...nodeBase, flex: 1, textAlign: 'left', background: unassigned(k) ? '#F5E3C8' : '#DDE7D0', color: '#2E1A1D', fontSize: 11, lineHeight: 1.35, outline: selectedCode === k.code ? '3px solid #722F3755' : 'none', padding: '6px 8px' }}
                          >
                            <div className="flex justify-between mono" style={{ fontSize: 10, color: '#7A6063' }}>
                              <span>{k.code}</span><span>{k.duration}</span>
                            </div>
                            <div style={{ fontWeight: 500 }}>{k.name}</div>
                            <div style={{ fontSize: 10, color: '#6B5254', marginTop: 2 }}>{k.owner}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}

      <div className="flex gap-4" style={{ marginTop: 18, fontSize: 11, color: '#7A6063' }}>
        {[['#722F37', 'Project'], ['#E8DCC6', 'Deliverable'], ['#DDE7D0', 'Assigned package'], ['#F5E3C8', 'Unassigned package']].map(([c, l]) => (
          <span key={l} className="flex items-center gap-1"><span style={{ width: 10, height: 10, borderRadius: 3, background: c, display: 'inline-block', border: '1px solid #D8CAB2' }} />{l}</span>
        ))}
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

const cardStyle: React.CSSProperties = { background: '#fff', border: '1px solid #E8DCC6', borderRadius: 6, padding: '18px 20px' }
const primaryBtn: React.CSSProperties = { padding: '9px 20px', background: '#722F37', color: '#fff', border: 'none', borderRadius: 4, fontSize: 13, fontWeight: 600, cursor: 'pointer' }
const secondaryBtn: React.CSSProperties = { padding: '9px 16px', background: '#fff', color: '#4A2B2F', border: '1px solid #CDBFAB', borderRadius: 4, fontSize: 13, cursor: 'pointer' }
