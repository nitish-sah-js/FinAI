'use client';
// Settings sent with every QueryRequest (docs/13 Prompt 13-A §7). Persisted to localStorage.
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import type { ChaosFlags, Lang, LlmMode } from './contracts';

export type Theme = 'dark' | 'light';

export interface SettingsState {
  theme: Theme;
  llmMode: LlmMode;
  lang: Lang;
  chaos: ChaosFlags;
  asOf: string | null;
  approvedBy: string;
  setLlmMode: (m: LlmMode) => void;
  setLang: (l: Lang) => void;
  setChaos: (c: Partial<ChaosFlags>) => void;
  setAsOf: (d: string | null) => void;
  setApprovedBy: (n: string) => void;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
}

const safeStorage = createJSONStorage(() => {
  try {
    return window.localStorage;
  } catch {
    const mem: Record<string, string> = {};
    return { getItem: (k) => mem[k] ?? null, setItem: (k, v) => void (mem[k] = v), removeItem: (k) => void delete mem[k] };
  }
});

export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      theme: 'dark',
      llmMode: 'local',
      lang: 'en',
      chaos: { weather_down: false, force_rate_limit: false, agri_raster_missing: false, vector_down: false, slow_network_ms: 0 },
      asOf: null,
      approvedBy: 'analyst',
      setLlmMode: (llmMode) => set({ llmMode }),
      setLang: (lang) => set({ lang }),
      setChaos: (c) => set((s) => ({ chaos: { ...s.chaos, ...c } })),
      setAsOf: (asOf) => set({ asOf }),
      setApprovedBy: (approvedBy) => set({ approvedBy }),
      setTheme: (theme) => set({ theme }),
      toggleTheme: () => set((s) => ({ theme: s.theme === 'dark' ? 'light' : 'dark' })),
    }),
    { name: 'copilot-settings', storage: safeStorage },
  ),
);
