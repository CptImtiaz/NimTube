'use client'
// FILE: app/page.tsx  — the ON/OFF button page
import { useState } from 'react'

export default function Switch() {
  const [password, setPassword] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  async function flip(action: 'on' | 'off') {
    setBusy(true)
    setStatus('Working…')
    try {
      const res = await fetch('/api/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password, action }),
      })
      const data = await res.json()
      setStatus(
        res.ok
          ? `✅ Store is now ${action === 'off' ? 'OFF (maintenance)' : 'ON (live)'}`
          : `⚠️ ${data.error ?? 'Failed'} — ${JSON.stringify(data.detail ?? '')}`,
      )
    } catch (e) {
      setStatus('⚠️ Network error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main style={styles.main}>
      <div style={styles.card}>
        <h1 style={styles.h1}>Store Switch</h1>
        <input
          type="password"
          placeholder="Control password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={styles.input}
        />
        <div style={styles.row}>
          <button disabled={busy} onClick={() => flip('off')} style={{ ...styles.btn, ...styles.off }}>
            Turn OFF
          </button>
          <button disabled={busy} onClick={() => flip('on')} style={{ ...styles.btn, ...styles.on }}>
            Turn ON
          </button>
        </div>
        <p style={styles.status}>{status}</p>
      </div>
    </main>
  )
}

const styles: Record<string, React.CSSProperties> = {
  main: { minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#0b1020', fontFamily: 'system-ui, sans-serif' },
  card: { background: '#121a30', padding: 32, borderRadius: 16, width: 320, textAlign: 'center', color: '#e7ecf5', boxShadow: '0 10px 40px rgba(0,0,0,.4)' },
  h1: { margin: '0 0 20px', fontSize: '1.4rem' },
  input: { width: '100%', padding: '12px 14px', borderRadius: 10, border: '1px solid #2a365c', background: '#0b1020', color: '#e7ecf5', marginBottom: 16, boxSizing: 'border-box' },
  row: { display: 'flex', gap: 12 },
  btn: { flex: 1, padding: '14px 0', borderRadius: 10, border: 'none', fontWeight: 700, cursor: 'pointer', color: '#fff' },
  off: { background: '#dc2626' },
  on: { background: '#16a34a' },
  status: { marginTop: 18, fontSize: '.9rem', color: '#aab6cf', minHeight: 20, wordBreak: 'break-word' },
}
