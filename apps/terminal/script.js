
const fs = require("fs");
const path = require("path");

const components = [
  "QueryBox.tsx", "IntentDecomposition.tsx",
  "AgentGraph.tsx", "AgentNode.tsx",
  "AnswerPanel.tsx", "CitationChip.tsx",
  "EvidenceDrawer.tsx", "EvidenceCard.tsx",
  "FreshnessBadge.tsx", "DegradedBanner.tsx",
  "RedTeamCard.tsx", "ValidatorBadge.tsx",
  "HedgeTable.tsx", "WhatIfSliders.tsx",
  "SectorHeatmap.tsx", "PriceChart.tsx",
  "WeatherChart.tsx", "QuotaMeter.tsx",
  "LatencyWaterfall.tsx", "HealthGrid.tsx",
  "CalibrationPlot.tsx", "Scoreboard.tsx",
  "TickerTape.tsx", "AlertToaster.tsx",
  "NavRail.tsx"
];

const libFiles = [
  "contracts.ts", "schema.json", "config.ts",
  "api.ts", "ws.ts", "mock/replay.ts",
  "graphLayout.ts", "citations.ts", "store.ts"
];

const baseComponentPath = path.join(__dirname, "src", "components");
const baseLibPath = path.join(__dirname, "src", "lib");
const mockPath = path.join(__dirname, "src", "lib", "mock");

if (!fs.existsSync(baseComponentPath)) fs.mkdirSync(baseComponentPath, { recursive: true });
if (!fs.existsSync(baseLibPath)) fs.mkdirSync(baseLibPath, { recursive: true });
if (!fs.existsSync(mockPath)) fs.mkdirSync(mockPath, { recursive: true });

components.forEach(c => {
  const file = path.join(baseComponentPath, c);
  if (!fs.existsSync(file)) {
    const name = c.replace(".tsx", "");
    fs.writeFileSync(file, `export default function ${name}() {\n  return <div>${name}</div>;\n}\n`);
  }
});

libFiles.forEach(f => {
  const file = path.join(baseLibPath, f);
  if (!fs.existsSync(file)) {
    if (f.endsWith(".json")) {
      fs.writeFileSync(file, `{}`);
    } else {
      fs.writeFileSync(file, `// ${f}`);
    }
  }
});

const pages = [
  "run/[runId]", "run/new", "evidence/[id]",
  "health", "latency", "backtest",
  "portfolio", "paper", "settings"
];

const appPath = path.join(__dirname, "src", "app");
pages.forEach(p => {
  const dir = path.join(appPath, p);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, "page.tsx");
  if (!fs.existsSync(file)) {
    fs.writeFileSync(file, `export default function Page() {\n  return <div>${p} page</div>;\n}\n`);
  }
});
console.log("Scaffold complete.");

