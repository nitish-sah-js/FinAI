'use client';
// Live streams: orchestrator run events (WS /ws/{run_id}) and monitor alerts (WS /ws/alerts). docs/13 Prompt 13-A §6.
import { useEffect, useRef, useState } from 'react';
import { MONITOR_WS, ORCH_WS, withKey } from './config';
import type { AgentEvent, Alert, FinalAnswer, WsMessage } from './contracts';
import { getAlerts } from './api';

export type RunStatus = 'idle' | 'connecting' | 'running' | 'done' | 'error';

export function useRunStream(runId: string | null) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [final, setFinal] = useState<FinalAnswer | null>(null);
  const [status, setStatus] = useState<RunStatus>('idle');

  useEffect(() => {
    setEvents([]);
    setFinal(null);
    if (!runId) {
      setStatus('idle');
      return;
    }
    setStatus('connecting');
    let gotFinal = false;
    const ws = new WebSocket(withKey(`${ORCH_WS}/ws/${encodeURIComponent(runId)}`));
    ws.onopen = () => setStatus('running');
    ws.onmessage = (e) => {
      let msg: WsMessage;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg.type === 'event') {
        const ev = msg.data;
        setEvents((prev) => (prev.some((p) => p.seq === ev.seq) ? prev : [...prev, ev].sort((a, b) => a.seq - b.seq)));
      } else if (msg.type === 'final') {
        gotFinal = true;
        setFinal(msg.data);
        setStatus('done');
      }
    };
    ws.onerror = () => setStatus((s) => (gotFinal ? s : 'error'));
    ws.onclose = () => setStatus((s) => (gotFinal ? 'done' : s === 'running' ? 'error' : s));
    return () => ws.close();
  }, [runId]);

  return { events, final, status };
}

/** Monitor alerts: REST backlog + live WS with exponential reconnect (1 s → 30 s). */
export function useAlerts() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [connected, setConnected] = useState(false);
  const [latest, setLatest] = useState<Alert | null>(null);
  const retry = useRef(1000);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const upsert = (a: Alert) =>
      setAlerts((prev) => [a, ...prev.filter((p) => p.alert_id !== a.alert_id)].slice(0, 100));

    getAlerts()
      .then((list) => setAlerts((prev) => {
        const ids = new Set(prev.map((p) => p.alert_id));
        return [...prev, ...list.filter((a) => !ids.has(a.alert_id))];
      }))
      .catch(() => {});

    const connect = () => {
      if (stopped) return;
      ws = new WebSocket(withKey(`${MONITOR_WS}/ws/alerts`));
      ws.onopen = () => {
        retry.current = 1000;
        setConnected(true);
      };
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.type === 'alert' && msg.data) {
            upsert(msg.data);
            if (!msg.replay) setLatest(msg.data);
          } else if (msg.type === 'ack' && msg.alert_id) {
            setAlerts((prev) => prev.map((a) => (a.alert_id === msg.alert_id ? { ...a, acknowledged: true } : a)));
          }
        } catch {}
      };
      ws.onclose = () => {
        setConnected(false);
        if (stopped) return;
        timer = setTimeout(connect, retry.current);
        retry.current = Math.min(retry.current * 2, 30000);
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, []);

  const markAcked = (id: string) =>
    setAlerts((prev) => prev.map((a) => (a.alert_id === id ? { ...a, acknowledged: true } : a)));

  return { alerts, connected, latest, markAcked };
}

/** Orchestrator activity feed (WS /ws/activity): every event of every run; used by the pet. */
export function useActivity(onMsg: (m: WsMessage) => void) {
  const cb = useRef(onMsg);
  cb.current = onMsg;
  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let delay = 1000;
    const connect = () => {
      if (stopped) return;
      ws = new WebSocket(withKey(`${ORCH_WS}/ws/activity`));
      ws.onopen = () => (delay = 1000);
      ws.onmessage = (e) => {
        try {
          cb.current(JSON.parse(e.data));
        } catch {}
      };
      ws.onclose = () => {
        if (stopped) return;
        timer = setTimeout(connect, delay);
        delay = Math.min(delay * 2, 30000);
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, []);
}
