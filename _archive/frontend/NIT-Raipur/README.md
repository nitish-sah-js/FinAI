# Utsava Terminal

A desktop financial-intelligence terminal with an agentic copilot and a desktop pet.

## Deliverables
- **`/contracts`**: OpenAPI 3.1 specification (`openapi.yaml`). Use `npm run generate` to build Zod schemas/types (requires `openapi-typescript`).
- **`/dev-mock-server`**: Standalone Express/WS mock server providing schema-valid, updating data without a real backend.
- **`/app`**: Next.js (App Router, Strict TS) + Electron application serving as the frontend.

## Architecture Highlights
- **Electron Shell**: Strict context isolation, `nodeIntegration: false`, sandbox enabled. Manages the system tray, two frameless windows (Terminal and Pet), global shortcuts, and custom IPC `app://` protocol.
- **Next.js UI**: Output set to `export`. Uses TanStack Query for caching REST and Zustand for state. Framer Motion handles the pet animations.

## How to Run

1. **Install Dependencies (Root)**
   \`\`\`bash
   npm install
   \`\`\`
   
2. **Start the Mock Server**
   Open a new terminal and run:
   \`\`\`bash
   npm run mock
   \`\`\`
   This will start the mock server on `http://localhost:8080`.

3. **Start the App (Dev Mode)**
   Open another terminal and run:
   \`\`\`bash
   npm run dev
   \`\`\`
   This runs Next.js in dev mode and waits for `localhost:3000` to boot before launching Electron.

## Building the Installer (Windows)
To build the `.exe` installer:
\`\`\`bash
cd app
npm run build
npm run electron:build
\`\`\`
The installer will be generated in `app/dist/`.

## Configuration
- **Backend URL**: By default, the app points to `http://localhost:8080`. You can change this in the app's Settings panel (persisted via `electron-store`).
- **Shortcut**: The default copilot shortcut is \`Ctrl+Shift+Space\`.

## Verification Checklists Completed
- [x] Contracts defined in `openapi.yaml`.
- [x] Dev mock server created with REST and WS support.
- [x] Electron structure established (`main.js`, `preload.js`).
- [x] Next.js layout strict static export mode.
- [x] Pet window frameless, click-through, animated.
- [x] Terminal window customized layout without browser chrome.
