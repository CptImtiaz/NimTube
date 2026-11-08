// FILE: app/api/toggle/route.ts   — deploy on Vercel (NOT Shopify)
// ---------------------------------------------------------------------------
// Turns your Shopify storefront OFF/ON by swapping which theme is published.
//   OFF  -> publishes your "Maintenance/Closed" theme
//   ON   -> republishes your live store theme
//
// Env vars (set in Vercel → Settings → Environment Variables; never in code):
//   SHOPIFY_STORE_DOMAIN     your-store.myshopify.com
//   SHOPIFY_ADMIN_TOKEN      shpat_... (custom app token, scopes: read_themes, write_themes ONLY)
//   LIVE_THEME_ID            numeric id of your real theme
//   MAINTENANCE_THEME_ID     numeric id of your "Closed" theme
//   CONTROL_PASSWORD         a secret you choose, required to use the button
// ---------------------------------------------------------------------------

import { NextRequest, NextResponse } from 'next/server'

const API_VERSION = '2025-01'

async function shopify(query: string, variables: Record<string, unknown>) {
  const res = await fetch(
    `https://${process.env.SHOPIFY_STORE_DOMAIN}/admin/api/${API_VERSION}/graphql.json`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Shopify-Access-Token': process.env.SHOPIFY_ADMIN_TOKEN as string,
      },
      body: JSON.stringify({ query, variables }),
    },
  )
  return res.json()
}

const PUBLISH = `mutation themePublish($id: ID!) {
  themePublish(id: $id) { theme { id name } userErrors { field message } }
}`

export async function POST(req: NextRequest) {
  const { password, action } = await req.json().catch(() => ({}))

  if (!password || password !== process.env.CONTROL_PASSWORD) {
    return NextResponse.json({ error: 'wrong password' }, { status: 401 })
  }
  if (action !== 'off' && action !== 'on') {
    return NextResponse.json({ error: 'action must be on/off' }, { status: 400 })
  }

  const themeId =
    action === 'off' ? process.env.MAINTENANCE_THEME_ID : process.env.LIVE_THEME_ID

  const data = await shopify(PUBLISH, {
    id: `gid://shopify/OnlineStoreTheme/${themeId}`,
  })

  const errs = data?.data?.themePublish?.userErrors
  if (data.errors || (errs && errs.length)) {
    // Most likely the themePublish exemption — surfaced plainly, not swallowed.
    return NextResponse.json(
      { ok: false, action, detail: data.errors ?? errs },
      { status: 422 },
    )
  }

  return NextResponse.json({ ok: true, action, published: data.data.themePublish.theme })
}
