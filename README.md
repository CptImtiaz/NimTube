# Store Switch

A password-protected ON/OFF button for a Shopify storefront. It works by
swapping which theme is published (`themePublish`): OFF publishes a
"Closed/Maintenance" theme, ON republishes the live theme.

## Environment variables (set in Vercel → Settings → Environment Variables)

| Variable | What it is |
|---|---|
| `SHOPIFY_STORE_DOMAIN` | `your-store.myshopify.com` |
| `SHOPIFY_ADMIN_TOKEN` | Custom-app Admin API token (`shpat_…`). Scopes: **`read_themes`, `write_themes` only** |
| `LIVE_THEME_ID` | Numeric id of your real/live theme |
| `MAINTENANCE_THEME_ID` | Numeric id of your "Closed" theme |
| `CONTROL_PASSWORD` | A secret you choose — required to use the button |

> Never commit these values. Enter them only in the Vercel dashboard.

## Setup

1. In Shopify admin, duplicate your live theme and edit the copy into a
   simple "We're closed, back soon" page. Note both theme IDs (the number
   in each theme's editor URL).
2. Create a custom app (Settings → Apps and sales channels → Develop apps),
   grant **only** `read_themes` and `write_themes`, install it, copy the token.
3. Add the five env vars above in Vercel.
4. Open the deployed URL, enter your control password, tap **Turn OFF / Turn ON**.

## Note on `themePublish`

Shopify may require a theme-API exemption for custom apps. If the first OFF
tap returns a permissions error, the button surfaces it — file the exemption
request and it will work once approved.
