// FILE: app/api/auth/route.ts  — starts the Shopify OAuth install
// Visit:  https://nimtube.vercel.app/api/auth?shop=wellessia.myshopify.com
//
// Env vars:
//   SHOPIFY_API_KEY     client ID of your Shopify app
//   APP_URL             https://nimtube.vercel.app  (no trailing slash)

import { NextRequest, NextResponse } from 'next/server'
import crypto from 'crypto'

export const runtime = 'nodejs'

const SHOP_RE = /^[a-z0-9][a-z0-9-]*\.myshopify\.com$/i
const SCOPES = 'read_themes,write_themes' // only what the switch needs

export async function GET(req: NextRequest) {
  const shop = req.nextUrl.searchParams.get('shop') || ''
  if (!SHOP_RE.test(shop)) {
    return NextResponse.json({ error: 'pass ?shop=your-store.myshopify.com' }, { status: 400 })
  }

  const state = crypto.randomBytes(16).toString('hex')
  const redirectUri = `${process.env.APP_URL}/api/auth/callback`
  const authUrl =
    `https://${shop}/admin/oauth/authorize` +
    `?client_id=${process.env.SHOPIFY_API_KEY}` +
    `&scope=${encodeURIComponent(SCOPES)}` +
    `&redirect_uri=${encodeURIComponent(redirectUri)}` +
    `&state=${state}`

  const res = NextResponse.redirect(authUrl)
  res.cookies.set('oauth_state', state, {
    httpOnly: true, secure: true, sameSite: 'lax', path: '/', maxAge: 600,
  })
  return res
}
