'use client';

import './globals.css';

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body onContextMenu={(e) => e.preventDefault()} /* Disable native context menu */>
        {children}
      </body>
    </html>
  );
}
