const express = require('express');
const { WebSocketServer } = require('ws');
const cors = require('cors');
const http = require('http');

const app = express();
app.use(cors());
app.use(express.json());

const PORT = 8080;
const server = http.createServer(app);
const wss = new WebSocketServer({ server });

let chaosMode = 'none';

app.get('/api/v1/health', (req, res) => {
  res.json({
    mode: chaosMode === 'none' ? 'live' : 'degraded',
    degradedReasons: chaosMode !== 'none' ? [chaosMode] : [],
    services: [
      { name: 'ingestion', status: 'ok', latencyMs: 15, lastOkAt: new Date().toISOString(), rateLimit: null }
    ]
  });
});

app.get('/api/v1/portfolio', (req, res) => {
  res.json({
    asOf: new Date().toISOString(),
    baseCurrency: 'INR',
    totalValue: 15000000,
    holdings: [
      { ticker: 'RELIANCE', name: 'Reliance Industries', assetClass: 'equity', exchange: 'NSE', sector: 'Energy', quantity: 500, avgCost: 2500, lastPrice: 2800, marketValue: 1400000, weightPct: 9.3, dayChangePct: 1.2, exposureTags: ['large-cap'] },
      { ticker: 'TCS', name: 'Tata Consultancy Services', assetClass: 'equity', exchange: 'NSE', sector: 'IT', quantity: 300, avgCost: 3500, lastPrice: 3800, marketValue: 1140000, weightPct: 7.6, dayChangePct: -0.5, exposureTags: ['large-cap'] }
    ]
  });
});

app.get('/api/v1/market/prices', (req, res) => {
  const { ticker } = req.query;
  res.json({
    ticker: ticker || 'RELIANCE',
    interval: '1d',
    source: 'mock',
    asOf: new Date().toISOString(),
    candles: [
      { t: new Date(Date.now() - 86400000*2).toISOString(), o: 100, h: 110, l: 90, c: 105, v: 1000 },
      { t: new Date(Date.now() - 86400000).toISOString(), o: 105, h: 115, l: 100, c: 112, v: 1200 },
      { t: new Date().toISOString(), o: 112, h: 120, l: 110, c: 118, v: 1500 }
    ]
  });
});

app.get('/api/v1/alerts', (req, res) => {
  res.json({
    alerts: [
      { id: 'a1', tier: 2, createdAt: new Date().toISOString(), title: 'Earnings Miss Risk', reason: 'TCS negative sentiment spike', tickers: ['TCS'], anomalyType: 'sentiment', materiality: 'high', confidence: 0.85, evidenceIds: [], linkedQueryId: 'q1', status: 'new', channels: [{type: 'pet', status: 'sent'}] },
      { id: 'a2', tier: 1, createdAt: new Date().toISOString(), title: 'Volume Breakout', reason: 'RELIANCE volume 3x avg', tickers: ['RELIANCE'], anomalyType: 'price', materiality: 'medium', confidence: 0.9, evidenceIds: [], linkedQueryId: null, status: 'new', channels: [] }
    ]
  });
});

app.post('/api/v1/queries', (req, res) => {
  res.json({ queryId: 'mock-query-' + Date.now() });
});

app.post('/api/v1/system/chaos', (req, res) => {
  chaosMode = req.body.mode;
  res.json({ status: 'ok' });
});

// Broadcast WS events periodically
setInterval(() => {
  const payload = JSON.stringify({
    type: 'price.tick',
    ts: new Date().toISOString(),
    seq: Date.now(),
    payload: { ticker: 'RELIANCE', price: 2800 + Math.random() * 10 - 5 }
  });
  wss.clients.forEach(client => {
    if (client.readyState === 1) { // OPEN
      client.send(payload);
    }
  });
}, 3000);

// Randomly emit a tier 2 alert every 30s for the pet pop-up
setInterval(() => {
  const payload = JSON.stringify({
    type: 'alert.created',
    ts: new Date().toISOString(),
    seq: Date.now(),
    payload: { id: 'a' + Date.now(), tier: 2, title: 'Sudden Macro Shock', reason: 'Unexpected rate hike discussion' }
  });
  wss.clients.forEach(client => {
    if (client.readyState === 1) {
      client.send(payload);
    }
  });
}, 30000);

server.listen(PORT, () => {
  console.log(`Mock server running on http://localhost:${PORT}`);
});
