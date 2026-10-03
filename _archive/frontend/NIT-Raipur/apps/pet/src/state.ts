
export type AgentEvent = { run_id: string; status: string; node: string; };
export type Alert = { alert_id: string; tier: number; headline: string; tickers: string[]; confidence: number; deeplink: string; acknowledged: boolean; };
export type State = { mode: "idle"|"thinking"|"alert"; activeRunId: string | null; nodesDone: number; nodesTotal: number; lastAlert: Alert | null; muted: boolean; };
export function reduceState(state: State, msg: any): State {
  const s = { ...state };
  if (msg.type === "MUTE") { s.muted = true; return s; }
  if (msg.type === "EVENT") {
    if (msg.data.status === "started") {
      s.mode = "thinking"; s.activeRunId = msg.data.run_id; s.nodesDone = 0;
    } else if (msg.data.status === "finished") {
      s.nodesDone++;
    }
  }
  if (msg.type === "FINAL") { s.mode = "idle"; }
  if (msg.type === "ALERT") {
    s.lastAlert = msg.data;
    s.mode = "alert";
  }
  return s;
}
