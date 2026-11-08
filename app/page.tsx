'use client'
// FILE: app/page.tsx — the control panel (maintenance + slow mode)
import { useEffect, useState } from 'react'

type Status = { maintenance: boolean; slowmode: boolean; slowms: number }

export default function Panel() {
  const [password, setPassword] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [seconds, setSeconds] = useState('5')
  const [status, setStatus] = useState<Status | null>(null)

  async function refresh() {
    try {
      const r = await fetch('/api/status', { cache: 'no-store' })
      const d = (await r.json()) as Status
      setStatus(d)
      if (d.slowms) setSeconds(String(Math.round(d.slowms / 1000)))
    } catch {}
  }
  useEffect(() => {
    refresh()
  }, [])

  async function set(key: string, value: string) {
    setBusy(true)
    setMsg('Working…')
    try {
      const r = await fetch('/api/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password, key, value }),
      })
      const d = await r.json()
      if (!r.ok) {
        setMsg('⚠️ ' + (d.error ?? 'Failed'))
      } else {
        setMsg('✅ Saved')
        refresh()
      }
    } catch {
      setMsg('⚠️ Network error')
    } finally {
      setBusy(false)
    }
  }

  const pill = (on: boolean) => ({
    ...s.pillBase,
    background: on ? '#16a34a22' : '#64748b22',
    color: on ? '#4ade80' : '#94a3b8',
  })

  return (
    <main style={s.main}>
      <div style={s.card}>
        <h1 style={s.h1}>Store Control</h1>

        <input
          type="password"
          placeholder="Control password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={s.input}
        />

        <section style={s.section}>
          <div style={s.rowHead}>
            <span style={s.label}>Maintenance</span>
            <span style={pill(!!status?.maintenance)}>
              {status ? (status.maintenance ? 'DOWN' : 'LIVE') : '—'}
            </span>
          </div>
          <div style={s.row}>
            <button disabled={busy} onClick={() => set('maintenance', 'on')} style={{ ...s.btn, ...s.off }}>
              Take DOWN
            </button>
            <button disabled={busy} onClick={() => set('maintenance', 'off')} style={{ ...s.btn, ...s.on }}>
              Go LIVE
            </button>
          </div>
        </section>

        <section style={s.section}>
          <div style={s.rowHead}>
            <span style={s.label}>Slow mode</span>
            <span style={pill(!!status?.slowmode)}>
              {status ? (status.slowmode ? 'ON' : 'OFF') : '—'}
            </span>
          </div>
          <div style={s.row}>
            <button disabled={busy} onClick={() => set('slowmode', 'on')} style={{ ...s.btn, ...s.warn }}>
              Slow ON
            </button>
            <button disabled={busy} onClick={() => set('slowmode', 'off')} style={{ ...s.btn, ...s.on }}>
              Slow OFF
            </button>
          </div>
          <div style={{ ...s.row, marginTop: 10, alignItems: 'center' }}>
            <input
              type="number"
              min={0}
              value={seconds}
              onChange={(e) => setSeconds(e.target.value)}
              style={{ ...s.input, marginBottom: 0, width: 90 }}
            />
            <span style={{ color: '#aab6cf', fontSize: '.85rem' }}>sec delay</span>
            <button
              disabled={busy}
              onClick={() => set('slowms', String((parseInt(seconds, 10) || 0) * 1000))}
              style={{ ...s.btn, flex: 'unset', padding: '10px 16px' }}
            >
              Save
            </button>
          </div>
        </section>

        <p style={s.status}>{msg}</p>
      </div>
    </main>
  )
}

const s: Record<string, React.CSSProperties> = {
  main: { minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#0b1020', fontFamily: 'system-ui, sans-serif', padding: 16 },
  card: { background: '#121a30', padding: 28, borderRadius: 16, width: 340, color: '#e7ecf5', boxShadow: '0 10px 40px rgba(0,0,0,.4)' },
  h1: { margin: '0 0 18px', fontSize: '1.4rem', textAlign: 'center' },
  input: { width: '100%', padding: '12px 14px', borderRadius: 10, border: '1px solid #2a365c', background: '#0b1020', color: '#e7ecf5', marginBottom: 18, boxSizing: 'border-box' },
  section: { borderTop: '1px solid #223', paddingTop: 14, marginBottom: 6 },
  rowHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 },
  label: { fontWeight: 600, fontSize: '.95rem' },
  pillBase: { fontSize: '.72rem', fontWeight: 700, padding: '3px 10px', borderRadius: 999, letterSpacing: '.03em' },
  row: { display: 'flex', gap: 10 },
  btn: { flex: 1, padding: '12px 0', borderRadius: 10, border: 'none', fontWeight: 700, cursor: 'pointer', color: '#fff', fontSize: '.9rem' },
  off: { background: '#dc2626' },
  on: { background: '#16a34a' },
  warn: { background: '#d97706' },
  status: { marginTop: 16, fontSize: '.9rem', color: '#aab6cf', minHeight: 20, textAlign: 'center', wordBreak: 'break-word' },
}
