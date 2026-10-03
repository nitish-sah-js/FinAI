const { app, BrowserWindow, Tray, Menu, nativeImage, globalShortcut, ipcMain, shell } = require('electron');
const path = require('path');
const Store = require('electron-store');

const store = new Store();
let tray = null;
let terminalWindow = null;
let petWindow = null;
let isQuitting = false;

// Custom protocol for serving static Next.js export
const { protocol } = require('electron');

app.commandLine.appendSwitch('disable-site-isolation-trials'); // Sometimes needed for custom protocols

// Create custom protocol to serve static files
const registerProtocol = () => {
  protocol.registerFileProtocol('app', (request, callback) => {
    const url = request.url.substr(6); // strip app://
    callback({ path: path.normalize(`${__dirname}/out/${url}`) });
  });
};

const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', (event, commandLine, workingDirectory) => {
    if (terminalWindow) {
      if (terminalWindow.isMinimized()) terminalWindow.restore();
      terminalWindow.focus();
    }
  });

  app.whenReady().then(() => {
    registerProtocol();
    createTray();
    createTerminalWindow();
    createPetWindow();

    const shortcut = store.get('shortcut', 'CommandOrControl+Shift+Space');
    registerGlobalShortcut(shortcut);

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createTerminalWindow();
    });
  });
}

function createTerminalWindow() {
  terminalWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 1100,
    minHeight: 700,
    frame: false,
    show: false, // Don't show immediately
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js')
    }
  });

  const isDev = !app.isPackaged;
  if (isDev) {
    terminalWindow.loadURL('http://localhost:3000/terminal');
    // terminalWindow.webContents.openDevTools();
  } else {
    terminalWindow.loadURL('app://terminal/index.html');
  }

  terminalWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      terminalWindow.hide();
    }
  });

  // Prevent external navigation
  terminalWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http')) {
      shell.openExternal(url);
    }
    return { action: 'deny' };
  });
}

function createPetWindow() {
  petWindow = new BrowserWindow({
    width: 450,
    height: 500,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    show: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js')
    }
  });

  // Clamp to bottom right of primary display
  const { screen } = require('electron');
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width, height } = primaryDisplay.workAreaSize;
  petWindow.setPosition(width - 450, height - 500);

  const isDev = !app.isPackaged;
  if (isDev) {
    petWindow.loadURL('http://localhost:3000/pet');
  } else {
    petWindow.loadURL('app://pet/index.html');
  }

  petWindow.setIgnoreMouseEvents(true, { forward: true });

  petWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      petWindow.hide();
    }
  });
}

function createTray() {
  // Use a generic icon or the pet idle image
  const iconPath = app.isPackaged ? path.join(process.resourcesPath, 'public/pet/idle.png') : path.join(__dirname, 'public/pet/idle.png');
  // Fallback to simple icon if doesn't exist
  tray = new Tray(nativeImage.createEmpty()); 
  
  const contextMenu = Menu.buildFromTemplate([
    { label: 'Open Terminal', click: () => { terminalWindow.show(); terminalWindow.focus(); } },
    { label: 'Show Pet', click: () => petWindow.show() },
    { label: 'Pin Pet', type: 'checkbox', checked: false, click: (item) => { store.set('pinPet', item.checked); } },
    { type: 'separator' },
    { label: 'Quit', click: () => { isQuitting = true; app.quit(); } }
  ]);
  
  tray.setToolTip('Utsava Terminal');
  tray.setContextMenu(contextMenu);
}

function registerGlobalShortcut(shortcut) {
  globalShortcut.unregisterAll();
  let success = false;
  try {
    success = globalShortcut.register('Super+Shift+F23', () => {
      handleCopilotShortcut();
    });
  } catch(e) {}
  
  if (!success) {
    globalShortcut.register(shortcut, () => {
      handleCopilotShortcut();
    });
  }
}

function handleCopilotShortcut() {
  if (petWindow.isVisible()) {
    terminalWindow.show();
    terminalWindow.focus();
    terminalWindow.webContents.send('focus-copilot');
  } else {
    petWindow.show();
    petWindow.webContents.send('show-copilot-thinking');
  }
}

app.on('before-quit', () => {
  isQuitting = true;
});

// IPC Handlers
ipcMain.handle('terminal:open', (e, { deepLink }) => {
  terminalWindow.show();
  terminalWindow.focus();
  if (deepLink) {
    terminalWindow.webContents.send('navigate', deepLink);
  }
});

ipcMain.handle('pet:show', () => petWindow.show());
ipcMain.handle('pet:hide', () => petWindow.hide());
ipcMain.handle('pet:setIgnoreMouse', (e, ignore) => {
  if (petWindow) petWindow.setIgnoreMouseEvents(ignore, { forward: true });
});
ipcMain.on('pet:moveBy', (e, dx, dy) => {
  if (petWindow) {
    const [x, y] = petWindow.getPosition();
    const safeDx = Number(dx) || 0;
    const safeDy = Number(dy) || 0;
    if (safeDx !== 0 || safeDy !== 0) {
      petWindow.setPosition(Math.round(x + safeDx), Math.round(y + safeDy));
    }
  }
});

ipcMain.handle('window:minimize', (e) => {
  const win = BrowserWindow.fromWebContents(e.sender);
  if (win) win.minimize();
});

ipcMain.handle('window:maximize', (e) => {
  const win = BrowserWindow.fromWebContents(e.sender);
  if (win) {
    if (win.isMaximized()) win.restore();
    else win.maximize();
  }
});

ipcMain.handle('window:close', (e) => {
  const win = BrowserWindow.fromWebContents(e.sender);
  if (win) win.close();
});

ipcMain.handle('settings:get', (e, key) => store.get(key));
ipcMain.handle('settings:set', (e, key, val) => store.set(key, val));
