// FILE: app/api/toggle/route.ts  — PRIVATE. Flips the maintenance flag in Upstash.
// Password-protected. Called by the admin button page (app/page.tsx).
//
//   action 'off' = take the store DOWN  -> maintenance flag = "on"
//   action 'on'  = bring the store LIVE  -> maintenance flag = "off"
//
// Env vars:
//   UPSTASH_REDIS_REST_URL
//   UPSTASH_REDIS_REST_TOKEN
//   CONTROL_PASSWORD   secret required to press the button

import { NextRequest, NextResponse } from 'next/server'

export const runtime = 'nodejs'

export async function POST(req: NextRequest) {
  const { password, action } = await req.json().catch(() => ({}))

  if (!password || password !== process.env.CONTROL_PASSWORD) {
    return NextResponse.json({ error: 'wrong password' }, { status: 401 })
  }
  if (action !== 'on' && action !== 'off') {
    return NextResponse.json({ error: 'action must be on/off' }, { status: 400 })
  }

  const base = process.env.UPSTASH_REDIS_REST_URL
  const token = process.env.UPSTASH_REDIS_REST_TOKEN
  if (!base || !token) {
    return NextResponse.json({ error: 'Upstash env vars not set' }, { status: 500 })
  }

  const value = action === 'off' ? 'on' : 'off' // flag "on" == store in maintenance

  const r = await fetch(`${base}/set/maintenance/${value}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: 'no-store',
  })
  if (!r.ok) {
    return NextResponse.json({ error: 'upstash write failed', detail: await r.text() }, { status: 502 })
  }

  return NextResponse.json({ ok: true, action, maintenance: value === 'on' })
}
