// FILE: app/api/status/route.ts  — PUBLIC. The storefront snippet calls this.
// Returns { maintenance: true|false } by reading the flag from Upstash.
//
// Env vars:
//   UPSTASH_REDIS_REST_URL
//   UPSTASH_REDIS_REST_TOKEN

import { NextResponse } from 'next/server'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function GET() {
  const base = process.env.UPSTASH_REDIS_REST_URL
  const token = process.env.UPSTASH_REDIS_REST_TOKEN

  // Fail safe: if anything is misconfigured, keep the store OPEN (maintenance=false).
  if (!base || !token) {
    return NextResponse.json(
      { maintenance: false },
      { headers: { 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store' } },
    )
  }

  try {
    const r = await fetch(`${base}/get/maintenance`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
    })
    const data = await r.json()
    const maintenance = data.result === 'on'
    return NextResponse.json(
      { maintenance },
      { headers: { 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store' } },
    )
  } catch {
    return NextResponse.json(
      { maintenance: false },
      { headers: { 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store' } },
    )
  }
}
