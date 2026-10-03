
import Store from "electron-store";
export const config = new Store({
  defaults: {
    monitorWs: "ws://localhost:8000",
    orchWs: "ws://localhost:8001",
    terminalUrl: "http://localhost:3000",
    muted: false
  }
});
