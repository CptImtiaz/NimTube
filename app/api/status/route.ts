// FILE: app/api/status/route.ts  — PUBLIC. The storefront snippet calls this.
// Returns the current flags:
//   { maintenance: bool, slowmode: bool, slowms: number }
//
// Env vars:
//   UPSTASH_REDIS_REST_URL
//   UPSTASH_REDIS_REST_TOKEN

import { NextResponse } from 'next/server'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

const CORS = { 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store' }
const OFF = { maintenance: false, slowmode: false, slowms: 0 }

async function getKey(base: string, token: string, key: string) {
  const r = await fetch(`${base}/get/${key}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: 'no-store',
  })
  const data = await r.json()
  return data.result as string | null
}

export async function GET() {
  const base = process.env.UPSTASH_REDIS_REST_URL
  const token = process.env.UPSTASH_REDIS_REST_TOKEN

  // Fail safe: if misconfigured, everything is "off" (store normal).
  if (!base || !token) {
    return NextResponse.json(OFF, { headers: CORS })
  }

  try {
    const [m, s, ms] = await Promise.all([
      getKey(base, token, 'maintenance'),
      getKey(base, token, 'slowmode'),
      getKey(base, token, 'slowms'),
    ])
    return NextResponse.json(
      {
        maintenance: m === 'on',
        slowmode: s === 'on',
        slowms: parseInt(ms || '', 10) || 0,
      },
      { headers: CORS },
    )
  } catch {
    return NextResponse.json(OFF, { headers: CORS })
  }
}
