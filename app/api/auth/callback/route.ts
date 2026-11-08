// FILE: app/api/auth/callback/route.ts  — Shopify redirects here after you approve.
// Verifies the request, exchanges the code for a permanent token, stores it in KV.
//
// Env vars:
//   SHOPIFY_API_KEY, SHOPIFY_API_SECRET, APP_URL
//   (Vercel KV vars are added automatically when you create a KV store)

import { NextRequest, NextResponse } from 'next/server'
import crypto from 'crypto'
import { kv } from '@vercel/kv'

export const runtime = 'nodejs'
const SHOP_RE = /^[a-z0-9][a-z0-9-]*\.myshopify\.com$/i

export async function GET(req: NextRequest) {
  const url = req.nextUrl
  const shop = url.searchParams.get('shop') || ''
  const hmac = url.searchParams.get('hmac') || ''
  const code = url.searchParams.get('code') || ''
  const state = url.searchParams.get('state') || ''

  // 1) state must match the cookie we set in /api/auth
  if (!state || state !== req.cookies.get('oauth_state')?.value) {
    return NextResponse.json({ error: 'state mismatch' }, { status: 403 })
  }
  if (!SHOP_RE.test(shop)) {
    return NextResponse.json({ error: 'bad shop' }, { status: 400 })
  }

  // 2) verify Shopify's HMAC over the remaining query params
  const params = new URLSearchParams(url.search)
  params.delete('hmac')
  params.delete('signature')
  const message = [...params.entries()].sort().map(([k, v]) => `${k}=${v}`).join('&')
  const digest = crypto
    .createHmac('sha256', process.env.SHOPIFY_API_SECRET as string)
    .update(message)
    .digest('hex')
  const ok =
    hmac.length === digest.length &&
    crypto.timingSafeEqual(Buffer.from(hmac), Buffer.from(digest))
  if (!ok) {
    return NextResponse.json({ error: 'hmac invalid' }, { status: 403 })
  }

  // 3) exchange the code for a permanent offline access token
  const tokenRes = await fetch(`https://${shop}/admin/oauth/access_token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      client_id: process.env.SHOPIFY_API_KEY,
      client_secret: process.env.SHOPIFY_API_SECRET,
      code,
    }),
  })
  const data = await tokenRes.json()
  if (!data.access_token) {
    return NextResponse.json({ error: 'token exchange failed', detail: data }, { status: 502 })
  }

  // 4) store it — this is the token the switch uses from now on
  await kv.set(`token:${shop}`, data.access_token)

  return NextResponse.redirect(`${process.env.APP_URL}/?installed=${encodeURIComponent(shop)}`)
}
