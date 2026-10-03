
const fs = require("fs");
const path = require("path");

const base = path.join(__dirname);
["src", "renderer", "tests", "renderer/assets"].forEach(d => fs.mkdirSync(path.join(base, d), { recursive: true }));

const pkg = {
  "name": "pet",
  "version": "1.0.0",
  "main": "dist/main.js",
  "scripts": {
    "dev": "tsc && electron .",
    "build": "tsc",
    "dist": "electron-builder --win",
    "test": "vitest"
  },
  "dependencies": {
    "electron-store": "^8.2.0",
    "ws": "^8.16.0"
  },
  "devDependencies": {
    "electron": "^30.0.0",
    "electron-builder": "^24.13.3",
    "typescript": "^5.0.0",
    "vitest": "^1.4.0"
  }
};
fs.writeFileSync(path.join(base, "package.json"), JSON.stringify(pkg, null, 2));

const tsconfig = {
  "compilerOptions": {
    "target": "ES2022",
    "module": "CommonJS",
    "outDir": "./dist",
    "rootDir": "./src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": ["src/**/*"]
};
fs.writeFileSync(path.join(base, "tsconfig.json"), JSON.stringify(tsconfig, null, 2));

const electronBuilder = `
appId: "in.copilot.pet"
productName: "Copilot Pet"
win:
  target:
    - nsis
    - portable
`;
fs.writeFileSync(path.join(base, "electron-builder.yml"), electronBuilder);

const mainTs = `
import { app, BrowserWindow, Tray, Menu, shell, ipcMain } from "electron";
import * as path from "path";
import { config } from "./config";
import { ReconnectingSocket } from "./ws";
import { reduceState, State, AgentEvent, Alert } from "./state";

let win: BrowserWindow | null = null;
let tray: Tray | null = null;
let currentState: State = { mode: "idle", activeRunId: null, nodesDone: 0, nodesTotal: 13, lastAlert: null, muted: false };

function createWindow() {
  win = new BrowserWindow({
    width: 220,
    height: 260,
    frame: false,
    transparent: true,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.setAlwaysOnTop(true, "screen-saver");
  win.setVisibleOnAllWorkspaces(true);

  const pos = config.get("position") as { x: number, y: number } | undefined;
  if (pos) {
    win.setPosition(pos.x, pos.y);
  }

  win.on("moved", () => {
    if (win) {
      const [x, y] = win.getPosition();
      config.set("position", { x, y });
    }
  });

  win.loadFile(path.join(__dirname, "../renderer/index.html"));
}

app.whenReady().then(() => {
  createWindow();

  tray = new Tray(path.join(__dirname, "../renderer/assets/icon.png")); // Mock icon path
  const contextMenu = Menu.buildFromTemplate([
    { label: "Show/Hide", click: () => { win?.isVisible() ? win.hide() : win?.show(); } },
    { label: "Mute 30 min", click: () => { 
        currentState = reduceState(currentState, { type: "MUTE" }); 
        win?.webContents.send("state", currentState);
      } 
    },
    { label: "Reconnect", click: () => { /* reconnect logic */ } },
    { label: "Settings", click: () => { /* open settings */ } },
    { label: "Quit", click: () => { app.quit(); } }
  ]);
  tray.setContextMenu(contextMenu);

  ipcMain.on("set-ignore-mouse", (e, flag: boolean) => {
    if (win) win.setIgnoreMouseEvents(flag, { forward: true });
  });

  ipcMain.on("open-terminal", () => {
    const termUrl = config.get("terminalUrl") as string || "http://localhost:3000";
    if (currentState.lastAlert && !currentState.lastAlert.acknowledged) {
      shell.openExternal(currentState.lastAlert.deeplink);
      // Mock ack
      currentState.lastAlert.acknowledged = true;
    } else if (currentState.activeRunId) {
      shell.openExternal(termUrl + "/run/" + currentState.activeRunId);
    } else {
      shell.openExternal(termUrl);
    }
  });
  
  if (process.env.PET_MOCK === "1") {
    // Mock run every 30s
    setInterval(() => {
       const runId = "run_" + Date.now();
       currentState = reduceState(currentState, { type: "EVENT", data: { run_id: runId, status: "started", node: "router" } as any });
       win?.webContents.send("state", currentState);
       
       let count = 0;
       const int = setInterval(() => {
         count++;
         currentState = reduceState(currentState, { type: "EVENT", data: { run_id: runId, status: "finished", node: "node_" + count } as any });
         win?.webContents.send("state", currentState);
         if (count >= 13) clearInterval(int);
       }, 600);
       
       setTimeout(() => {
         currentState = reduceState(currentState, { type: "FINAL" });
         win?.webContents.send("state", currentState);
       }, 8500);
    }, 30000);

    // Mock alert every 45s
    setInterval(() => {
      const alert: Alert = {
        alert_id: "al_" + Date.now(), tier: 2, headline: "Cyclone approaching",
        tickers: ["ITC"], confidence: 0.8, deeplink: "http://localhost:3000/run/new", acknowledged: false
      };
      currentState = reduceState(currentState, { type: "ALERT", data: alert });
      win?.webContents.send("state", currentState);
    }, 45000);
  }
});
`;
fs.writeFileSync(path.join(base, "src", "main.ts"), mainTs);

const preloadTs = `
import { contextBridge, ipcRenderer } from "electron";
contextBridge.exposeInMainWorld("pet", {
  onState: (cb: any) => ipcRenderer.on("state", (e, state) => cb(state)),
  openTerminal: () => ipcRenderer.send("open-terminal"),
  ack: (id: string) => ipcRenderer.send("ack", id),
  dismiss: () => ipcRenderer.send("dismiss"),
  setIgnoreMouse: (flag: boolean) => ipcRenderer.send("set-ignore-mouse", flag),
  showMenu: () => ipcRenderer.send("show-menu")
});
`;
fs.writeFileSync(path.join(base, "src", "preload.ts"), preloadTs);

const configTs = `
import Store from "electron-store";
export const config = new Store({
  defaults: {
    monitorWs: "ws://localhost:8000",
    orchWs: "ws://localhost:8001",
    terminalUrl: "http://localhost:3000",
    muted: false
  }
});
`;
fs.writeFileSync(path.join(base, "src", "config.ts"), configTs);

const wsTs = `
export class ReconnectingSocket {
  // stub
}
`;
fs.writeFileSync(path.join(base, "src", "ws.ts"), wsTs);

const stateTs = `
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
`;
fs.writeFileSync(path.join(base, "src", "state.ts"), stateTs);

const html = `
<!DOCTYPE html>
<html>
<head>
  <link rel="stylesheet" href="pet.css">
</head>
<body>
  <div id="pet" class="idle"></div>
  <div id="bubble" style="display:none;"></div>
  <script src="pet.js"></script>
</body>
</html>
`;
fs.writeFileSync(path.join(base, "renderer", "index.html"), html);

const css = `
body { margin: 0; overflow: hidden; background: transparent; font-family: "JetBrains Mono", monospace; }
#pet { width: 100px; height: 100px; background: #ffb000; border-radius: 50%; margin: 50px auto; transition: all 0.3s; cursor: pointer; }
#pet.idle { animation: breathe 3s infinite alternate; }
#pet.thinking { border: 4px dashed #0b0f14; animation: spin 2s linear infinite; }
#pet.alert-t1 { animation: hop 0.6s; }
@keyframes breathe { from { transform: scale(1); } to { transform: scale(1.03); } }
@keyframes spin { 100% { transform: rotate(360deg); } }
@keyframes hop { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-18px); } }
`;
fs.writeFileSync(path.join(base, "renderer", "pet.css"), css);

const js = `
const pet = document.getElementById("pet");
window.pet.onState((state) => {
  pet.className = state.mode;
  if (state.mode === "alert") pet.classList.add("alert-t1");
});
pet.addEventListener("mouseenter", () => window.pet.setIgnoreMouse(false));
pet.addEventListener("mouseleave", () => window.pet.setIgnoreMouse(true));
pet.addEventListener("click", () => window.pet.openTerminal());
// Start ignoring
window.pet.setIgnoreMouse(true);
`;
fs.writeFileSync(path.join(base, "renderer", "pet.js"), js);

// Empty icon mock
fs.writeFileSync(path.join(base, "renderer", "assets", "icon.png"), "");

console.log("Pet generated.");

