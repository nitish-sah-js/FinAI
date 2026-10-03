
import { contextBridge, ipcRenderer } from "electron";
contextBridge.exposeInMainWorld("pet", {
  onState: (cb: any) => ipcRenderer.on("state", (e, state) => cb(state)),
  openTerminal: () => ipcRenderer.send("open-terminal"),
  ack: (id: string) => ipcRenderer.send("ack", id),
  dismiss: () => ipcRenderer.send("dismiss"),
  setIgnoreMouse: (flag: boolean) => ipcRenderer.send("set-ignore-mouse", flag),
  showMenu: () => ipcRenderer.send("show-menu")
});
