# Store Switch (overlay)

A password-protected ON/OFF button for the Wellessia storefront. A tiny snippet
in the Shopify theme checks this app on every page load; when the switch is ON,
it shows a full-screen "We'll be back soon" overlay.

- `app/page.tsx` — the password-protected control button
- `app/api/toggle/route.ts` — flips the flag in Upstash (needs the password)
- `app/api/status/route.ts` — PUBLIC; the storefront reads the flag here

No OAuth, no Shopify token. The on/off state lives as a single key in Upstash
Redis.

## Environment variables (set in Vercel → Settings → Environment Variables)

| Variable | What it is |
|---|---|
| `UPSTASH_REDIS_REST_URL` | From your Upstash database (REST section) |
| `UPSTASH_REDIS_REST_TOKEN` | From your Upstash database (REST section) |
| `CONTROL_PASSWORD` | A secret you choose — required to use the button |

## Shopify snippet

Create `snippets/maintenance.liquid` in your theme with the script below, then
add `{% render 'maintenance' %}` right after `<head>` in `layout/theme.liquid`.

```liquid
<script>
  fetch("https://nimtube.vercel.app/api/status")
    .then(r => r.json())
    .then(d => {
      if (d.maintenance) {
        document.documentElement.innerHTML =
          '<body style="display:flex;align-items:center;justify-content:center;height:100vh;margin:0;font-family:sans-serif;background:#111;color:#fff;text-align:center"><div><h1>We\'ll be back soon</h1><p>Wellessia is updating. Check back shortly.</p></div></body>';
      }
    });
</script>
```

## Note

The overlay runs in the visitor's browser, so a technical visitor could bypass
it. It's ideal for pausing sales during updates, not a hard lock.
