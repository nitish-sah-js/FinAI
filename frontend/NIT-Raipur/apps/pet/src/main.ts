
import { app, BrowserWindow, Tray, Menu, shell, ipcMain, nativeImage, globalShortcut } from "electron";
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

  const shortcut = (config.get("shortcut") as string) || "CommandOrControl+Shift+Space";
  try {
    globalShortcut.register("Super+Shift+F23", () => {
      win?.isVisible() ? win.hide() : win?.show();
    });
  } catch(e) {
    globalShortcut.register(shortcut, () => {
      win?.isVisible() ? win.hide() : win?.show();
    });
  }

  tray = new Tray(nativeImage.createEmpty()); 
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
