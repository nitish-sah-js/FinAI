const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('terminalApi', {
  open: (opts) => ipcRenderer.invoke('terminal:open', opts),
  minimize: () => ipcRenderer.invoke('window:minimize'),
  maximize: () => ipcRenderer.invoke('window:maximize'),
  close: () => ipcRenderer.invoke('window:close'),
  getSettings: (key) => ipcRenderer.invoke('settings:get', key),
  setSettings: (key, val) => ipcRenderer.invoke('settings:set', key, val),
  onDeepLink: (callback) => {
    ipcRenderer.on('navigate', (e, link) => callback(link));
  },
  onFocusCopilot: (callback) => {
    ipcRenderer.on('focus-copilot', () => callback());
  }
});

contextBridge.exposeInMainWorld('petApi', {
  show: () => ipcRenderer.invoke('pet:show'),
  hide: () => ipcRenderer.invoke('pet:hide'),
  setIgnoreMouse: (ignore) => ipcRenderer.invoke('pet:setIgnoreMouse', ignore),
  moveBy: (dx, dy) => ipcRenderer.send('pet:moveBy', dx, dy),
  onShowCopilotThinking: (callback) => {
    ipcRenderer.on('show-copilot-thinking', () => callback());
  }
});
