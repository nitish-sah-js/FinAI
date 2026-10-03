'use client';
// Deep-link target used by monitor alerts and the pet: /run/new?q=...&alert=... (docs/13 Prompt 13-D §7).
import { useEffect } from 'react';

export default function NewRun() {
  useEffect(() => { window.location.replace('/terminal' + window.location.search); }, []);
  return null;
}
