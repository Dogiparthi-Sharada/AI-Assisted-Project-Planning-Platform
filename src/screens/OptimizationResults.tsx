import { useState, type CSSProperties, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { isApiError, type ApiError } from '@/lib/api'
import { useLatestRun, useStartRun } from '@/lib/queries'
import { stepPath, useStepNav } from '@/lib/steps'
import type { OptimizationRun, OptimizerInput, RunStatus } from '@/lib/types'

const SOLVE_STEPS = ['Building model', 'Solving', 'Preparing results']

const ACTIVE: RunStatus[] = ['queued', 'running']
const FINISHED_OK: RunStatus[] = ['optimal', 'feasible']

export default function OptimizationResults() {
  const { projectId = '' } = useParams()
  const latest = useLatestRun(projectId)
  const startRun = useStartRun(projectId)

  const run = latest.data ?? null
  const status = run?.status ?? null
  const active = status !== null && ACTIVE.includes(status)
  const busy = startRun.isPending || active
  const startError = startRun.error && isApiError(startRun.error) ? startRun.error : null
  const notAllowed = startError?.status === 403
  const missingInput = startError?.status === 422 ? missingFrom(startError) : null
  const rerunnable = status !== null && !active && !notAllowed

  const start = () => {
    if (!busy) startRun.mutate()
  }

  let body: ReactNode
  if (latest.isPending) {
    body = <NeutralNote role="status">Loading optimization status…</NeutralNote>
  } else if (latest.isError && !run) {
    body = (
      <ErrorPanel
        title="Can’t load optimization status"
        message={isApiError(latest.error) ? latest.error.message : 'Something went wrong.'}
        action={<button type="button" onClick={() => void latest.refetch()} style={secondaryBtn}>Retry</button>}
      />
    )
  } else if (missingInput) {
    body = <NeedsInputView missing={missingInput} message={startError?.message ?? ''} projectId={projectId} />
  } else if (status === 'queued' || status === 'running') {
    body = <RunningView status={status} />
  } else if (!run) {
    body = (
      <div className="opt-panel opt-center">
        <div style={{ fontSize: 15, fontWeight: 600 }}>No optimization has been run for this project yet</div>
        <div style={{ fontSize: 12, color: '#7A6063', maxWidth: 440 }}>
          The solver uses your work packages, team, dependencies and constraints to find the best schedule and assignments.
        </div>
        <button type="button" onClick={start} disabled={busy} style={{ ...primaryBtn, marginTop: 4, ...(busy ? disabledBtn : null) }}>
          {startRun.isPending ? 'Starting…' : 'Run Optimization'}
        </button>
      </div>
    )
  } else if (status === 'optimal' || status === 'feasible') {
    body = <ResultsView run={run} />
  } else if (status === 'infeasible') {
    body = <InfeasibleView run={run} projectId={projectId} />
  } else {
    body = (
      <ErrorPanel
        title={status === 'timeout' ? 'The solver timed out' : 'The optimization failed'}
        message={run.error || (status === 'timeout' ? 'No solution was found within the time limit.' : 'Something went wrong while solving.')}
        action={
          <button type="button" onClick={start} disabled={busy} style={primaryBtn}>
            Try again
          </button>
        }
      />
    )
  }

  return (
    <div className="opt-page">
      <Breadcrumb steps={['Dashboard', 'Create Project', 'AI Intake', 'Scope Review', 'WBS', 'Constraints', 'Optimization Results']} />

      <div className="opt-head">
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Optimization Results</h1>
          <div style={{ fontSize: 13, color: '#7A6063', marginTop: 2 }}>
            {status !== null && FINISHED_OK.includes(status)
              ? 'Solver completed. Review recommended plan adjustments before finalizing.'
              : 'Run the optimizer against your WBS, team and constraints.'}
          </div>
        </div>
        <div className="opt-head-actions">
          {run && status !== null && FINISHED_OK.includes(status) && <SolverBadge run={run} />}
          {(rerunnable || busy) && (
            <button type="button" onClick={start} disabled={busy} style={{ ...secondaryBtn, ...(busy ? disabledBtn : null) }}>
              {busy ? 'Running…' : 'Run again'}
            </button>
          )}
        </div>
      </div>

      {notAllowed && (
        <div style={{ marginBottom: 16 }}>
          <NeutralNote>Only project editors can run the optimizer. Ask a project owner or manager for access.</NeutralNote>
        </div>
      )}

      {startError && startError.status !== 403 && startError.status !== 422 && (
        <div role="alert" style={{ ...noteBox('#F3D9D5', '#E6BDB8'), marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <span>{startError.message}</span>
          <button type="button" onClick={() => startRun.reset()} style={secondaryBtn}>
            Dismiss
          </button>
        </div>
      )}

      {body}
    </div>
  )
}

function missingFrom(error: ApiError): OptimizerInput[] {
  const raw = error.extensions.missing
  if (!Array.isArray(raw)) return []
  return raw.filter((m): m is OptimizerInput => m === 'workPackages' || m === 'team')
}

function SolverBadge({ run }: { run: OptimizationRun }) {
  const optimal = run.status === 'optimal'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', padding: '8px 14px', background: optimal ? '#DDE7D0' : '#F5E3C8', border: `1px solid ${optimal ? '#BFD1AE' : '#E6CFA6'}`, borderRadius: 6 }}>
      <span style={{ fontSize: 13, color: optimal ? '#4F7A4A' : '#a8691f', fontWeight: 600 }}>{run.status.toUpperCase()}</span>
      <span style={{ fontSize: 12, color: '#6B5254', fontFamily: 'DM Mono, monospace' }}>
        {run.solver || 'Optimizer'}
        {run.solveSeconds != null ? ` · ${run.solveSeconds.toFixed(1)}s` : ''}
      </span>
    </div>
  )
}

function RunningView({ status }: { status: 'queued' | 'running' }) {
  const current = status === 'queued' ? 0 : 1
  return (
    <div className="opt-panel opt-center" role="status" aria-live="polite">
      <div className="opt-ring" aria-hidden="true" />
      <div style={{ fontSize: 16, fontWeight: 600 }}>The optimizer is solving your plan…</div>
      <div style={{ fontSize: 12, color: '#7A6063' }}>This usually takes under a minute. You can leave this page open.</div>
      <ol className="opt-steps">
        {SOLVE_STEPS.map((label, i) => (
          <li key={label} style={{ display: 'contents' }}>
            {i > 0 && (
              <span className="opt-step-sep" aria-hidden="true">
                →
              </span>
            )}
            <span className="opt-step" aria-current={i === current ? 'step' : undefined} data-done={i < current}>
              {i < current ? '✓' : i + 1} {label}
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}

const INPUT_HINTS: Record<OptimizerInput, { label: string; step: 'wbs' | 'constraints'; cta: string }> = {
  workPackages: { label: 'Work packages with durations', step: 'wbs', cta: 'Open WBS Builder' },
  team: { label: 'Team members', step: 'constraints', cta: 'Open Constraints' },
}

function NeedsInputView({ missing, message, projectId }: { missing: OptimizerInput[]; message: string; projectId: string }) {
  return (
    <div className="opt-panel" role="alert" style={{ borderLeft: '4px solid #a8691f' }}>
      <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>The optimizer needs a bit more to work with</div>
      <div style={{ fontSize: 13, color: '#6B5254', marginBottom: 16 }}>{message}</div>
      <div className="opt-iis" style={{ marginBottom: 0 }}>
        {missing.map((m) => (
          <div key={m} className="flex items-center justify-between gap-3" style={{ padding: '10px 12px', background: '#FAF5EB', border: '1px solid #E8DCC6', borderRadius: 6, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>{INPUT_HINTS[m].label}</span>
            <Link to={stepPath(INPUT_HINTS[m].step, projectId)} style={{ ...secondaryBtn, textDecoration: 'none' }}>
              {INPUT_HINTS[m].cta} →
            </Link>
          </div>
        ))}
      </div>
    </div>
  )
}

function InfeasibleView({ run, projectId }: { run: OptimizationRun; projectId: string }) {
  const iis = run.iis ?? []
  return (
    <>
      <div role="alert" style={{ ...noteBox('#F3D9D5', '#E6BDB8'), marginBottom: 16 }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: '#A83A4A', marginBottom: 2 }}>This plan is infeasible as specified.</div>
        <div style={{ fontSize: 12, color: '#6B5254' }}>No assignment satisfies every hard constraint at once. The constraints below cannot all hold together.</div>
      </div>

      <div className="opt-iis">
        {iis.length === 0 && (
          <div className="opt-panel" style={{ fontSize: 13, color: '#6B5254' }}>
            The solver did not report which constraints conflict.
          </div>
        )}
        {iis.map((c) => (
          <div key={c.name} className="opt-panel" style={{ borderLeft: '4px solid #A83A4A' }}>
            <div style={{ fontSize: 10, color: '#A83A4A', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Conflicting constraint</div>
            <div style={{ fontSize: 14, fontWeight: 600, fontFamily: 'DM Mono, monospace', wordBreak: 'break-word', marginBottom: 4 }}>{c.name}</div>
            <div style={{ fontSize: 13, color: '#4A2B2F', lineHeight: 1.5 }}>{c.explanation}</div>
          </div>
        ))}
      </div>

      <Link to={stepPath('constraints', projectId)} style={{ ...primaryBtn, display: 'inline-block', textDecoration: 'none' }}>
        Edit constraints
      </Link>
    </>
  )
}

function ErrorPanel({ title, message, action }: { title?: string; message: string; action: ReactNode }) {
  return (
    <div role="alert" style={{ ...noteBox('#F3D9D5', '#E6BDB8'), display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'flex-start' }}>
      <div>
        {title && <div style={{ fontSize: 15, fontWeight: 700, color: '#A83A4A', marginBottom: 2 }}>{title}</div>}
        <div style={{ fontSize: 13 }}>{message}</div>
      </div>
      {action}
    </div>
  )
}

function NeutralNote({ children, role }: { children: ReactNode; role?: string }) {
  return (
    <div role={role} style={{ ...noteBox('#E8DCC6', '#CDBFAB'), fontSize: 14 }}>
      {children}
    </div>
  )
}

type Tab = 'reassignments' | 'schedule' | 'resources'

const TAB_LABELS: Record<Tab, string> = {
  reassignments: 'Task Reassignments',
  schedule: 'Schedule Changes',
  resources: 'Resource Utilization',
}

function ResultsView({ run }: { run: OptimizationRun }) {
  const nav = useStepNav()
  const [activeTab, setActiveTab] = useState<Tab>('reassignments')
  const result = run.result
  const optimal = run.status === 'optimal'

  if (!result) {
    return <NeutralNote>The solver finished but returned no plan details. Try running it again.</NeutralNote>
  }

  return (
    <>
      {!optimal && (
        <div role="note" style={{ ...noteBox('#F5E3C8', '#E6CFA6'), padding: '8px 14px', fontSize: 12, color: '#a8691f', marginBottom: 16 }}>
          A feasible plan was found, but optimality has not been proven.
        </div>
      )}

      <div className="opt-kpis">
        <div style={cardStyle}>
          <div style={kpiLabel}>Solver Status</div>
          <div style={{ fontSize: 20, fontWeight: 700, color: optimal ? '#4F7A4A' : '#a8691f', marginBottom: 2, textTransform: 'capitalize' }}>{run.status}</div>
          <div style={{ fontSize: 11, color: '#7A6063' }}>All modeled hard constraints satisfied</div>
        </div>
        {result.kpis.map((k) => (
          <div key={k.label} style={cardStyle}>
            <div style={kpiLabel}>{k.label}</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: '#722F37', marginBottom: 2 }}>{k.value}</div>
            {k.sub && <div style={{ fontSize: 11, color: '#7A6063' }}>{k.sub}</div>}
          </div>
        ))}
      </div>

      <div style={{ ...cardStyle, marginBottom: 20 }}>
        <div className="opt-tabs" role="tablist" aria-label="Plan details">
          {(Object.keys(TAB_LABELS) as Tab[]).map((tab) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={activeTab === tab}
              onClick={() => setActiveTab(tab)}
              style={{
                padding: '10px 20px',
                border: 'none',
                borderBottom: activeTab === tab ? '2px solid #722F37' : '2px solid transparent',
                background: 'transparent',
                fontSize: 13,
                fontWeight: activeTab === tab ? 600 : 400,
                color: activeTab === tab ? '#2E1A1D' : '#7A6063',
                cursor: 'pointer',
                marginBottom: -1,
                whiteSpace: 'nowrap',
              }}
            >
              {TAB_LABELS[tab]}
            </button>
          ))}
        </div>

        {activeTab === 'reassignments' &&
          (result.reassignments.length === 0 ? (
            <EmptyTab>No reassignments needed — the current owners already fit the plan.</EmptyTab>
          ) : (
            <div className="opt-scroll">
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #E8DCC6' }}>
                    {['Task', 'Current Assignment', 'Recommended', 'Reason'].map((h) => (
                      <th key={h} style={th}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.reassignments.map((x) => (
                    <tr key={x.task} style={{ borderBottom: '1px solid #F3EADB' }}>
                      <td style={{ padding: '10px 12px', fontFamily: 'DM Mono, monospace', fontSize: 12 }}>{x.task}</td>
                      <td style={{ padding: '10px 12px', fontSize: 12, color: '#7A6063' }}>{x.current}</td>
                      <td style={{ padding: '10px 12px', fontSize: 12, color: '#722F37', fontWeight: 500 }}>{x.recommended}</td>
                      <td style={{ padding: '10px 12px', fontSize: 12, color: '#6B5254' }}>{x.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}

        {activeTab === 'schedule' &&
          (result.scheduleChanges.length === 0 ? (
            <EmptyTab>The schedule is unchanged from the baseline.</EmptyTab>
          ) : (
            <div className="opt-scroll">
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #E8DCC6' }}>
                    {['WBS', 'Task', 'Original', 'Optimized', 'Delta', 'Reason'].map((h) => (
                      <th key={h} style={th}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.scheduleChanges.map((x) => (
                    <tr key={x.wbs} style={{ borderBottom: '1px solid #F3EADB' }}>
                      <td style={{ padding: '10px 12px', fontFamily: 'DM Mono, monospace', fontSize: 12, color: '#7A6063' }}>{x.wbs}</td>
                      <td style={{ padding: '10px 12px', fontWeight: 500 }}>{x.name}</td>
                      <td style={{ padding: '10px 12px', fontFamily: 'DM Mono, monospace', fontSize: 12, color: '#7A6063' }}>{x.original}</td>
                      <td style={{ padding: '10px 12px', fontFamily: 'DM Mono, monospace', fontSize: 12 }}>{x.optimized}</td>
                      <td style={{ padding: '10px 12px' }}>
                        <span
                          style={{
                            fontSize: 11,
                            padding: '2px 8px',
                            borderRadius: 10,
                            background: x.delta === '—' ? '#EFE5D2' : '#F5E3C8',
                            color: x.delta === '—' ? '#7A6063' : '#a8691f',
                            fontFamily: 'DM Mono, monospace',
                            fontWeight: 600,
                          }}
                        >
                          {x.delta}
                        </span>
                      </td>
                      <td style={{ padding: '10px 12px', fontSize: 12, color: '#6B5254' }}>{x.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}

        {activeTab === 'resources' &&
          (result.utilization.length === 0 ? (
            <EmptyTab>No resource utilization to show.</EmptyTab>
          ) : (
            <div>
              <div className="opt-util-row opt-util-head" style={{ marginBottom: 10, fontSize: 10, color: '#7A6063', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                <span>Resource</span>
                <span>Before Optimization</span>
                <span>After Optimization</span>
                <span>Change</span>
              </div>
              {result.utilization.map((x) => {
                const delta = x.after - x.before
                return (
                  <div key={x.name} className="opt-util-row" style={{ padding: '10px 0', borderBottom: '1px solid #F3EADB' }}>
                    <span style={{ fontSize: 13, fontWeight: 500 }}>{x.name}</span>
                    <div>
                      <span className="opt-sm-label">Before</span>
                      <UtilBar value={x.before} base="#B7A89A" />
                    </div>
                    <div>
                      <span className="opt-sm-label">After</span>
                      <UtilBar value={x.after} base="#722F37" />
                    </div>
                    <span style={{ fontFamily: 'DM Mono, monospace', fontSize: 12, color: delta < 0 ? '#4F7A4A' : delta > 0 ? '#a8691f' : '#7A6063', fontWeight: 600 }}>
                      {delta > 0 ? `+${delta}%` : delta < 0 ? `${delta}%` : '—'}
                    </span>
                  </div>
                )
              })}
            </div>
          ))}
      </div>

      <div className="opt-actions">
        <button type="button" onClick={() => nav('constraints')} style={secondaryBtn}>
          ← Adjust Constraints
        </button>
        <div className="opt-actions-group">
          {/* Applying a plan and exports need backend endpoints that don't exist yet. */}
          <button type="button" disabled title="Coming soon" style={{ ...secondaryBtn, ...disabledBtn }}>
            Export PDF Report
          </button>
          <button type="button" disabled title="Coming soon" style={{ ...secondaryBtn, ...disabledBtn }}>
            Export to MS Project
          </button>
          <button type="button" disabled title="Coming soon" style={{ ...primaryBtn, ...disabledBtn }}>
            Apply Optimized Plan
          </button>
        </div>
      </div>
    </>
  )
}

function EmptyTab({ children }: { children: ReactNode }) {
  return <div style={{ padding: '20px 4px', fontSize: 13, color: '#7A6063' }}>{children}</div>
}

function UtilBar({ value, base }: { value: number; base: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div style={{ flex: 1, height: 8, background: '#E8DCC6', borderRadius: 4 }}>
        <div style={{ width: `${Math.min(100, Math.max(0, value))}%`, height: '100%', background: value >= 100 ? '#A83A4A' : value > 80 ? '#a8691f' : base, borderRadius: 4 }} />
      </div>
      <span style={{ fontFamily: 'DM Mono, monospace', fontSize: 12, width: 36, textAlign: 'right' }}>{value}%</span>
    </div>
  )
}

function Breadcrumb({ steps }: { steps: string[] }) {
  return (
    <div className="flex items-center gap-2" style={{ fontSize: 12, color: '#7A6063', marginBottom: 20, flexWrap: 'wrap' }}>
      {steps.map((s, i) => (
        <span key={s} className="flex items-center gap-2">
          {i > 0 && <span>›</span>}
          <span style={{ color: i === steps.length - 1 ? '#2E1A1D' : '#7A6063' }}>{s}</span>
        </span>
      ))}
    </div>
  )
}

const noteBox = (bg: string, border: string): CSSProperties => ({
  background: bg,
  border: `1px solid ${border}`,
  borderRadius: 6,
  padding: '16px 20px',
  color: '#2E1A1D',
})
const kpiLabel: CSSProperties = { fontSize: 10, color: '#7A6063', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }
const th: CSSProperties = { textAlign: 'left', padding: '6px 12px', fontSize: 10, color: '#7A6063', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }
const cardStyle: CSSProperties = { background: '#fff', border: '1px solid #E8DCC6', borderRadius: 6, padding: '18px 20px' }
const primaryBtn: CSSProperties = { padding: '9px 20px', background: '#722F37', color: '#fff', border: 'none', borderRadius: 4, fontSize: 13, fontWeight: 600, cursor: 'pointer' }
const secondaryBtn: CSSProperties = { padding: '9px 16px', background: '#fff', color: '#4A2B2F', border: '1px solid #CDBFAB', borderRadius: 4, fontSize: 13, cursor: 'pointer' }
const disabledBtn: CSSProperties = { opacity: 0.55, cursor: 'not-allowed' }
