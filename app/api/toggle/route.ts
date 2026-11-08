// FILE: app/api/toggle/route.ts  — the ON/OFF action (theme swap).
// Uses the token captured during OAuth install (stored in KV), so you never
// paste a token anywhere.
//
// Env vars:
//   SHOPIFY_STORE_DOMAIN   wellessia.myshopify.com
//   LIVE_THEME_ID          numeric id of your live theme
//   MAINTENANCE_THEME_ID   numeric id of your "Closed" theme
//   CONTROL_PASSWORD       secret required to press the button

import { NextRequest, NextResponse } from 'next/server'
import { kv } from '@vercel/kv'

export const runtime = 'nodejs'
const API_VERSION = '2025-01'

const PUBLISH = `mutation themePublish($id: ID!) {
  themePublish(id: $id) { theme { id name } userErrors { field message } }
}`

export async function POST(req: NextRequest) {
  const { password, action } = await req.json().catch(() => ({}))

  if (!password || password !== process.env.CONTROL_PASSWORD) {
    return NextResponse.json({ error: 'wrong password' }, { status: 401 })
  }
  if (action !== 'on' && action !== 'off') {
    return NextResponse.json({ error: 'action must be on/off' }, { status: 400 })
  }

  const shop = process.env.SHOPIFY_STORE_DOMAIN as string
  const token = await kv.get<string>(`token:${shop}`)
  if (!token) {
    return NextResponse.json(
      { error: `app not installed on ${shop} yet — open /api/auth?shop=${shop} first` },
      { status: 409 },
    )
  }

  const themeId =
    action === 'off' ? process.env.MAINTENANCE_THEME_ID : process.env.LIVE_THEME_ID

  const res = await fetch(`https://${shop}/admin/api/${API_VERSION}/graphql.json`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Shopify-Access-Token': token },
    body: JSON.stringify({
      query: PUBLISH,
      variables: { id: `gid://shopify/OnlineStoreTheme/${themeId}` },
    }),
  })
  const data = await res.json()

  const errs = data?.data?.themePublish?.userErrors
  if (data.errors || (errs && errs.length)) {
    return NextResponse.json({ ok: false, action, detail: data.errors ?? errs }, { status: 422 })
  }

  return NextResponse.json({ ok: true, action, published: data.data.themePublish.theme })
}
