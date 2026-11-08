// FILE: app/api/toggle/route.ts  — PRIVATE. Sets a flag in Upstash.
// Password-protected. Called by the control panel (app/page.tsx).
//
// Body: { password, key, value }
//   key   one of: "maintenance" | "slowmode" | "slowms"
//   value "on" / "off" for the switches, or a number of ms for "slowms"
//
// Env vars:
//   UPSTASH_REDIS_REST_URL
//   UPSTASH_REDIS_REST_TOKEN
//   CONTROL_PASSWORD

import { NextRequest, NextResponse } from 'next/server'

export const runtime = 'nodejs'

const ALLOWED = ['maintenance', 'slowmode', 'slowms']

export async function POST(req: NextRequest) {
  const { password, key, value } = await req.json().catch(() => ({}))

  if (!password || password !== process.env.CONTROL_PASSWORD) {
    return NextResponse.json({ error: 'wrong password' }, { status: 401 })
  }
  if (!ALLOWED.includes(key)) {
    return NextResponse.json({ error: 'bad key' }, { status: 400 })
  }

  const base = process.env.UPSTASH_REDIS_REST_URL
  const token = process.env.UPSTASH_REDIS_REST_TOKEN
  if (!base || !token) {
    return NextResponse.json({ error: 'Upstash env vars not set' }, { status: 500 })
  }

  const v = encodeURIComponent(String(value ?? ''))
  const r = await fetch(`${base}/set/${key}/${v}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: 'no-store',
  })
  if (!r.ok) {
    return NextResponse.json({ error: 'upstash write failed', detail: await r.text() }, { status: 502 })
  }

  return NextResponse.json({ ok: true, key, value: String(value ?? '') })
}
