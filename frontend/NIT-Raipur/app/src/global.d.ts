interface TerminalApi {
  open: (opts: { deepLink?: string }) => void;
  minimize: () => void;
  maximize: () => void;
  close: () => void;
  getSettings: (key: string) => Promise<any>;
  setSettings: (key: string, val: any) => Promise<void>;
  onDeepLink: (callback: (link: string) => void) => void;
  onFocusCopilot: (callback: () => void) => void;
}

interface PetApi {
  show: () => void;
  hide: () => void;
  setIgnoreMouse: (ignore: boolean) => void;
  onShowCopilotThinking: (callback: () => void) => void;
}

declare global {
  interface Window {
    terminalApi?: TerminalApi;
    petApi?: PetApi;
  }
}

export {};
