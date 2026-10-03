# 14 — Desktop Pet (Electron)

| | |
|---|---|
| **Owner / Laptop** | Frontend dev (after 13 is stable), **L3 "Edge & UI"** |
| **Depends on** | [01_CONTRACTS.md](01_CONTRACTS.md) (`Alert`, `AgentEvent`), monitor `WS /ws/alerts` (11), orchestrator `WS /ws/{run_id}` (04), terminal deeplinks `/run/new?q=` (13) |
| **Provides** | A small always-on-top companion that reacts to alerts and running analyses, and opens the terminal on click |
| **Build window** | H14–H20. It's a "cut early" item: Telegram goes first, then the pet's copilot-button flow |

---

## 1. Behaviour

| State | Trigger | Visual |
|---|---|---|
| `idle` | Default, or 10 s after the last event | Slow breathing loop, amber eyes blink every 4–7 s |
| `thinking` | Any orchestrator run is active: an `AgentEvent` with status `started` and no `final` yet | Eyes become a spinning dotted ring. A tiny counter shows how many nodes have finished, for example `7/13` |
| `alert` | An `Alert` arrives | **Tier 1:** a 600 ms hop plus a saffron dot. **Tier 2/3:** hop plus a speech bubble showing `headline`, `tickers` and confidence, with **Analyze** and **Dismiss** buttons. The bubble auto-hides after 12 s (Tier 2) and stays up for Tier 3 |

- **Click on the pet** opens the terminal: the last alert's `deeplink` if there's an unacknowledged alert, else the active run (`/run/{run_id}`), else `/`.
- **Drag** the pet to move it, and the position persists. **Right-click** opens a context menu: Open terminal · Mute 30 min · Quit.
- **Tray icon** menu: Show/Hide pet · Mute alerts · Reconnect · Settings (server URLs) · Quit.
- **Mute** suppresses the Tier 1/2 animations, but a Tier 3 still shows the bubble.

## 2. Folder layout

```
apps/pet/
├── package.json            # electron, electron-builder, ws (node), electron-store
├── electron-builder.yml
├── src/
│   ├── main.ts             # BrowserWindow, tray, IPC, WS clients (in main process)
│   ├── preload.ts          # contextBridge: onState, onAlert, openTerminal, ack, setIgnoreMouse
│   ├── ws.ts               # ReconnectingSocket (exponential backoff 1s→30s, heartbeat)
│   ├── state.ts            # pure reducer: (state, msg) → state  (unit-tested)
│   └── config.ts           # electron-store: monitorWs, orchWs, terminalUrl, position, muted
├── renderer/
│   ├── index.html
│   ├── pet.css             # sprite/CSS animations per state
│   ├── pet.js              # listens to preload events, toggles classes, renders bubble
│   └── assets/ pet_idle.png pet_think.png pet_alert.png (or a Lottie JSON)
└── tests/state.test.ts
```

## 3. Build prompt (paste into a coding LLM)

```
Build an Electron (v30+) desktop pet in apps/pet with TypeScript (main/preload) and plain HTML/CSS/JS renderer.

1. main.ts: create a BrowserWindow {width:220,height:260, frame:false, transparent:true, resizable:false,
   alwaysOnTop:true, skipTaskbar:true, hasShadow:false, webPreferences:{preload, contextIsolation:true,
   nodeIntegration:false}}. Call win.setAlwaysOnTop(true,"screen-saver") and win.setVisibleOnAllWorkspaces(true).
   Restore the saved position from electron-store; save it on 'moved'.
2. Click-through: the transparent area should not block clicks. In the renderer, on mouseenter/mouseleave of the
   pet sprite and bubble, call window.pet.setIgnoreMouse(false/true); in main, call
   win.setIgnoreMouseEvents(flag, {forward:true}).
3. ws.ts: class ReconnectingSocket(url, onMessage) using the 'ws' package in the MAIN process. It uses exponential
   backoff 1s,2s,4s… capped at 30s, plus jitter; it sends a ping every 15s and treats 2 missed pongs as a reconnect.
   It exposes status 'connecting'|'open'|'closed'.
4. Connect two sockets:
   a) MONITOR: `${monitorWs}/ws/alerts` → messages {"type":"alert","data":Alert} (see 01_CONTRACTS Alert).
   b) ORCHESTRATOR activity: `${orchWs}/ws/activity` → messages {"type":"event","data":AgentEvent} for ALL runs
      (if that endpoint is not available, poll GET {orchUrl}/runs?limit=1 every 3 s and treat a run without
      a final as active).
5. state.ts: a pure reducer, state = {mode:'idle'|'thinking'|'alert', activeRunId, nodesDone, nodesTotal:13,
   lastAlert: Alert|null, muted:boolean}. Rules:
   - an AgentEvent with status 'started' and a new run_id → mode 'thinking', activeRunId = run_id, nodesDone = 0
   - an AgentEvent with status in finished|degraded|skipped|failed → nodesDone++
   - a {"type":"final"} for the active run, or node 'validator' finished → mode 'idle' after 1.5 s
   - an Alert → lastAlert = alert; mode 'alert' (tier 1: 2 s, then back; tier 2: 12 s; tier 3: until ack)
   - an alert takes priority over thinking; afterwards, return to thinking if a run is still active.
   Send the state to the renderer via webContents.send('state', state).
6. preload.ts: contextBridge.exposeInMainWorld('pet', {onState(cb), openTerminal(), ack(alertId), dismiss(),
   setIgnoreMouse(flag), showMenu()}).
7. openTerminal(): if lastAlert && !acknowledged → shell.openExternal(lastAlert.deeplink) and mark it acked
   (also POST {monitorUrl}/alerts/{id}/ack); else if activeRunId → `${terminalUrl}/run/${activeRunId}`;
   else terminalUrl.
8. renderer: a sprite with CSS classes .idle (breathing scale 1→1.03, 3s), .thinking (rotating ring + counter
   text "nodesDone/13"), .alert-t1 (hop translateY -18px, 600ms), .alert-t2/.alert-t3 (hop + bubble).
   The bubble shows alert.headline (≤ 25 words), ticker chips, confidence %, and buttons Analyze/Dismiss.
   Colours: ink #0b0f14, amber #ffb000, saffron #ff7a1a; font JetBrains Mono (bundled locally, no CDN).
   Respect prefers-reduced-motion (no hop, use colour change only).
9. Tray: Tray icon with menu Show/Hide, Mute 30 min, Reconnect, Settings…, Quit. Settings opens a small
   window with 3 inputs (monitor WS, orchestrator WS, terminal URL) saved to electron-store.
10. Packaging: electron-builder.yml with appId "in.copilot.pet", win target "nsis" and "portable", icon
    assets/icon.ico. Add the scripts "dev": "tsc && electron .", "dist": "electron-builder --win".
11. tests/state.test.ts (vitest): thinking→idle on final; alert priority; tier-3 persists until ack; mute
    suppresses tier 1/2 but not tier 3.
12. MOCK mode: if env PET_MOCK=1, don't connect; instead, every 30 s emit a fake run (13 node events over 8 s)
    and every 45 s a fake Alert (alternating tier 1/2) so the pet can be demoed alone.
Output complete files.
```

## 4. Example messages

```json
{"type":"alert","data":{"alert_id":"al_20261003_3fa21c","tier":2,"kind":"weather_threshold",
 "tickers":["ITC.NS","UPL.NS"],"headline":"Cyclone near Odisha coast; kharif stress risk for ITC, UPL holdings. Confidence medium.",
 "reason":"IMD severe cyclonic storm + NDVI −1.4σ in coastal districts","impact_score":0.62,"confidence":0.58,
 "evidence_ids":["ev_weather_004"],"created_at":"2026-10-03T09:10:00Z","cooldown_key":"weather_threshold:ITC.NS",
 "deeplink":"http://192.168.43.103:3000/run/new?q=Cyclone%20near%20Odisha%20impact%20on%20ITC%20and%20UPL","acknowledged":false}}
```
```json
{"type":"event","data":{"run_id":"run_20261003141502_a91f","seq":4,"node":"agri_agent","status":"started","ts":"2026-10-03T08:45:03Z","host":"L2"}}
```

## 5. Mock mode
`PET_MOCK=1 npm run dev` cycles fake runs and alerts (step 12), so the pet can be built and shown with no backend.

## 6. Acceptance checklist
- [ ] The window is transparent, frameless and stays above other windows, and clicks pass through the empty area
- [ ] Killing the monitor shows a grey "disconnected" eye. Restarting it reconnects within 30 s with no app restart
- [ ] Submitting a query in the terminal turns the pet to `thinking` with a counter. It returns to `idle` after the final answer
- [ ] A Tier-2 alert shows the bubble, and **Analyze** opens the deeplink in the default browser and auto-runs the query
- [ ] The position persists across restarts. Mute works. The tray works
- [ ] `npm run dist` produces a Windows installer and a portable exe

## 7. Integration hooks
- **Monitor (11):** must serve `WS /ws/alerts` and `POST /alerts/{id}/ack`.
- **Orchestrator (04):** optional `WS /ws/activity`, which broadcasts every `AgentEvent` of every run. If it's missing, the pet falls back to polling `GET /runs?limit=1`.
- **Terminal (13):** `/run/new?q=` and `/run/{id}` routes.

## 8. Sources
- Electron transparent and frameless windows: https://www.electronjs.org/docs/latest/tutorial/custom-window-styles
- `setIgnoreMouseEvents` with forward: https://www.electronjs.org/docs/latest/api/browser-window#winsetignoremouseeventsignore-options
- electron-builder Windows targets: https://www.electron.build/nsis
