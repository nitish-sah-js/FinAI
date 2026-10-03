'use client';

import { useEffect } from 'react';
import './globals.css';
import { useSettings } from '@/lib/store';

// Runs before first paint: apply the saved theme so a light-theme user never sees a dark flash.
const THEME_BOOT = `try{var s=JSON.parse(localStorage.getItem('copilot-settings')||'{}');if(s.state&&s.state.theme==='light'){document.documentElement.classList.add('light');document.documentElement.classList.remove('dark');}}catch(e){}`;

function ThemeSync() {
  const theme = useSettings((s) => s.theme);
  useEffect(() => {
    const html = document.documentElement;
    html.classList.toggle('light', theme === 'light');
    html.classList.toggle('dark', theme === 'dark');
  }, [theme]);
  // the terminal and the pet are separate Electron windows: follow theme changes made in the other one
  useEffect(() => {
    const onStorage = (e: StorageEvent) => { if (e.key === 'copilot-settings') useSettings.persist.rehydrate(); };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);
  return null;
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body onContextMenu={(e) => e.preventDefault()} /* Disable native context menu */>
        <ThemeSync />
        {children}
      </body>
    </html>
  );
}
