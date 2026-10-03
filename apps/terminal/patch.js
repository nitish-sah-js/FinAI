
const fs = require("fs");
const path = require("path");
const file = path.join(__dirname, "src/app/terminal/page.tsx");
let content = fs.readFileSync(file, "utf8");

content = content.replace("const [health, setHealth] = useState('LIVE');", "const [activeTab, setActiveTab] = useState('Overview');\n  const [health, setHealth] = useState('LIVE');");

const navReplacement = `{/* MOCK TAG */}
            <div className="absolute -top-3 -right-3 bg-[#ffb000] text-[#121821] text-[9px] font-bold px-1.5 py-0.5 uppercase z-10 rounded-sm font-mono">MOCK</div>
            {['Overview', 'Portfolio', 'Paper', 'Backtest', 'Health'].map((tab) => (
              <button 
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={\`px-4 py-1.5 text-[12px] font-medium rounded-full transition-all duration-300 uppercase \${activeTab === tab ? 'bg-white/10 text-white' : 'text-[#7a8a9c] hover:text-white hover:bg-white/5'}\`}
              >
                {tab}
              </button>
            ))}
            
            <button className="relative inline-flex items-center justify-center rounded-full p-[1px] bg-[#1f2a36] ml-1 transition-transform hover:scale-105">
              <span className="flex items-center justify-center h-full w-full rounded-full px-4 py-1.5 text-[12px] font-medium transition-colors bg-black text-[#e6edf3] uppercase">
                Settings
              </span>
            </button>
          </div>
        </div>

        <div className="flex items-center justify-end gap-3 w-1/3" style={{ WebkitAppRegion: 'no-drag' } as any}>
          <div className="relative flex items-center px-3 py-1.5 bg-black/40 border border-[#1f2a36] rounded-full shadow-inner">
            <Search size={14} className="text-[#7a8a9c] mr-2" />
            <input 
              type="text" 
              placeholder="Search..." 
              className="bg-transparent border-none outline-none text-sm w-28 placeholder-[#7a8a9c] text-[#e6edf3]"
            />
          </div>
          
          <button onClick={handleMinimize} className="p-2 hover:bg-white/10 rounded-full transition-colors text-[#7a8a9c] hover:text-[#e6edf3]"><Minus size={16} /></button>
          <button onClick={handleMaximize} className="p-2 hover:bg-white/10 rounded-full transition-colors text-[#7a8a9c] hover:text-[#e6edf3]"><Maximize2 size={16} /></button>
          <button onClick={handleClose} className="p-2 hover:bg-[#ff4d6d]/20 hover:text-[#ff4d6d] rounded-full transition-colors text-[#7a8a9c]"><X size={16} /></button>
        </div>
      </div>

      {/* Ticker Tape */}
      <div className="w-full bg-[#121821]/80 backdrop-blur-md border-y border-[#1f2a36] flex items-center overflow-hidden shrink-0 h-7" style={{ WebkitAppRegion: 'no-drag' } as any}>
         <div className="flex whitespace-nowrap animate-marquee gap-12 font-mono text-[10px] uppercase font-bold px-4">
            <span className="text-[#e6edf3]">RELIANCE.NS <span className="text-[#3ddc97]">? 1.2%</span></span>
            <span className="text-[#e6edf3]">HDFCBANK.NS <span className="text-[#3ddc97]">? 0.8%</span></span>
            <span className="text-[#e6edf3]">INFY.NS <span className="text-[#ff4d6d]">? 1.1%</span></span>
            <span className="text-[#e6edf3]">TCS.NS <span className="text-[#ff4d6d]">? 0.4%</span></span>
            <span className="text-[#e6edf3]">ITC.NS <span className="text-[#3ddc97]">? 0.1%</span></span>
            <span className="text-[#e6edf3]">BRENT <span className="text-[#ff4d6d]">? 1.4%</span></span>
            <span className="text-[#e6edf3]">USDINR <span className="text-[#3ddc97]">? 0.1%</span></span>
            <span className="text-[#e6edf3]">NIFTY <span className="text-[#ff4d6d]">? 2.1%</span></span>
            {/* Duplicate for infinite effect */}
            <span className="text-[#e6edf3] ml-12">RELIANCE.NS <span className="text-[#3ddc97]">? 1.2%</span></span>
            <span className="text-[#e6edf3]">HDFCBANK.NS <span className="text-[#3ddc97]">? 0.8%</span></span>
            <span className="text-[#e6edf3]">INFY.NS <span className="text-[#ff4d6d]">? 1.1%</span></span>
         </div>
      </div>

      {/* Main Content Area */}`;

const regexNav = /\{\/\* MOCK TAG \*\/\}.*?\{\/\* Main Content Area \*\/\}/s;
content = content.replace(regexNav, navReplacement);

const contentReplacement = `<div className="flex-1 p-4 overflow-auto relative z-10 custom-scrollbar">
         {activeTab === "Overview" && (
         <div className="grid grid-cols-12 gap-3 auto-rows-min min-w-[1200px]">`;
const regexContent = /<div className="flex-1 p-4 overflow-auto relative z-10 custom-scrollbar">\s*<div className="grid grid-cols-12 gap-3 auto-rows-min min-w-\[1200px\]">/s;
content = content.replace(regexContent, contentReplacement);

const footerReplacement = `         )}
         {activeTab === "Portfolio" && <PortfolioView />}
         {activeTab === "Paper" && <PaperView />}
         {activeTab === "Backtest" && <BacktestView />}
         {activeTab === "Health" && <HealthView />}
      </div>
      </div>
    </BrailleTerrainBackground>
  );
}`;
const regexFooter = /<\/BrailleTerrainBackground>\s*\);\s*\}/s;
content = content.replace(regexFooter, footerReplacement);

const newViews = `
function PortfolioView() {
  return (
    <div className="grid grid-cols-12 gap-0 border border-[#1f2a36] bg-[#121821]/80 backdrop-blur-md h-full">
      <div className="col-span-12 p-4 border-b border-[#1f2a36]">
        <h2 className="text-[#3ddc97] text-3xl font-mono">?11.50 Cr <span className="text-[#7a8a9c] text-sm ml-2">Total AUM</span></h2>
      </div>
      <div className="col-span-12 overflow-auto p-0">
         <table className="w-full text-left text-xs font-mono">
           <thead className="bg-[#0b0f14] text-[#7a8a9c] uppercase border-b border-[#1f2a36]">
             <tr>
               <th className="p-3 font-normal">Asset</th>
               <th className="p-3 font-normal text-right">Holdings</th>
               <th className="p-3 font-normal text-right">Current Price</th>
               <th className="p-3 font-normal text-right">Cost Basis</th>
               <th className="p-3 font-normal text-right">Unrealized PnL</th>
             </tr>
           </thead>
           <tbody>
             {[
               { a: "RELIANCE.NS", h: "4,500", p: "2,850.40", c: "2,710.00", pnl: "+6.31,800", up: true },
               { a: "TCS.NS", h: "1,200", p: "3,892.10", c: "3,950.00", pnl: "-69,480", up: false },
               { a: "HDFCBANK.NS", h: "12,000", p: "1,432.00", c: "1,400.00", pnl: "+3,84,000", up: true },
               { a: "INFY.NS", h: "5,400", p: "1,421.90", c: "1,450.00", pnl: "-1,51,740", up: false },
               { a: "ITC.NS", h: "8,800", p: "412.30", c: "405.00", pnl: "+64,240", up: true }
             ].map(r => (
               <tr key={r.a} className="border-b border-[#1f2a36] hover:bg-white/5 transition-colors">
                 <td className="p-3 text-white font-bold">{r.a}</td>
                 <td className="p-3 text-[#e6edf3] text-right">{r.h}</td>
                 <td className="p-3 text-[#e6edf3] text-right">{r.p}</td>
                 <td className="p-3 text-[#7a8a9c] text-right">{r.c}</td>
                 <td className={\`p-3 text-right font-bold \${r.up ? "text-[#3ddc97]" : "text-[#ff4d6d]"}\`}>{r.pnl}</td>
               </tr>
             ))}
           </tbody>
         </table>
      </div>
    </div>
  );
}

function PaperView() {
  return (
    <div className="grid grid-cols-1 gap-0 border border-[#1f2a36] bg-[#121821]/80 backdrop-blur-md h-full">
      <div className="p-4 border-b border-[#1f2a36]">
        <h2 className="text-[#e6edf3] text-lg font-mono">Recent Executions <span className="text-[#ffb000] text-[10px] ml-2 px-1 border border-[#ffb000]/30 bg-[#ffb000]/10">SIMULATED</span></h2>
      </div>
      <div className="overflow-auto p-0">
         <table className="w-full text-left text-xs font-mono">
           <thead className="bg-[#0b0f14] text-[#7a8a9c] uppercase border-b border-[#1f2a36]">
             <tr>
               <th className="p-3 font-normal">Timestamp</th>
               <th className="p-3 font-normal">Action</th>
               <th className="p-3 font-normal">Ticker</th>
               <th className="p-3 font-normal text-right">Qty</th>
               <th className="p-3 font-normal text-right">Fill Price</th>
               <th className="p-3 font-normal text-right">Status</th>
             </tr>
           </thead>
           <tbody>
             {[
               { t: "10:14:02.144", a: "BUY", tick: "NIFTY_26OCT_22000_CE", q: "500", p: "124.50", s: "FILLED" },
               { t: "09:45:11.890", a: "SELL", tick: "RELIANCE.NS", q: "100", p: "2,845.00", s: "FILLED" },
               { t: "09:15:02.001", a: "BUY", tick: "ITC.NS", q: "1000", p: "410.15", s: "FILLED" },
               { t: "09:15:01.005", a: "SELL", tick: "BANKNIFTY_26OCT_48000_PE", q: "1200", p: "340.20", s: "PARTIAL" }
             ].map((r, i) => (
               <tr key={i} className="border-b border-[#1f2a36] hover:bg-white/5 transition-colors">
                 <td className="p-3 text-[#7a8a9c]">{r.t}</td>
                 <td className={\`p-3 font-bold \${r.a === "BUY" ? "text-[#3ddc97]" : "text-[#ff4d6d]"}\`}>{r.a}</td>
                 <td className="p-3 text-white">{r.tick}</td>
                 <td className="p-3 text-[#e6edf3] text-right">{r.q}</td>
                 <td className="p-3 text-[#e6edf3] text-right">{r.p}</td>
                 <td className="p-3 text-right"><span className="bg-[#1f2a36] px-2 py-0.5 text-[10px] text-white">{r.s}</span></td>
               </tr>
             ))}
           </tbody>
         </table>
      </div>
    </div>
  );
}

function BacktestView() {
  return (
    <div className="grid grid-cols-2 gap-4 h-full">
      <div className="border border-[#1f2a36] bg-[#121821]/80 backdrop-blur-md p-4 flex flex-col items-center justify-center h-48">
         <div className="text-[#7a8a9c] uppercase text-xs tracking-widest mb-2">Sharpe Ratio</div>
         <div className="text-4xl text-[#3ddc97] font-mono">2.14</div>
         <div className="text-[10px] text-[#e6edf3] mt-2">Benchmark: 1.05</div>
      </div>
      <div className="border border-[#1f2a36] bg-[#121821]/80 backdrop-blur-md p-4 flex flex-col items-center justify-center h-48">
         <div className="text-[#7a8a9c] uppercase text-xs tracking-widest mb-2">Max Drawdown</div>
         <div className="text-4xl text-[#ff4d6d] font-mono">-12.4%</div>
         <div className="text-[10px] text-[#e6edf3] mt-2">March 2024 Stress Test</div>
      </div>
      <div className="col-span-2 border border-[#1f2a36] bg-[#121821]/80 backdrop-blur-md p-4 flex-1">
         <div className="text-[#e6edf3] uppercase text-xs tracking-widest mb-4">Cumulative Returns (1Y)</div>
         <div className="h-40 flex items-end gap-1">
           {Array.from({length: 40}).map((_, i) => (
             <div key={i} className="flex-1 bg-[#3ddc97]/40 hover:bg-[#3ddc97] transition-colors" style={{ height: \`\${Math.random() * 80 + 20}%\` }}></div>
           ))}
         </div>
      </div>
    </div>
  );
}

function HealthView() {
  return (
    <div className="grid grid-cols-3 gap-2 h-full content-start">
      {[
        { n: "Orchestrator WS", s: "ONLINE", ms: "12ms" },
        { n: "Monitor WS", s: "ONLINE", ms: "8ms" },
        { n: "Quant Agent", s: "ONLINE", ms: "145ms" },
        { n: "Sentiment Agent", s: "DEGRADED", ms: "1200ms", warn: true },
        { n: "Agri Agent", s: "ONLINE", ms: "45ms" },
        { n: "VectorDB", s: "ONLINE", ms: "4ms" },
        { n: "Ingestion Pipeline", s: "OFFLINE", ms: "TIMEOUT", err: true }
      ].map(node => (
        <div key={node.n} className={\`border p-3 flex flex-col gap-2 bg-[#121821]/80 backdrop-blur-md \${node.err ? "border-[#ff4d6d]" : node.warn ? "border-[#ffb000]" : "border-[#1f2a36]"}\`}>
           <div className="text-[#e6edf3] font-mono text-xs font-bold">{node.n}</div>
           <div className="flex justify-between items-center">
             <span className={\`text-[10px] px-1 py-0.5 font-mono \${node.err ? "bg-[#ff4d6d]/20 text-[#ff4d6d]" : node.warn ? "bg-[#ffb000]/20 text-[#ffb000]" : "bg-[#3ddc97]/20 text-[#3ddc97]"}\`}>
               {node.s}
             </span>
             <span className="text-[#7a8a9c] text-[10px] font-mono">{node.ms}</span>
           </div>
        </div>
      ))}
    </div>
  );
}
`;
content = content + newViews;

fs.writeFileSync(file, content, "utf8");
console.log("Patched successfully!");

