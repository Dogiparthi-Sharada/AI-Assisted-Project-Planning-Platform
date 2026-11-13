import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router'
import { apiFetch, errorMessage } from '@/lib/api'
import { useProject, useStartRun } from '@/lib/queries'
import { useStepNav } from '@/lib/steps'

type Mode = 'hard' | 'soft'
type DependencyType = 'FS' | 'SS' | 'FF' | 'SF'
interface Employee { id: string; fullName: string; roleTitle: string | null; weeklyCapacityHours: number; hourlyRateCents: number | null; skills?: EmployeeSkill[]; allocations?: Allocation[] }
interface EmployeeSkill { id: string; name: string; proficiency: number }
interface Allocation { weekStart: string; hours: number }
interface Skill { id: string; name: string }
interface Node { id: string; parentId: string | null; name: string; level: number }
interface Dependency { id: string; predecessorId: string; successorId: string; type: DependencyType; lagDays: number }
interface Milestone { id: string; name: string; dueDate: string; wbsNodeId: string | null; mode: Mode }
interface Settings {
  projectId: string; budgetTotalCents: number | null; contingencyPct: number; infraCapCents: number | null
  laborCapCents: number | null; budgetMode: Mode; budgetPenalty: number; startDate: string | null
  mustFinishBy: string | null; deadlineMode: Mode; deadlinePenaltyPerDay: number; workingDaysPerWeek: number
  capacityMode: Mode; capacityPenalty: number; skillMode: Mode; allowContractors: boolean; contractorRateCents: number | null
}
type MoneyField = 'budgetTotalCents' | 'laborCapCents' | 'infraCapCents' | 'contractorRateCents'
type MoneyForm = Record<MoneyField, string>
const moneyFields: MoneyField[] = ['budgetTotalCents', 'laborCapCents', 'infraCapCents', 'contractorRateCents']
const labels: Record<DependencyType, string> = { FS: 'Finish-to-Start', SS: 'Start-to-Start', FF: 'Finish-to-Finish', SF: 'Start-to-Finish' }

function dollars(cents: number | null): string { return cents == null ? '' : (cents / 100).toFixed(2) }
function cents(value: string): number | null {
  const input = value.trim().replace(/,/g, '')
  if (!input) return null
  if (!/^\d+(?:\.\d{1,2})?$/.test(input)) throw new Error('Enter valid amounts with no more than two decimal places.')
  const [whole, fraction = ''] = input.split('.')
  const amount = Number(whole) * 100 + Number(fraction.padEnd(2, '0'))
  if (!Number.isSafeInteger(amount)) throw new Error('Amount is too large.')
  return amount
}

export default function Constraints() {
  const nav = useStepNav()
  const { projectId = '' } = useParams()
  const project = useProject(projectId)
  const orgId = project.data?.orgId ?? ''
  const base = `/api/v1/projects/${encodeURIComponent(projectId)}`
  const org = `/api/v1/orgs/${encodeURIComponent(orgId)}`
  const config = useQuery({ queryKey: ['projects', projectId, 'constraints'], queryFn: ({ signal }) => apiFetch<Settings>(`${base}/constraints`, { signal }), enabled: !!projectId })
  const team = useQuery({ queryKey: ['orgs', orgId, 'employees'], queryFn: async ({ signal }) => {
    const employees = await apiFetch<Employee[]>(`${org}/employees`, { signal })
    return Promise.all(employees.map(async employee => {
      const path = `${org}/employees/${encodeURIComponent(employee.id)}`
      const [skills, allocations] = await Promise.all([
        apiFetch<EmployeeSkill[]>(`${path}/skills`, { signal }),
        apiFetch<Allocation[]>(`${path}/allocations`, { signal }),
      ])
      return { ...employee, skills, allocations }
    }))
  }, enabled: !!orgId })
  const skills = useQuery({ queryKey: ['orgs', orgId, 'skills'], queryFn: ({ signal }) => apiFetch<Skill[]>(`${org}/skills`, { signal }), enabled: !!orgId })
  const nodes = useQuery({ queryKey: ['projects', projectId, 'wbs', 'nodes'], queryFn: ({ signal }) => apiFetch<Node[]>(`${base}/wbs/nodes`, { signal }), enabled: !!projectId })
  const dependencies = useQuery({ queryKey: ['projects', projectId, 'dependencies'], queryFn: ({ signal }) => apiFetch<Dependency[]>(`${base}/dependencies`, { signal }), enabled: !!projectId })
  const milestones = useQuery({ queryKey: ['projects', projectId, 'milestones'], queryFn: ({ signal }) => apiFetch<Milestone[]>(`${base}/milestones`, { signal }), enabled: !!projectId })
  const startRun = useStartRun(projectId)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [amounts, setAmounts] = useState<MoneyForm>({ budgetTotalCents: '', laborCapCents: '', infraCapCents: '', contractorRateCents: '' })
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => { setSettings(null); setMessage(''); setNotice('') }, [projectId])
  useEffect(() => {
    const data = config.data
    if (!data) return
    setSettings(data)
    setAmounts(Object.fromEntries(moneyFields.map(key => [key, dollars(data[key])])) as MoneyForm)
  }, [config.data])

  function edit<K extends keyof Settings>(key: K, value: Settings[K]) {
    setSettings(current => current ? { ...current, [key]: value } : current)
    setNotice('')
  }
  function toggle(key: 'budgetMode' | 'deadlineMode' | 'capacityMode' | 'skillMode') {
    if (settings) edit(key, settings[key] === 'hard' ? 'soft' : 'hard')
  }
  async function save(): Promise<void> {
    if (!settings) throw new Error('Constraints are still loading.')
    const { projectId: _projectId, ...body } = settings
    const parsed = Object.fromEntries(moneyFields.map(key => [key, cents(amounts[key])]))
    await apiFetch<Settings>(`${base}/constraints`, { method: 'PUT', json: { ...body, ...parsed } })
  }
  async function perform(work: () => Promise<void>, success?: string) {
    if (busy) return
    setBusy(true); setMessage(''); setNotice('')
    try { await work(); if (success) setNotice(success) } catch (error) { setMessage(errorMessage(error)) }
    finally { setBusy(false) }
  }
  function choose<T>(items: T[], label: string, name: (item: T) => string): T | null {
    if (!items.length) { setMessage(`Add ${label.toLowerCase()} first.`); return null }
    const value = window.prompt(`Choose ${label} by number:\n${items.map((item, index) => `${index + 1}. ${name(item)}`).join('\n')}`)
    if (value === null) return null
    const index = Number(value)
    if (!/^\d+$/.test(value.trim()) || index < 1 || index > items.length) { setMessage('Choose a valid number.'); return null }
    return items[index - 1]
  }
  function addEmployee() {
    const name = window.prompt('Team member name')
    if (name === null) return
    const fullName = name.trim()
    if (!fullName) { setMessage('Enter a team member name.'); return }
    const roleTitle = window.prompt('Role or title (optional)', '')
    if (roleTitle === null) return
    const hours = window.prompt('Available hours per week', '40')
    if (hours === null) return
    const rate = window.prompt('Hourly cost in dollars (optional)', '')
    if (rate === null) return
    void perform(async () => {
      const weeklyCapacityHours = Number(hours)
      if (!hours.trim() || !Number.isFinite(weeklyCapacityHours) || weeklyCapacityHours < 0 || weeklyCapacityHours > 80) throw new Error('Enter valid weekly hours between 0 and 80.')
      await apiFetch(`${org}/employees`, { method: 'POST', json: { fullName, roleTitle: roleTitle.trim() || null, weeklyCapacityHours, hourlyRateCents: cents(rate), isContractor: false } })
      await team.refetch()
    }, 'Team member added.')
  }
  function addSkill() {
    const value = window.prompt('Skill name')
    if (value === null) return
    const name = value.trim()
    if (!name) { setMessage('Enter a skill name.'); return }
    void perform(async () => { await apiFetch(`${org}/skills`, { method: 'POST', json: { name } }); await skills.refetch() }, 'Skill added.')
  }
  function editEmployeeRate(employee: Employee) {
    const value = window.prompt(
      `Hourly cost in dollars for ${employee.fullName} (leave blank to clear)`,
      employee.hourlyRateCents == null ? '' : dollars(employee.hourlyRateCents),
    )
    if (value === null) return
    void perform(async () => {
      await apiFetch(`${org}/employees/${encodeURIComponent(employee.id)}`, {
        method: 'PATCH',
        json: { hourlyRateCents: cents(value) },
      })
      await team.refetch()
    }, 'Hourly cost saved.')
  }
  function assignOwner() {
    const work = (nodes.data ?? []).filter(n => n.level === 3 || (n.level === 2 && !(nodes.data ?? []).some(child => child.parentId === n.id)))
    const task = choose(work, 'a work package', item => item.name)
    if (!task) return
    const employee = choose(team.data ?? [], 'a team member', item => item.fullName)
    if (!employee) return
    void perform(async () => {
      await apiFetch(`${base}/wbs/nodes/${encodeURIComponent(task.id)}`, { method: 'PATCH', json: { ownerEmployeeId: employee.id } })
      await nodes.refetch()
    }, 'Task owner saved.')
  }
  function assignSkill(kind: 'employee' | 'task') {
    const skill = choose(skills.data ?? [], 'a skill', item => item.name)
    if (!skill) return
    const list: (Employee | Node)[] = kind === 'employee'
      ? (team.data ?? [])
      : (nodes.data ?? []).filter(n => n.level === 3 || (n.level === 2 && !(nodes.data ?? []).some(child => child.parentId === n.id)))
    const item = choose(list, kind === 'employee' ? 'a team member' : 'a WBS item', item => 'fullName' in item ? item.fullName : item.name)
    if (!item) return
    const proficiency = window.prompt('Proficiency level from 1 to 5', '3')
    if (proficiency === null) return
    if (!/^[1-5]$/.test(proficiency.trim())) { setMessage('Enter a proficiency level from 1 to 5.'); return }
    const path = kind === 'employee'
      ? `${org}/employees/${encodeURIComponent(item.id)}/skills/${encodeURIComponent(skill.id)}`
      : `${base}/wbs/nodes/${encodeURIComponent(item.id)}/skills/${encodeURIComponent(skill.id)}`
    void perform(async () => {
      await apiFetch(path, { method: 'PUT', json: { proficiency: Number(proficiency) } })
      if (kind === 'employee') await team.refetch()
    }, kind === 'employee' ? 'Team skill saved.' : 'Task skill requirement saved.')
  }
  function addMilestone() {
    const value = window.prompt('Milestone name')
    if (value === null) return
    const name = value.trim()
    if (!name) { setMessage('Enter a milestone name.'); return }
    const dueDate = window.prompt('Due date (YYYY-MM-DD)')
    if (dueDate === null) return
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dueDate)) { setMessage('Enter the date as YYYY-MM-DD.'); return }
    const item = choose((nodes.data ?? []).filter(n => n.level > 1), 'a WBS item', item => item.name)
    if (!item) return
    const mode = window.prompt('Is this a hard or soft milestone?', 'hard')
    if (mode === null) return
    const normalized = mode.trim().toLowerCase()
    if (normalized !== 'hard' && normalized !== 'soft') { setMessage('Enter hard or soft.'); return }
    void perform(async () => {
      await apiFetch(`${base}/milestones`, { method: 'POST', json: { name, dueDate, wbsNodeId: item.id, mode: normalized } })
      await milestones.refetch()
    }, 'Milestone added.')
  }
  function addDependency() {
    const candidates = (nodes.data ?? []).filter(n => n.level > 1)
    const predecessor = choose(candidates, 'a predecessor', item => item.name)
    if (!predecessor) return
    const successor = choose(candidates, 'a successor', item => item.name)
    if (!successor) return
    if (predecessor.id === successor.id) { setMessage('Choose two different tasks.'); return }
    const type = window.prompt('Dependency type: FS, SS, FF or SF', 'FS')
    if (type === null) return
    const normalized = type.trim().toUpperCase()
    if (!['FS', 'SS', 'FF', 'SF'].includes(normalized)) { setMessage('Choose FS, SS, FF or SF.'); return }
    const lag = window.prompt('Lag in working days (negative for lead)', '0')
    if (lag === null) return
    const lagDays = Number(lag)
    if (!lag.trim() || !Number.isInteger(lagDays) || lagDays < -365 || lagDays > 365) { setMessage('Lag must be a whole number from -365 to 365.'); return }
    void perform(async () => { await apiFetch(`${base}/dependencies`, { method: 'POST', json: { predecessorId: predecessor.id, successorId: successor.id, type: normalized, lagDays } }); await dependencies.refetch() }, 'Dependency added.')
  }
  const dataError = project.error || config.error || team.error || skills.error || nodes.error || dependencies.error || milestones.error
  if (dataError) return <div style={{ padding: 32 }} role="alert">Could not load planning data: {errorMessage(dataError)} <button onClick={() => { void project.refetch(); void config.refetch(); void team.refetch(); void skills.refetch(); void nodes.refetch(); void dependencies.refetch(); void milestones.refetch() }} style={secondaryBtn}>Retry</button></div>
  if (!settings || !orgId || team.isPending || skills.isPending || nodes.isPending || dependencies.isPending || milestones.isPending) return <div style={{ padding: 32 }} role="status">Loading constraints…</div>
  const nodeName = (id: string) => nodes.data?.find(node => node.id === id)?.name ?? 'Unavailable task'
  return (
    <div style={{ padding: '32px 36px', maxWidth: 1100 }}>
      <Breadcrumb steps={['Dashboard', 'Create Project', 'AI Intake', 'Scope Review', 'WBS Builder', 'Constraints']} />
      <div style={{ marginBottom: 24 }}><div style={{ fontSize: 22, fontWeight: 700 }}>Constraints</div><div style={{ fontSize: 13, color: '#7A6063', marginTop: 2 }}>Define hard and soft constraints for your project plan.</div></div>
      {message && <div role="alert" style={errorBox}>{message}</div>}{notice && <div role="status" style={noticeBox}>{notice}</div>}
      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 430px), 1fr))', gap: 20 }}>
        <div style={cardStyle}>
          <SectionHeader label="Budget" badge={settings.budgetMode} onToggle={() => toggle('budgetMode')} />
          <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <Field label="Total Budget ($)"><input aria-label="Total Budget" inputMode="decimal" value={amounts.budgetTotalCents} onChange={e => setAmounts({ ...amounts, budgetTotalCents: e.target.value })} style={inputStyle} /></Field>
            <Field label="Contingency Reserve (%)"><input aria-label="Contingency Reserve" type="number" min="0" max="100" value={settings.contingencyPct} onChange={e => edit('contingencyPct', Number(e.target.value))} style={inputStyle} /></Field>
            <Field label="Labor Cost Cap ($)"><input aria-label="Labor Cost Cap" inputMode="decimal" value={amounts.laborCapCents} onChange={e => setAmounts({ ...amounts, laborCapCents: e.target.value })} style={inputStyle} /></Field>
            <Field label="Infrastructure Cap ($)"><input aria-label="Infrastructure Cap" inputMode="decimal" value={amounts.infraCapCents} onChange={e => setAmounts({ ...amounts, infraCapCents: e.target.value })} style={inputStyle} /></Field>
          </div>
          <p style={hintStyle}>Infrastructure cap and contingency are saved, but are not included in the current optimizer.</p>
        </div>
        <div style={cardStyle}>
          <SectionHeader label="Deadline" badge={settings.deadlineMode} onToggle={() => toggle('deadlineMode')} />
          <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <Field label="Project Start Date"><input aria-label="Project Start Date" type="date" value={settings.startDate ?? ''} onChange={e => edit('startDate', e.target.value || null)} style={inputStyle} /></Field>
            <Field label="Must-Finish-By Date"><input aria-label="Must-Finish-By Date" type="date" value={settings.mustFinishBy ?? ''} onChange={e => edit('mustFinishBy', e.target.value || null)} style={inputStyle} /></Field>
          </div>
          <div style={{ marginTop: 12 }}><Field label="Working Days per Week"><select aria-label="Working Days per Week" value={settings.workingDaysPerWeek} onChange={e => edit('workingDaysPerWeek', Number(e.target.value))} style={inputStyle}>{[1, 2, 3, 4, 5, 6, 7].map(n => <option key={n} value={n}>{n} days</option>)}</select></Field></div>
          <div style={{ marginTop: 12 }}><strong style={{ fontSize: 12 }}>Milestones</strong>{milestones.data?.length ? milestones.data.map(m => <div key={m.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, fontSize: 12, marginTop: 6 }}><span>{m.name} · {m.dueDate} · {m.mode}{m.wbsNodeId ? ` · ${nodeName(m.wbsNodeId)}` : ' · informational'}</span><button disabled={busy} onClick={() => void perform(async () => { await apiFetch(`${base}/milestones/${encodeURIComponent(m.id)}`, { method: 'DELETE' }); await milestones.refetch() }, 'Milestone removed.')} style={secondaryBtn}>Remove</button></div>) : <p style={hintStyle}>No milestones yet.</p>}<button disabled={busy} onClick={addMilestone} style={secondaryBtn}>+ Add Milestone</button></div>
        </div>
        <div style={cardStyle}>
          <SectionHeader label="Employee Capacity" badge={settings.capacityMode} onToggle={() => toggle('capacityMode')} />
          {team.data?.length ? <table style={tableStyle}><thead><tr style={tableBorder}>{['Name', 'Role', 'Avail. (hrs/wk)', 'Hourly cost', 'Skills', 'Existing allocations'].map(h => <th key={h} style={thStyle}>{h}</th>)}</tr></thead><tbody>{team.data.map(e => <tr key={e.id} style={rowBorder}><td style={tdStyle}>{e.fullName}</td><td style={tdStyle}>{e.roleTitle || '—'}</td><td style={tdStyle}>{e.weeklyCapacityHours}h</td><td style={tdStyle}>{e.hourlyRateCents == null ? 'Not set' : `$${dollars(e.hourlyRateCents)}`} <button type="button" disabled={busy} aria-label={`Edit hourly cost for ${e.fullName}`} onClick={() => editEmployeeRate(e)} style={{ ...secondaryBtn, fontSize: 11, padding: '2px 6px' }}>Edit</button></td><td style={tdStyle}>{e.skills?.map(s => `${s.name} (${s.proficiency})`).join(', ') || '—'}</td><td style={tdStyle}>{e.allocations?.length ? e.allocations.map(a => `${a.weekStart}: ${a.hours}h`).join(', ') : 'None recorded'}</td></tr>)}</tbody></table> : <p style={hintStyle}>No team members yet. Add at least one to run optimization.</p>}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}><button disabled={busy} onClick={addEmployee} style={secondaryBtn}>+ Add Team Member</button><button disabled={busy} onClick={assignOwner} style={secondaryBtn}>Assign Task Owner</button></div>
          <p style={hintStyle}>Allocations show total hours across projects for each listed week.</p>
        </div>
        <div style={cardStyle}>
          <SectionHeader label="Required Skills" badge={settings.skillMode} onToggle={() => toggle('skillMode')} />
          {skills.data?.length ? <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>{skills.data.map(s => <span key={s.id} style={{ fontSize: 12, padding: '4px 8px', background: '#F3EADB', borderRadius: 10 }}>{s.name}</span>)}</div> : <p style={hintStyle}>No skills added yet.</p>}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}><button disabled={busy} onClick={addSkill} style={secondaryBtn}>+ Add Skill</button><button disabled={busy} onClick={() => assignSkill('employee')} style={secondaryBtn}>Assign to Team</button><button disabled={busy} onClick={() => assignSkill('task')} style={secondaryBtn}>Require on Task</button></div>
          <p style={hintStyle}>Skill coverage is not displayed here. Set a requirement on a task and the matching skills for team members.</p>
          <div style={{ marginTop: 12 }}><label><input type="checkbox" checked={settings.allowContractors} onChange={e => edit('allowContractors', e.target.checked)} /> Allow contractors for skill gaps</label></div>
          {settings.allowContractors && <div style={{ marginTop: 8 }}><Field label="Contractor Rate ($/hour)"><input aria-label="Contractor Rate" inputMode="decimal" value={amounts.contractorRateCents} onChange={e => setAmounts({ ...amounts, contractorRateCents: e.target.value })} style={inputStyle} /></Field></div>}
        </div>
        <div style={{ ...cardStyle, gridColumn: '1 / -1' }}>
          <SectionHeader label="Task Dependencies" badge="hard" />
          {dependencies.data?.length ? <table style={tableStyle}><thead><tr style={tableBorder}>{['Predecessor', 'Successor', 'Dependency Type', 'Lag (days)', 'Actions'].map(h => <th key={h} style={thStyle}>{h}</th>)}</tr></thead><tbody>{dependencies.data.map(d => <tr key={d.id} style={rowBorder}><td style={tdStyle}>{nodeName(d.predecessorId)}</td><td style={tdStyle}>{nodeName(d.successorId)}</td><td style={tdStyle}>{labels[d.type]}</td><td style={tdStyle}>{d.lagDays}</td><td style={tdStyle}><button disabled={busy} onClick={() => void perform(async () => { await apiFetch(`${base}/dependencies/${encodeURIComponent(d.id)}`, { method: 'DELETE' }); await dependencies.refetch() }, 'Dependency removed.')} style={secondaryBtn}>Remove</button></td></tr>)}</tbody></table> : <p style={hintStyle}>No dependencies yet.</p>}
          <button disabled={busy} onClick={addDependency} style={{ ...secondaryBtn, marginTop: 12 }}>+ Add Dependency</button>
        </div>
      </div>
      <div className="flex justify-between" style={{ marginTop: 24, gap: 12, flexWrap: 'wrap' }}><button disabled={busy} onClick={() => nav('wbs')} style={secondaryBtn}>← Back to WBS Builder</button><div style={{ display: 'flex', gap: 8 }}><button disabled={busy} onClick={() => void perform(save, 'Constraints saved.')} style={secondaryBtn}>Save Constraints</button><button disabled={busy} onClick={() => void perform(async () => { await save(); await startRun.mutateAsync(); nav('optimization') })} style={primaryBtn}>{busy ? 'Working…' : 'Run Optimization →'}</button></div></div>
    </div>
  )
}
function SectionHeader({ label, badge, onToggle }: { label: string; badge: Mode; onToggle?: () => void }) { return <div className="flex items-center justify-between" style={{ marginBottom: 14, paddingBottom: 10, borderBottom: '1px solid #EFE5D2' }}><span style={{ fontSize: 13, fontWeight: 600 }}>{label}</span><button type="button" disabled={!onToggle} onClick={onToggle} style={{ fontSize: 11, padding: '3px 10px', background: badge === 'hard' ? '#F3D9D5' : '#E3DCCB', color: badge === 'hard' ? '#A83A4A' : '#4455aa', border: 'none', borderRadius: 10, cursor: onToggle ? 'pointer' : 'default', fontWeight: 600 }}>{badge === 'hard' ? 'Hard Constraint' : 'Soft Constraint'}</button></div> }
function Field({ label, children }: { label: string; children: ReactNode }) { return <div><label style={{ display: 'block', fontSize: 11, fontWeight: 600, marginBottom: 5, color: '#6B5254', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</label>{children}</div> }
function Breadcrumb({ steps }: { steps: string[] }) { return <div className="flex items-center gap-2" style={{ fontSize: 12, color: '#7A6063', marginBottom: 20 }}>{steps.map((s, i) => <span key={s} className="flex items-center gap-2">{i > 0 && <span>›</span>}<span style={{ color: i === steps.length - 1 ? '#2E1A1D' : '#7A6063' }}>{s}</span></span>)}</div> }
const cardStyle: CSSProperties = { background: '#fff', border: '1px solid #E8DCC6', borderRadius: 6, padding: '18px 20px' }
const inputStyle: CSSProperties = { width: '100%', padding: '8px 10px', border: '1px solid #CDBFAB', borderRadius: 4, fontSize: 13, background: '#fff', color: '#2E1A1D', fontFamily: 'inherit' }
const primaryBtn: CSSProperties = { padding: '9px 20px', background: '#722F37', color: '#fff', border: 'none', borderRadius: 4, fontSize: 13, fontWeight: 600, cursor: 'pointer' }
const secondaryBtn: CSSProperties = { padding: '9px 16px', background: '#fff', color: '#4A2B2F', border: '1px solid #CDBFAB', borderRadius: 4, fontSize: 13, cursor: 'pointer' }
const hintStyle: CSSProperties = { fontSize: 12, color: '#7A6063', marginTop: 10 }
const errorBox: CSSProperties = { padding: 12, background: '#F3D9D5', color: '#A83A4A', borderRadius: 4, marginBottom: 16 }
const noticeBox: CSSProperties = { padding: 12, background: '#DDE7D0', color: '#4F7A4A', borderRadius: 4, marginBottom: 16 }
const tableStyle: CSSProperties = { width: '100%', borderCollapse: 'collapse', fontSize: 12 }
const tableBorder: CSSProperties = { borderBottom: '1px solid #E8DCC6' }
const rowBorder: CSSProperties = { borderBottom: '1px solid #F3EADB' }
const thStyle: CSSProperties = { textAlign: 'left', padding: '5px 8px', fontSize: 10, color: '#7A6063', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }
const tdStyle: CSSProperties = { padding: '8px 8px' }
