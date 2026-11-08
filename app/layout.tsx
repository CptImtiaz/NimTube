export const metadata = {
  title: 'Store Switch',
  description: 'Remote on/off switch for the Shopify storefront',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0 }}>{children}</body>
    </html>
  )
}
