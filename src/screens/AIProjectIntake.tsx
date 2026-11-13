import { useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { apiFetch, errorMessage } from '@/lib/api'
import { useStepNav } from '@/lib/steps'

const SAMPLE_TEXT = `Meeting Notes — Enterprise Data Platform Kickoff
Date: January 15, 2025 | Attendees: Robert Walsh (VP Eng), Jane Doe (PM), Sara Kim (Lead Architect), Mark Chen (Data Engineer)

OBJECTIVES
• Consolidate 7 legacy BI systems into a single unified platform
• Enable real-time dashboards for all business units by Q3 2025
• Reduce reporting cycle time from 3 days to 4 hours
• Support 500+ concurrent users at launch

REQUIREMENTS
• Ingestion layer supporting Salesforce, SAP, and 12 internal databases
• Sub-5-second query response for standard reports
• Role-based access control (RBAC) with audit logging
• GDPR and SOC 2 Type II compliance required

RISKS & CONCERNS
• Legacy SAP integration timeline is unclear — vendor dependency
• Only 2 data engineers available; team may be understaffed
• Budget approved at $375K; scope may exceed budget if infrastructure costs spike

OPEN ITEMS / UNKNOWNS
• Security review process not yet initiated
• Disaster recovery SLA not defined
• Training plan for 300 end-users not scoped`

export default function AIProjectIntake() {
  const nav = useStepNav()
  const navigate = useNavigate()
  const { projectId } = useParams()
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzed, setAnalyzed] = useState(false)
  const [notes, setNotes] = useState('')
  const [extracted, setExtracted] = useState(0)

  async function handleAnalyze() {
    if (!projectId || !notes.trim()) return
    if (!window.confirm('Your notes will be sent to the configured OpenAI service for analysis. Provider charges may apply. Each analysis adds a new set of scope items. Analyze these notes?')) return
    setAnalyzing(true)
    setAnalyzed(false)
    try {
      const result = await apiFetch<{ items: unknown[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/intake/analyze`, {
        method: 'POST', json: { text: notes.trim() },
      })
      setExtracted(result.items.length)
      setAnalyzed(true)
    } catch (error) {
      window.alert(errorMessage(error))
    } finally {
      setAnalyzing(false)
    }
  }

  return (
    <div style={{ padding: '32px 36px', maxWidth: 900 }}>
      <Breadcrumb steps={['Dashboard', 'Create Project', 'AI Project Intake']} />

      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 22, fontWeight: 700 }}>AI Project Intake</div>
        <div style={{ fontSize: 13, color: '#7A6063', marginTop: 2 }}>
          Provide raw project notes, meeting transcripts, or documents. AI will extract structured project information.
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 280px', gap: 20 }}>
        {/* Main input */}
        <div>
          <div style={cardStyle}>
            <div style={{ ...sectionLabel, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>Brainstorm / Meeting Notes</span>
              <button type="button" onClick={() => { setNotes(SAMPLE_TEXT); setAnalyzed(false) }} style={{ ...secondaryBtn, fontSize: 11, padding: '4px 8px' }}>Load Example</button>
            </div>
            <textarea
              value={notes}
              onChange={event => { setNotes(event.target.value); setAnalyzed(false) }}
              maxLength={20000}
              style={{
                width: '100%',
                minHeight: 320,
                padding: '12px 14px',
                border: '1px solid #CDBFAB',
                borderRadius: 4,
                fontSize: 13,
                lineHeight: 1.65,
                fontFamily: 'inherit',
                resize: 'vertical',
                outline: 'none',
                color: '#2E1A1D',
              }}
              placeholder="Paste meeting notes, project description, brainstorming content, or any freeform project context here…"
            />
            <div style={{ fontSize: 11, color: '#999', marginTop: 6 }}>
              AI will identify objectives, requirements, deliverables, constraints, and risks automatically.
            </div>
          </div>

          {/* Upload options */}
          <div style={{ ...cardStyle, marginTop: 16 }}>
            <div style={sectionLabel}>Upload Documents</div>
            <div className="grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
              {[
                { icon: '📄', label: 'PDF', sub: 'Reports, specs' },
                { icon: '📝', label: 'Word / DOCX', sub: 'Project docs' },
                { icon: '💬', label: 'Transcript', sub: 'Meeting text' },
                { icon: '🎙', label: 'Audio', sub: 'MP3, WAV, M4A' },
              ].map(u => (
                <button key={u.label} onClick={() => window.alert('Document upload is not available yet. Please paste the text into the notes field.')} style={{
                  padding: '14px 10px',
                  border: '1px dashed #c8c8c8',
                  borderRadius: 6,
                  background: '#FAF5EB',
                  cursor: 'pointer',
                  textAlign: 'center',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: 4,
                }}>
                  <span style={{ fontSize: 22 }}>{u.icon}</span>
                  <span style={{ fontSize: 12, fontWeight: 600 }}>{u.label}</span>
                  <span style={{ fontSize: 11, color: '#999' }}>{u.sub}</span>
                </button>
              ))}
            </div>
            <div style={{ marginTop: 10, fontSize: 11, color: '#999' }}>
              Document upload is not yet available. Paste text above to analyze it.
            </div>
          </div>

          {/* Analyze button */}
          <div className="flex items-center justify-between" style={{ marginTop: 20 }}>
            <button onClick={() => navigate('/dashboard')} style={secondaryBtn}>← Back to Dashboard</button>
            <div className="flex items-center gap-10">
              {analyzed && (
                <span style={{ fontSize: 13, color: '#4F7A4A', fontWeight: 500 }}>✓ Analysis complete — {extracted} items extracted</span>
              )}
              <button
                onClick={handleAnalyze}
                disabled={analyzing || !notes.trim()}
                style={{
                  padding: '10px 28px',
                  background: analyzing ? '#888' : '#722F37',
                  color: '#fff',
                  border: 'none',
                  borderRadius: 4,
                  fontSize: 14,
                  fontWeight: 600,
                  cursor: analyzing ? 'default' : 'pointer',
                }}
              >
                {analyzing ? 'Analyzing…' : analyzed ? 'Re-Analyze' : 'Analyze Project'}
              </button>
              <button onClick={() => nav('scope')} disabled={analyzing} style={{ ...primaryBtn, background: analyzed ? '#4F7A4A' : '#722F37' }}>
                Continue to Scope Review →
              </button>
            </div>
          </div>
        </div>

        {/* Right: tips + history */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={cardStyle}>
            <div style={sectionLabel}>What AI Extracts</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {[
                { tag: 'OBJ', label: 'Objectives', color: '#DDE7D0' },
                { tag: 'REQ', label: 'Requirements', color: '#E3DCCB' },
                { tag: 'DEL', label: 'Deliverables', color: '#F5E3C8' },
                { tag: 'CON', label: 'Constraints', color: '#F3D9D5' },
                { tag: 'RSK', label: 'Risks', color: '#F5E3C8' },
                { tag: 'UNK', label: 'Missing Info', color: '#F3EADB' },
              ].map(e => (
                <div key={e.tag} className="flex items-center gap-8" style={{ fontSize: 12 }}>
                  <span style={{ fontFamily: 'DM Mono, monospace', fontSize: 10, padding: '2px 6px', background: e.color, borderRadius: 3, color: '#6B5254', width: 36, textAlign: 'center' }}>{e.tag}</span>
                  <span>{e.label}</span>
                </div>
              ))}
            </div>
          </div>

          <div style={cardStyle}>
            <div style={sectionLabel}>Tips for Better Results</div>
            <ul style={{ fontSize: 12, color: '#6B5254', lineHeight: 1.7, paddingLeft: 16, margin: 0 }}>
              <li>Include stakeholder names and roles</li>
              <li>Mention budget figures explicitly</li>
              <li>Note hard deadlines vs. targets</li>
              <li>List known dependencies</li>
              <li>Describe success criteria</li>
            </ul>
          </div>

          <div style={cardStyle}>
            <div style={sectionLabel}>Previous Intakes</div>
            <div style={{ fontSize: 12, color: '#6B5254', padding: '6px 0', borderBottom: '1px solid #EFE5D2' }}>
              Intake history is not available yet.
            </div>
          </div>
        </div>
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
const sectionLabel: React.CSSProperties = { fontSize: 11, fontWeight: 600, color: '#7A6063', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 12, paddingBottom: 8, borderBottom: '1px solid #EFE5D2' }
const primaryBtn: React.CSSProperties = { padding: '9px 20px', background: '#722F37', color: '#fff', border: 'none', borderRadius: 4, fontSize: 13, fontWeight: 600, cursor: 'pointer' }
const secondaryBtn: React.CSSProperties = { padding: '9px 16px', background: '#fff', color: '#4A2B2F', border: '1px solid #CDBFAB', borderRadius: 4, fontSize: 13, cursor: 'pointer' }
