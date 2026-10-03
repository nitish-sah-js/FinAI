'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import { Settings, Maximize2, X, Minus, Bell, Activity, Search, AlertTriangle, ShieldCheck, CheckCircle2, Loader2, Play } from 'lucide-react';
import { createChart } from 'lightweight-charts';
import { ReactFlow, Background, Controls, MarkerType } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, AreaChart, Area } from 'recharts';
import BrailleTerrainBackground from '@/components/BrailleTerrainBackground';

const initialNodes = [
  { id: '1', position: { x: 400, y: 0 }, data: { label: 'parse_intent' }, className: 'bg-[#2c2d2d] text-[#e6edf3] text-[10px] border border-white/20 px-3 py-2 font-mono rounded-none uppercase' },
  { id: '2', position: { x: 400, y: 60 }, data: { label: 'router' }, className: 'bg-[#2c2d2d] text-[#e6edf3] text-[10px] border border-white/20 px-3 py-2 font-mono rounded-none uppercase' },
  { id: '3', position: { x: 50, y: 140 }, data: { label: 'sentiment' }, className: 'bg-[#2c2d2d] text-[#e6edf3] text-[10px] border border-[#ffb000] px-3 py-2 font-mono rounded-none uppercase shadow-[0_0_8px_rgba(255,176,0,0.3)]' },
  { id: '4', position: { x: 190, y: 140 }, data: { label: 'weather' }, className: 'bg-[#2c2d2d] text-[#e6edf3] text-[10px] border border-[#3ddc97] px-3 py-2 font-mono rounded-none uppercase' },
  { id: '5', position: { x: 330, y: 140 }, data: { label: 'agri' }, className: 'bg-[#2c2d2d] text-[#e6edf3] text-[10px] border border-[#3ddc97] px-3 py-2 font-mono rounded-none uppercase' },
  { id: '6', position: { x: 470, y: 140 }, data: { label: 'macro' }, className: 'bg-[#2c2d2d] text-[#e6edf3] text-[10px] border border-[#3ddc97] px-3 py-2 font-mono rounded-none uppercase' },
  { id: '7', position: { x: 610, y: 140 }, data: { label: 'analog' }, className: 'bg-[#2c2d2d] text-[#e6edf3] text-[10px] border border-[#3ddc97] px-3 py-2 font-mono rounded-none uppercase' },
  { id: '8', position: { x: 750, y: 140 }, data: { label: 'exposure' }, className: 'bg-[#2c2d2d] text-[#7a8a9c] text-[10px] border border-white/20 border-dashed px-3 py-2 font-mono rounded-none uppercase' },
  { id: '9', position: { x: 400, y: 220 }, data: { label: 'join' }, className: 'bg-[#2c2d2d] text-[#e6edf3] text-[10px] border border-white/20 px-3 py-2 font-mono rounded-none uppercase' },
  { id: '10', position: { x: 400, y: 280 }, data: { label: 'quant_agent' }, className: 'bg-[#2c2d2d] text-[#e6edf3] text-[10px] border border-white/20 px-3 py-2 font-mono rounded-none uppercase' },
  { id: '11', position: { x: 400, y: 340 }, data: { label: 'synthesizer' }, className: 'bg-[#2c2d2d] text-[#e6edf3] text-[10px] border border-white/20 px-3 py-2 font-mono rounded-none uppercase' },
  { id: '12', position: { x: 250, y: 340 }, data: { label: 'red_team' }, className: 'bg-[#2c2d2d] text-[#e6edf3] text-[10px] border border-white/20 px-3 py-2 font-mono rounded-none uppercase' },
  { id: '13', position: { x: 550, y: 340 }, data: { label: 'validator' }, className: 'bg-[#2c2d2d] text-[#e6edf3] text-[10px] border border-white/20 px-3 py-2 font-mono rounded-none uppercase' },
];

const initialEdges = [
  { id: 'e1-2', source: '1', target: '2', animated: true, style: { stroke: '#3ddc97' } },
  { id: 'e2-3', source: '2', target: '3', animated: true, style: { stroke: '#ffb000' } },
  { id: 'e2-4', source: '2', target: '4', animated: false, style: { stroke: '#3ddc97' } },
  { id: 'e2-5', source: '2', target: '5', animated: false, style: { stroke: '#3ddc97' } },
  { id: 'e2-6', source: '2', target: '6', animated: false, style: { stroke: '#3ddc97' } },
  { id: 'e2-7', source: '2', target: '7', animated: false, style: { stroke: '#3ddc97' } },
  { id: 'e2-8', source: '2', target: '8', animated: false, style: { stroke: '#1f2a36', strokeDasharray: '4' } },
  { id: 'e3-9', source: '3', target: '9', animated: false, style: { stroke: '#1f2a36' } },
  { id: 'e4-9', source: '4', target: '9', animated: false, style: { stroke: '#1f2a36' } },
  { id: 'e5-9', source: '5', target: '9', animated: false, style: { stroke: '#1f2a36' } },
  { id: 'e6-9', source: '6', target: '9', animated: false, style: { stroke: '#1f2a36' } },
  { id: 'e7-9', source: '7', target: '9', animated: false, style: { stroke: '#1f2a36' } },
  { id: 'e9-10', source: '9', target: '10', animated: false, style: { stroke: '#1f2a36' } },
  { id: 'e10-11', source: '10', target: '11', animated: false, style: { stroke: '#1f2a36' } },
  { id: 'e10-12', source: '10', target: '12', animated: false, style: { stroke: '#1f2a36' } },
  { id: 'e10-13', source: '10', target: '13', animated: false, style: { stroke: '#1f2a36' } },
];

export default function TerminalWindow() {
  const [isLanding, setIsLanding] = useState(true);
  const [activeTab, setActiveTab] = useState('Overview');
  const [health, setHealth] = useState('LIVE');
  const [latency, setLatency] = useState(15);
  const [time, setTime] = useState('');

  useEffect(() => {
    const updateTime = () => {
      setTime(new Date().toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false }) + ' IST');
    };
    updateTime();
    const int = setInterval(updateTime, 1000);
    return () => clearInterval(int);
  }, []);

  const handleMinimize = () => window.terminalApi?.minimize();
  const handleMaximize = () => window.terminalApi?.maximize();
  const handleClose = () => window.terminalApi?.close();

  return (
    <BrailleTerrainBackground 
      background="#050505" 
      color="#c3cad4"
      className="h-screen w-screen overflow-hidden text-zinc-300 selection:bg-indigo-500/30"
    >
      <div className="flex flex-col h-full w-full font-sans text-sm">
        {/* Title Bar - Draggable */}
        <div 
          className="flex items-center justify-between px-4 pt-4 pb-2 bg-transparent z-50 shrink-0"
          style={{ WebkitAppRegion: 'drag' } as any}
        >
        <div className="flex items-center gap-4 w-1/3">
          <div className="flex items-center gap-2">
             <div className="w-5 h-5 rounded bg-gradient-to-br from-blue-400 to-indigo-600 shadow-[0_0_10px_rgba(99,102,241,0.5)]"></div>
             <span className="font-bold text-white text-base tracking-wide">Sigma</span>
          </div>
        </div>

        {/* Floating Pill Navbar */}
        <div className="flex items-center justify-center flex-1 shrink-0 whitespace-nowrap" style={{ WebkitAppRegion: 'no-drag' } as any}>
          <div className="flex items-center gap-1 bg-[#2c2d2d]/90 backdrop-blur-2xl border border-white/20 rounded-full p-1.5 shadow-2xl relative">
            {['Home', 'Overview', 'Portfolio', 'Paper', 'Backtest', 'Health'].map((tab) => (
              <button 
                key={tab}
                onClick={() => {
                  if (tab === 'Home') setIsLanding(true);
                  else { setActiveTab(tab); setIsLanding(false); }
                }}
                className={`px-4 py-1.5 text-[12px] font-medium rounded-full transition-all duration-300 uppercase ${(tab === 'Home' && isLanding) || (activeTab === tab && !isLanding) ? 'bg-white/10 text-white' : 'text-[#7a8a9c] hover:text-white hover:bg-white/5'}`}
              >
                {tab}
              </button>
            ))}
            
            <button 
              onClick={() => { setActiveTab('Settings'); setIsLanding(false); }}
              className="relative inline-flex items-center justify-center rounded-full p-[1px] bg-[#1f2a36] ml-1 transition-transform hover:scale-105"
            >
              <span className={`flex items-center justify-center h-full w-full rounded-full px-4 py-1.5 text-[12px] font-medium transition-colors uppercase ${activeTab === 'Settings' && !isLanding ? 'bg-[#3ddc97] text-black shadow-[0_0_15px_rgba(61,220,151,0.5)]' : 'bg-black text-[#e6edf3]'}`}>
                Settings
              </span>
            </button>
          </div>
        </div>

        <div className="flex items-center justify-end gap-3 w-1/3" style={{ WebkitAppRegion: 'no-drag' } as any}>
          
          
          <button onClick={handleMinimize} className="p-2 hover:bg-white/10 rounded-full transition-colors text-[#7a8a9c] hover:text-[#e6edf3]"><Minus size={16} /></button>
          <button onClick={handleMaximize} className="p-2 hover:bg-white/10 rounded-full transition-colors text-[#7a8a9c] hover:text-[#e6edf3]"><Maximize2 size={16} /></button>
          <button onClick={handleClose} className="p-2 hover:bg-[#ff4d6d]/20 hover:text-[#ff4d6d] rounded-full transition-colors text-[#7a8a9c]"><X size={16} /></button>
        </div>
      </div>

      {/* Ticker Tape */}
      {!isLanding && (
      <div className="w-full bg-[#2c2d2d]/90 backdrop-blur-md border-y border-white/20 flex items-center overflow-hidden shrink-0 h-7" style={{ WebkitAppRegion: 'no-drag' } as any}>
         <div className="flex whitespace-nowrap animate-marquee gap-12 font-mono text-[10px] uppercase font-bold px-4">
            <span className="text-[#e6edf3]">RELIANCE.NS <span className="text-[#3ddc97]">▲ 1.2%</span></span>
            <span className="text-[#e6edf3]">HDFCBANK.NS <span className="text-[#3ddc97]">▲ 0.8%</span></span>
            <span className="text-[#e6edf3]">INFY.NS <span className="text-[#ff4d6d]">▼ 1.1%</span></span>
            <span className="text-[#e6edf3]">TCS.NS <span className="text-[#ff4d6d]">▼ 0.4%</span></span>
            <span className="text-[#e6edf3]">ITC.NS <span className="text-[#3ddc97]">▲ 0.1%</span></span>
            <span className="text-[#e6edf3]">BRENT <span className="text-[#ff4d6d]">▲ 1.4%</span></span>
            <span className="text-[#e6edf3]">USDINR <span className="text-[#3ddc97]">▼ 0.1%</span></span>
            <span className="text-[#e6edf3]">NIFTY <span className="text-[#ff4d6d]">▼ 2.1%</span></span>
            {/* Duplicate for infinite effect */}
            <span className="text-[#e6edf3] ml-12">RELIANCE.NS <span className="text-[#3ddc97]">▲ 1.2%</span></span>
            <span className="text-[#e6edf3]">HDFCBANK.NS <span className="text-[#3ddc97]">▲ 0.8%</span></span>
            <span className="text-[#e6edf3]">INFY.NS <span className="text-[#ff4d6d]">▼ 1.1%</span></span>
         </div>
      </div>
      )}

      {/* Main Content Area */}
      <div className="flex-1 overflow-hidden relative z-10 flex flex-col">
         {isLanding ? (
            <div className="flex-1 flex flex-col items-center justify-center -mt-20 px-4">
               <div className="w-full max-w-3xl text-left mb-12">
                 <h2 className="text-6xl font-display font-bold tracking-widest uppercase text-white mb-4">What's</h2>
                 <h2 className="text-5xl font-display font-bold tracking-widest uppercase text-[#7a8a9c]">On your Mind?</h2>
               </div>
               
               <div className="w-full max-w-3xl bg-[#f5e6d3] border border-[#e3d1c1] rounded-2xl p-2 shadow-[0_0_40px_rgba(0,0,0,0.2)] flex items-center transition-all focus-within:border-[#121821]/30 focus-within:shadow-[0_0_30px_rgba(0,0,0,0.1)] group">
                     <div className="text-[#121821] ml-6 mr-4 opacity-50 group-focus-within:opacity-100 transition-opacity">
                        <Search size={24} />
                     </div>
                     <input 
                       type="text" 
                       className="flex-1 bg-transparent border-none outline-none text-xl text-[#121821] font-sans placeholder-[#121821]/40 py-5" 
                       placeholder="e.g., Cyclone in Bay of Bengal + FMCG stocks impact..."
                       onKeyDown={(e) => { if (e.key === 'Enter') setIsLanding(false); }}
                       autoFocus
                     />
                     <button 
                       onClick={() => setIsLanding(false)}
                       className="bg-[#2c2d2d] hover:bg-black text-[#f5e6d3] px-8 py-4 rounded-xl font-bold font-sans transition-all mx-1 uppercase text-sm tracking-wider flex items-center gap-2"
                     >
                       Execute <Play size={16} fill="currentColor" />
                     </button>
                   </div>
               
               <div className="mt-12 w-full max-w-3xl flex flex-wrap justify-start gap-3 text-xs font-mono text-[#7a8a9c]">
                  {['Analyze Reliance Q3 Earnings vs expectations', 'Macro impact of recent Fed rate cut on Indian IT', 'Run full portfolio stress test against 2008 analog', 'Scan for insider buying in mid-cap pharma'].map((prompt, i) => (
                    <button 
                      key={i} 
                      className="bg-white border border-white px-4 py-2.5 rounded-lg cursor-pointer hover:bg-gray-200 transition-all text-left text-black font-semibold shadow-sm" 
                      onClick={() => setIsLanding(false)}
                    >
                      {prompt}
                    </button>
                  ))}
               </div>
            </div>
         ) : (
           <div className="flex-1 p-4 overflow-auto custom-scrollbar relative">
           {activeTab === 'Overview' && (
         <div className="grid grid-cols-12 gap-3 auto-rows-min min-w-[1200px]">
            {/* ROW 2: Query Box + Quota */}
            <div className="col-span-10">
               <div className="bg-[#2c2d2d]/90 backdrop-blur-md border border-white/20 px-4 py-3 flex items-center gap-3 h-full">
                 <div className="text-[#ffb000] animate-pulse">▍</div>
                 <input type="text" className="flex-1 bg-transparent border-none outline-none text-[13px] placeholder-[#7a8a9c] text-[#e6edf3] font-mono" placeholder="Enter query..." defaultValue="Cyclone in Bay of Bengal + weak monsoon — what happens to my FMCG and agri stocks?" />
                 <div className="flex gap-2">
                    <span className="text-[10px] text-[#7a8a9c] bg-[#1f2a36] px-2 py-1 font-mono uppercase">LLM: Local</span>
                    <span className="text-[10px] text-[#7a8a9c] bg-[#1f2a36] px-2 py-1 font-mono uppercase">Lang: EN</span>
                 </div>
               </div>
            </div>
            <div className="col-span-2">
               <div className="bg-[#2c2d2d]/90 backdrop-blur-md border border-white/20 flex flex-col justify-center p-3 h-full">
                 <span className="text-[9px] font-mono text-[#7a8a9c] uppercase flex justify-between w-full mb-1.5">
                   <span>Quota</span> <span>2/10 Cloud</span>
                 </span>
                 <div className="flex gap-0.5 w-full">
                   <div className="h-1.5 flex-1 bg-[#3ddc97]"></div><div className="h-1.5 flex-1 bg-[#3ddc97]"></div><div className="h-1.5 flex-1 bg-[#ffb000]"></div><div className="h-1.5 flex-1 bg-[#1f2a36]"></div><div className="h-1.5 flex-1 bg-[#1f2a36]"></div><div className="h-1.5 flex-1 bg-[#1f2a36]"></div>
                 </div>
               </div>
            </div>

            {/* ROW 3: Agent Graph (7 cols) + Intent/Latency (5 cols) */}
            <div className="col-span-7 h-[300px]">
               <div className="h-full bg-[#2c2d2d]/90 backdrop-blur-md border border-white/20 flex flex-col relative">
                 <div className="px-3 py-1.5 border-b border-white/20 flex items-center justify-between bg-[#2c2d2d]/40">
                   <span className="font-bold text-[#e6edf3] font-display uppercase tracking-widest text-[10px]">▍AGENT GRAPH · run_cyclone</span>
                 </div>
                 <div className="flex-1 relative bg-transparent">
                   <ReactFlow nodes={initialNodes} edges={initialEdges} fitView proOptions={{ hideAttribution: true }} colorMode="dark">
                     <Background color="rgba(255,255,255,0.02)" gap={16} size={1} />
                   </ReactFlow>
                 </div>
               </div>
            </div>
            <div className="col-span-5 h-[300px] flex flex-col gap-3">
               <div className="flex-1 bg-[#2c2d2d]/90 backdrop-blur-md border border-white/20 flex flex-col">
                  <div className="px-3 py-1.5 border-b border-white/20 bg-[#2c2d2d]/40">
                    <span className="font-bold text-[#e6edf3] font-display uppercase tracking-widest text-[10px]">▍INTENT DECOMPOSITION</span>
                  </div>
                  <div className="p-3 overflow-auto font-mono text-[10px] text-[#7a8a9c] space-y-2">
                     <div className="flex"><span className="w-24">Intent</span> <span className="text-[#3ddc97]">event_impact</span></div>
                     <div className="flex"><span className="w-24">Event Type</span> <span className="text-[#e6edf3]">cyclone</span></div>
                     <div className="flex"><span className="w-24">Region</span> <span className="text-[#e6edf3]">Bay of Bengal</span></div>
                     <div className="flex"><span className="w-24">Tickers</span> <span className="bg-[#1f2a36] px-1 text-white mr-1">ITC.NS</span> <span className="bg-[#1f2a36] px-1 text-white">HDFCBANK.NS</span></div>
                     <div className="flex"><span className="w-24">Needs Tools</span> <span className="text-[#ffb000] mr-1">weather</span> <span className="text-[#ffb000] mr-1">agri</span> <span className="text-[#7a8a9c] line-through">macro</span></div>
                  </div>
               </div>
               <div className="h-24 bg-[#2c2d2d]/90 backdrop-blur-md border border-white/20 flex flex-col">
                  <div className="px-3 py-1.5 border-b border-white/20 bg-[#2c2d2d]/40">
                    <span className="font-bold text-[#e6edf3] font-display uppercase tracking-widest text-[10px]">▍LATENCY WATERFALL</span>
                  </div>
                  <div className="flex-1 p-3 flex flex-col justify-center gap-1.5 font-mono text-[9px] uppercase">
                     <div className="flex items-center gap-2"><span className="w-12 text-right text-[#7a8a9c]">Ingest</span> <div className="h-1 bg-[#1f2a36] w-64"><div className="h-full bg-[#3ddc97] w-[10%]"></div></div> <span className="text-[#e6edf3]">120ms</span></div>
                     <div className="flex items-center gap-2"><span className="w-12 text-right text-[#7a8a9c]">Agents</span> <div className="h-1 bg-[#1f2a36] w-64"><div className="h-full bg-[#ffb000] ml-[10%] w-[60%]"></div></div> <span className="text-[#e6edf3]">1450ms</span></div>
                     <div className="flex items-center gap-2"><span className="w-12 text-right text-[#7a8a9c]">Quant</span> <div className="h-1 bg-[#1f2a36] w-64"><div className="h-full bg-[#3ddc97] ml-[70%] w-[30%]"></div></div> <span className="text-[#e6edf3]">300ms</span></div>
                  </div>
               </div>
            </div>

            {/* ROW 4: Answer Panel (8 cols) + Secondary (4 cols) */}
            <div className="col-span-8">
               <div className="h-full bg-[#2c2d2d]/90 backdrop-blur-md border border-white/20 flex flex-col">
                  <div className="px-3 py-1.5 border-b border-white/20 flex justify-between items-center bg-[#2c2d2d]/40">
                    <span className="font-bold text-[#e6edf3] font-display uppercase tracking-widest text-[10px]">▍FINAL ANSWER</span>
                    <span className="text-[9px] text-[#121821] bg-[#3ddc97] font-bold px-2 py-0.5 uppercase font-mono">CONFIDENCE: HIGH</span>
                  </div>
                  <div className="p-5 text-[14px] leading-relaxed text-[#e6edf3] font-sans flex-1">
                    <p className="mb-4 text-[16px] font-medium border-l-2 border-[#ffb000] pl-3">
                      The Bay of Bengal cyclone is projected to severely disrupt Kharif crop yields, creating significant input inflation for FMCG players while simultaneously causing widespread port disruptions.
                    </p>
                    <p className="mb-4">
                      Agri yields in the Godavari basin show a <span className="text-[#ff4d6d] font-mono">-14%</span> deviation from the 10-year mean <span className="text-[10px] text-[#ffb000] bg-[#ffb000]/10 px-1 border border-[#ffb000]/20 font-mono cursor-pointer hover:bg-[#ffb000]/20">[ev_agri_092]</span>. This directly impacts raw material costs for <span className="bg-[#1f2a36] font-mono px-1">ITC.NS</span>. Simultaneously, wind speeds exceeding 120km/h at eastern ports have triggered force majeure declarations <span className="text-[10px] text-[#ffb000] bg-[#ffb000]/10 px-1 border border-[#ffb000]/20 font-mono cursor-pointer hover:bg-[#ffb000]/20">[ev_weather_441]</span>. 
                    </p>
                    <div className="bg-[#ff4d6d]/5 border border-[#ff4d6d]/20 p-3 mt-4">
                      <div className="text-[10px] font-mono text-[#ff4d6d] mb-1 font-bold uppercase">▍DEGRADED EVIDENCE</div>
                      <div className="text-[#7a8a9c] text-xs font-mono">Analog tool [ev_analog_110] failed to fetch 1999 Odisha cyclone data (timeout). Extrapolated using 2013 Phailin data.</div>
                    </div>
                  </div>
               </div>
            </div>
            <div className="col-span-4 flex flex-col gap-3">
               <div className="bg-[#2c2d2d]/90 backdrop-blur-md border border-white/20 p-3 flex gap-3">
                  <div className="flex-1 border-r border-white/20 pr-3">
                    <div className="text-[10px] font-mono text-[#ff4d6d] mb-1 uppercase font-bold">Red Team</div>
                    <div className="text-[#e6edf3] text-[10px] font-mono mb-2">ITC pricing power typically absorbs 60% of input inflation. Do not over-hedge.</div>
                    <span className="text-[9px] bg-[#ffb000]/20 text-[#ffb000] px-1 border border-[#ffb000]/30 font-mono">CAUTION</span>
                  </div>
                  <div className="flex-1 pl-1">
                    <div className="text-[10px] font-mono text-[#3ddc97] mb-1 uppercase font-bold">Validator</div>
                    <div className="text-[#e6edf3] text-[10px] font-mono mb-2">Math constraints satisfied. Ledger matches.</div>
                    <span className="text-[9px] bg-[#3ddc97]/20 text-[#3ddc97] px-1 border border-[#3ddc97]/30 font-mono">14/14 PASSED</span>
                  </div>
               </div>
               
               <div className="flex-1 bg-[#2c2d2d]/90 backdrop-blur-md border border-white/20 flex flex-col">
                  <div className="px-3 py-1.5 border-b border-white/20 bg-[#2c2d2d]/40">
                    <span className="font-bold text-[#e6edf3] font-display uppercase tracking-widest text-[10px]">▍HEDGE PROPOSAL</span>
                  </div>
                  <div className="flex-1 overflow-auto">
                    <table className="w-full text-left border-collapse text-[10px] font-mono">
                       <thead className="bg-[#1f2a36]/50 text-[#7a8a9c] uppercase">
                         <tr><th className="p-2 font-normal">Instrument</th><th className="p-2 font-normal">Side</th><th className="p-2 font-normal">Qty</th></tr>
                       </thead>
                       <tbody>
                         <tr className="border-b border-white/20 hover:bg-white/5">
                           <td className="p-2 text-white">ITC 400 PE</td>
                           <td className="p-2 text-[#3ddc97]">BUY</td>
                           <td className="p-2 text-[#e6edf3]">2,500</td>
                         </tr>
                         <tr className="hover:bg-white/5">
                           <td colSpan={3} className="p-2 text-center">
                             <button className="bg-[#ffb000] text-[#121821] w-full hover:bg-[#ffb000]/90 px-3 py-1.5 text-[10px] font-bold uppercase transition-colors">Approve → Paper</button>
                           </td>
                         </tr>
                       </tbody>
                    </table>
                  </div>
               </div>
            </div>

            {/* ROW 5: Sliders (4 cols) + Heatmap (4 cols) + Chart (4 cols) */}
            <div className="col-span-4 h-64 bg-[#2c2d2d]/90 backdrop-blur-md border border-white/20 flex flex-col">
               <div className="px-3 py-1.5 border-b border-white/20 bg-[#2c2d2d]/40">
                 <span className="font-bold text-[#e6edf3] font-display uppercase tracking-widest text-[10px]">▍WHAT-IF SLIDERS</span>
               </div>
               <div className="flex-1 p-4 flex flex-col justify-between font-mono">
                 <div>
                   <div className="flex justify-between text-[10px] text-[#7a8a9c] uppercase mb-1"><span>Monsoon Rain %</span> <span className="text-[#ff4d6d]">-20.0%</span></div>
                   <input type="range" className="w-full accent-[#ff4d6d]" min="-40" max="20" defaultValue="-20" />
                 </div>
                 <div>
                   <div className="flex justify-between text-[10px] text-[#7a8a9c] uppercase mb-1"><span>Crude Oil %</span> <span className="text-[#e6edf3]">+5.0%</span></div>
                   <input type="range" className="w-full accent-[#ffb000]" min="-30" max="30" defaultValue="5" />
                 </div>
                 <div className="bg-[#1f2a36]/50 p-2 mt-2 border border-white/20">
                   <div className="text-[9px] text-[#7a8a9c] uppercase mb-1">Simulated Impact</div>
                   <div className="text-xl text-[#ff4d6d] font-bold">-₹14,200 <span className="text-[10px] ml-1 font-normal">-0.9%</span></div>
                 </div>
               </div>
            </div>
            
            <div className="col-span-4 h-64 bg-[#2c2d2d]/90 backdrop-blur-md border border-white/20 flex flex-col">
               <div className="px-3 py-1.5 border-b border-white/20 bg-[#2c2d2d]/40">
                 <span className="font-bold text-[#e6edf3] font-display uppercase tracking-widest text-[10px]">▍SECTOR HEATMAP</span>
               </div>
               <div className="flex-1 p-3 font-mono">
                 <div className="grid grid-cols-5 text-[8px] text-[#7a8a9c] uppercase mb-1 text-center">
                   <div className="text-left">Sector</div><div>Weather</div><div>Agri</div><div>Crude</div><div>Rates</div>
                 </div>
                 <div className="space-y-1">
                   <div className="grid grid-cols-5 h-7 gap-1">
                     <div className="text-[9px] text-[#e6edf3] flex items-center">FMCG</div>
                     <div className="bg-[#ff4d6d]/60 flex items-center justify-center text-white text-[9px]">-0.6</div>
                     <div className="bg-[#ff4d6d]/80 flex items-center justify-center text-white text-[9px]">-0.8</div>
                     <div className="bg-[#3ddc97]/20 flex items-center justify-center text-white text-[9px]">0.2</div>
                     <div className="bg-[#3ddc97]/10 flex items-center justify-center text-white text-[9px]">0.1</div>
                   </div>
                   <div className="grid grid-cols-5 h-7 gap-1">
                     <div className="text-[9px] text-[#e6edf3] flex items-center">Agri</div>
                     <div className="bg-[#ff4d6d]/90 flex items-center justify-center text-white text-[9px]">-0.9</div>
                     <div className="bg-[#ff4d6d]/90 flex items-center justify-center text-white text-[9px]">-0.9</div>
                     <div className="bg-black/20 flex items-center justify-center text-[#7a8a9c] text-[9px]">0.0</div>
                     <div className="bg-[#3ddc97]/10 flex items-center justify-center text-white text-[9px]">0.1</div>
                   </div>
                   <div className="grid grid-cols-5 h-7 gap-1">
                     <div className="text-[9px] text-[#e6edf3] flex items-center">Energy</div>
                     <div className="bg-[#3ddc97]/30 flex items-center justify-center text-white text-[9px]">0.3</div>
                     <div className="bg-[#3ddc97]/10 flex items-center justify-center text-white text-[9px]">0.1</div>
                     <div className="bg-[#3ddc97]/80 flex items-center justify-center text-white text-[9px]">0.8</div>
                     <div className="bg-[#ff4d6d]/20 flex items-center justify-center text-white text-[9px]">-0.2</div>
                   </div>
                 </div>
               </div>
            </div>

            <div className="col-span-4 h-64 bg-[#2c2d2d]/90 backdrop-blur-md border border-white/20 flex flex-col">
               <div className="px-3 py-1.5 border-b border-white/20 bg-[#2c2d2d]/40">
                 <span className="font-bold text-[#e6edf3] font-display uppercase tracking-widest text-[10px]">▍PRICE CHART (ITC)</span>
               </div>
               <div className="flex-1 w-full h-full relative" style={{ minHeight: 0 }}>
                 <LightweightChartPlaceholder />
               </div>
            </div>

            {/* ROW 6: Portfolio (5 cols) + Alerts (7 cols) */}
            <div className="col-span-5 h-64 bg-[#2c2d2d]/90 backdrop-blur-md border border-white/20 flex flex-col">
               <div className="px-3 py-1.5 border-b border-white/20 bg-[#2c2d2d]/40 flex justify-between items-center">
                 <span className="font-bold text-[#e6edf3] font-display uppercase tracking-widest text-[10px]">▍PORTFOLIO OVERVIEW</span>
                 <span className="text-[#3ddc97] font-mono font-bold bg-[#3ddc97]/10 px-2 py-0.5 border border-[#3ddc97]/20 text-[10px]">₹1.50 Cr</span>
               </div>
               <div className="flex-1 overflow-auto p-3">
                 <table className="w-full text-left border-collapse text-[10px] font-mono">
                   <thead className="sticky top-0 bg-[#2c2d2d] text-[#7a8a9c] uppercase">
                     <tr>
                       <th className="font-normal p-2 border-b border-white/20">Ticker</th>
                       <th className="font-normal p-2 text-right border-b border-white/20">Weight</th>
                       <th className="font-normal p-2 text-right border-b border-white/20">Price</th>
                       <th className="font-normal p-2 text-right border-b border-white/20">24h Δ</th>
                     </tr>
                   </thead>
                   <tbody>
                     {[
                       { t: 'RELIANCE.NS', w: '12.5%', p: '2,850.40', c: '+1.24%', up: true },
                       { t: 'TCS.NS', w: '9.8%', p: '3,892.10', c: '-0.45%', up: false },
                       { t: 'HDFCBANK.NS', w: '15.2%', p: '1,432.00', c: '+0.89%', up: true },
                       { t: 'INFY.NS', w: '7.4%', p: '1,421.90', c: '-1.12%', up: false },
                       { t: 'ITC.NS', w: '5.1%', p: '412.30', c: '+0.10%', up: true },
                     ].map(row => (
                       <tr key={row.t} className="hover:bg-white/5 cursor-pointer transition-colors border-b border-white/20 last:border-0">
                         <td className="p-2 font-bold text-white">{row.t}</td>
                         <td className="p-2 text-right text-[#e6edf3]">{row.w}</td>
                         <td className="p-2 text-right text-[#e6edf3]">{row.p}</td>
                         <td className={`p-2 text-right font-bold ${row.up ? 'text-[#3ddc97]' : 'text-[#ff4d6d]'}`}>{row.c}</td>
                       </tr>
                     ))}
                   </tbody>
                 </table>
               </div>
            </div>

            <div className="col-span-7 h-64 bg-[#2c2d2d]/90 backdrop-blur-md border border-white/20 flex flex-col">
               <div className="px-3 py-1.5 border-b border-white/20 bg-[#2c2d2d]/40">
                 <span className="font-bold text-[#e6edf3] font-display uppercase tracking-widest text-[10px]">▍SYSTEM ALERTS</span>
               </div>
               <div className="flex-1 overflow-auto p-3">
                 <table className="w-full text-left border-collapse text-[10px] font-mono">
                    <thead className="text-[#7a8a9c] uppercase bg-[#1f2a36]/50">
                      <tr>
                        <th className="p-2 font-normal">Time</th>
                        <th className="p-2 font-normal">Sev</th>
                        <th className="p-2 font-normal">Asset</th>
                        <th className="p-2 font-normal">Trigger</th>
                        <th className="p-2 font-normal text-right">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr className="border-b border-white/20 hover:bg-white/5">
                        <td className="p-2 text-[#7a8a9c]">12:30</td>
                        <td className="p-2"><span className="bg-[#ff4d6d]/10 text-[#ff4d6d] px-1 border border-[#ff4d6d]/20 uppercase">High</span></td>
                        <td className="p-2 text-[#e6edf3] font-bold">RELIANCE</td>
                        <td className="p-2 text-[#e6edf3]">Cyclone warning mapped to Gujarat refinery assets.</td>
                        <td className="p-2 text-right"><button className="uppercase bg-[#1f2a36] text-white px-2 py-1 hover:bg-[#1f2a36]/80">Ack</button></td>
                      </tr>
                      <tr className="border-b border-white/20 hover:bg-white/5">
                        <td className="p-2 text-[#7a8a9c]">11:15</td>
                        <td className="p-2"><span className="bg-[#ffb000]/10 text-[#ffb000] px-1 border border-[#ffb000]/20 uppercase">Med</span></td>
                        <td className="p-2 text-[#e6edf3] font-bold">HDFCBANK</td>
                        <td className="p-2 text-[#e6edf3]">Volume breakout > 3x 20DMA detected on options chain.</td>
                        <td className="p-2 text-right text-[#7a8a9c]">Dismissed</td>
                      </tr>
                    </tbody>
                 </table>
               </div>
            </div>

         </div>
         )}
         {activeTab === 'Portfolio' && <PortfolioView />}
         {activeTab === 'Paper' && <PaperView />}
         {activeTab === 'Backtest' && <BacktestView />}
         {activeTab === 'Health' && <HealthView />}
           {activeTab === 'Settings' && <SettingsView />}
           </div>
         )}
      </div>
      </div>
    </BrailleTerrainBackground>
  );
}
// UTILS
// -----------------------------------------------------------------------------

function Sparkline({ color, reverse }: { color: string, reverse?: boolean }) {
  const data = Array.from({length: 20}, () => Math.random() * 10 + (reverse ? 20 : 10));
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data.map((v, i) => ({i, v}))}>
        <Line type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

function LightweightChartPlaceholder() {
  const chartContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;
    
    const chart = createChart(chartContainerRef.current, {
      layout: { background: { color: 'transparent' }, textColor: '#666' },
      grid: { vertLines: { color: '#222' }, horzLines: { color: '#222' } },
      timeScale: { borderColor: '#333' },
      rightPriceScale: { borderColor: '#333' },
      crosshair: { mode: 1 },
    });

    const candlestickSeries = chart.addCandlestickSeries({
      upColor: '#2dd4bf', downColor: '#ef4444', borderVisible: false,
      wickUpColor: '#2dd4bf', wickDownColor: '#ef4444'
    });

    // Dummy data
    const data = [];
    let time = Math.floor(Date.now() / 1000) - 100 * 86400;
    let price = 2500;
    for(let i=0; i<100; i++) {
      const open = price + (Math.random() * 40 - 20);
      const close = open + (Math.random() * 60 - 30);
      const high = Math.max(open, close) + Math.random() * 20;
      const low = Math.min(open, close) - Math.random() * 20;
      data.push({ time: time as any, open, high, low, close });
      price = close;
      time += 86400;
    }
    candlestickSeries.setData(data);

    // Weather overlay simulation (background color band)
    const lineSeries = chart.addLineSeries({ color: '#f59e0b', lineWidth: 1, lineStyle: 2 });
    const lineData = data.map(d => ({ time: d.time, value: 2700 + Math.random()*100 }));
    lineSeries.setData(lineData);

    chart.timeScale().fitContent();

    const handleResize = () => {
      chart.applyOptions({ width: chartContainerRef.current?.clientWidth });
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, []);

  return <div ref={chartContainerRef} className="w-full h-full" />;
}

function PortfolioView() {
  return (
    <div className="grid grid-cols-12 gap-0 border border-white/20 bg-[#2c2d2d]/90 backdrop-blur-md h-full">
      <div className="col-span-12 p-4 border-b border-white/20">
        <h2 className="text-[#3ddc97] text-3xl font-mono">₹11.50 Cr <span className="text-[#7a8a9c] text-sm ml-2">Total AUM</span></h2>
      </div>
      <div className="col-span-12 overflow-auto p-0">
         <table className="w-full text-left text-xs font-mono">
           <thead className="bg-[#2c2d2d] text-[#7a8a9c] uppercase border-b border-white/20">
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
               <tr key={r.a} className="border-b border-white/20 hover:bg-white/5 transition-colors">
                 <td className="p-3 text-white font-bold">{r.a}</td>
                 <td className="p-3 text-[#e6edf3] text-right">{r.h}</td>
                 <td className="p-3 text-[#e6edf3] text-right">{r.p}</td>
                 <td className="p-3 text-[#7a8a9c] text-right">{r.c}</td>
                 <td className={`p-3 text-right font-bold ${r.up ? "text-[#3ddc97]" : "text-[#ff4d6d]"}`}>{r.pnl}</td>
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
    <div className="grid grid-cols-1 gap-0 border border-white/20 bg-[#2c2d2d]/90 backdrop-blur-md h-full">
      <div className="p-4 border-b border-white/20">
        <h2 className="text-[#e6edf3] text-lg font-mono">Recent Executions <span className="text-[#ffb000] text-[10px] ml-2 px-1 border border-[#ffb000]/30 bg-[#ffb000]/10">SIMULATED</span></h2>
      </div>
      <div className="overflow-auto p-0">
         <table className="w-full text-left text-xs font-mono">
           <thead className="bg-[#2c2d2d] text-[#7a8a9c] uppercase border-b border-white/20">
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
               <tr key={i} className="border-b border-white/20 hover:bg-white/5 transition-colors">
                 <td className="p-3 text-[#7a8a9c]">{r.t}</td>
                 <td className={`p-3 font-bold ${r.a === "BUY" ? "text-[#3ddc97]" : "text-[#ff4d6d]"}`}>{r.a}</td>
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
      <div className="border border-white/20 bg-[#2c2d2d]/90 backdrop-blur-md p-4 flex flex-col items-center justify-center h-48">
         <div className="text-[#7a8a9c] uppercase text-xs tracking-widest mb-2">Sharpe Ratio</div>
         <div className="text-4xl text-[#3ddc97] font-mono">2.14</div>
         <div className="text-[10px] text-[#e6edf3] mt-2">Benchmark: 1.05</div>
      </div>
      <div className="border border-white/20 bg-[#2c2d2d]/90 backdrop-blur-md p-4 flex flex-col items-center justify-center h-48">
         <div className="text-[#7a8a9c] uppercase text-xs tracking-widest mb-2">Max Drawdown</div>
         <div className="text-4xl text-[#ff4d6d] font-mono">-12.4%</div>
         <div className="text-[10px] text-[#e6edf3] mt-2">March 2024 Stress Test</div>
      </div>
      <div className="col-span-2 border border-white/20 bg-[#2c2d2d]/90 backdrop-blur-md p-4 flex-1">
         <div className="text-[#e6edf3] uppercase text-xs tracking-widest mb-4">Cumulative Returns (1Y)</div>
         <div className="h-40 flex items-end gap-1">
           {Array.from({length: 40}).map((_, i) => (
             <div key={i} className="flex-1 bg-[#3ddc97]/40 hover:bg-[#3ddc97] transition-colors" style={{ height: `${Math.random() * 80 + 20}%` }}></div>
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
        <div key={node.n} className={`border p-3 flex flex-col gap-2 bg-[#2c2d2d]/90 backdrop-blur-md ${node.err ? "border-[#ff4d6d]" : node.warn ? "border-[#ffb000]" : "border-white/20"}`}>
           <div className="text-[#e6edf3] font-mono text-xs font-bold">{node.n}</div>
           <div className="flex justify-between items-center">
             <span className={`text-[10px] px-1 py-0.5 font-mono ${node.err ? "bg-[#ff4d6d]/20 text-[#ff4d6d]" : node.warn ? "bg-[#ffb000]/20 text-[#ffb000]" : "bg-[#3ddc97]/20 text-[#3ddc97]"}`}>
               {node.s}
             </span>
             <span className="text-[#7a8a9c] text-[10px] font-mono">{node.ms}</span>
           </div>
        </div>
      ))}
    </div>
  );
}




function SettingsView() {
  const [llmMode, setLlmMode] = useState('auto');
  const [lang, setLang] = useState('en');
  const [chaos, setChaos] = useState({
    weather_down: false,
    force_rate_limit: false,
    agri_raster_missing: false,
    vector_down: false
  });
  const [slowNetwork, setSlowNetwork] = useState(0);
  const [asOf, setAsOf] = useState('2026-10-03');

  return (
    <div className="flex flex-col h-full bg-[#2c2d2d]/90 backdrop-blur-md border border-white/20 overflow-y-auto custom-scrollbar p-10 font-sans shadow-inner">
      <div className="max-w-5xl w-full mx-auto">
        <h2 className="text-3xl font-display font-bold uppercase tracking-widest text-white mb-2">Control Center</h2>
        <p className="text-[#7a8a9c] mb-12 text-sm">Configure global agent states, mock API chaos, and system preferences.</p>
        
        <div className="grid grid-cols-2 gap-16">
          <div className="space-y-12">
            {/* LLM Mode */}
            <div>
              <h3 className="text-xs font-bold text-[#7a8a9c] uppercase tracking-widest mb-4 border-b border-white/20 pb-3">LLM Routing Mode</h3>
              <div className="flex bg-[#2c2d2d]/40 border border-white/20 rounded-lg p-1 w-fit shadow-inner">
                {['local', 'boost', 'auto'].map(mode => (
                  <button 
                    key={mode}
                    onClick={() => setLlmMode(mode)}
                    className={`px-6 py-2.5 rounded-md text-xs font-bold uppercase transition-all duration-300 ${llmMode === mode ? 'bg-[#3ddc97] text-black shadow-lg scale-95' : 'text-[#7a8a9c] hover:text-white hover:bg-white/5'}`}
                  >
                    {mode}
                  </button>
                ))}
              </div>
              <p className="text-[#7a8a9c] text-[11px] mt-4 flex items-center gap-2 bg-black/20 p-2 rounded-md">
                <Activity size={14} className="text-[#3ddc97]" />
                {llmMode === 'boost' ? "Boost mode uses Groq LPU (Llama-3-70B) for ultra-low latency." : llmMode === 'auto' ? "Auto routing: Fast queries to local, complex to Groq fallback." : "Local mode strictly uses 4-bit quantized local models."}
              </p>
            </div>

            {/* Language */}
            <div>
              <h3 className="text-xs font-bold text-[#7a8a9c] uppercase tracking-widest mb-4 border-b border-white/20 pb-3">Response Language</h3>
              <div className="flex gap-3">
                {[
                  {id: 'en', label: 'English'},
                  {id: 'hi', label: 'हिंदी (Hindi)'},
                  {id: 'hinglish', label: 'Hinglish'}
                ].map(l => (
                  <button 
                    key={l.id}
                    onClick={() => setLang(l.id)}
                    className={`px-5 py-2.5 border rounded-lg text-sm transition-all font-medium ${lang === l.id ? 'border-[#3ddc97] bg-[#3ddc97]/10 text-[#3ddc97] shadow-[0_0_15px_rgba(61,220,151,0.2)]' : 'border-white/20 bg-[#2c2d2d]/40 text-[#7a8a9c] hover:border-[#7a8a9c]'}`}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Time Machine */}
            <div>
              <h3 className="text-xs font-bold text-[#7a8a9c] uppercase tracking-widest mb-4 border-b border-white/20 pb-3">Time-Machine (As-Of Date)</h3>
              <div className="relative w-fit">
                <input 
                  type="date" 
                  value={asOf}
                  onChange={(e) => setAsOf(e.target.value)}
                  className="bg-[#2c2d2d]/40 border border-white/20 rounded-lg px-5 py-3 text-[#e6edf3] outline-none focus:border-[#3ddc97] transition-all font-mono text-sm"
                  style={{ colorScheme: 'dark' }}
                />
              </div>
              <p className="text-[#7a8a9c] text-[11px] mt-3">Trick the agent orchestration into thinking it's a specific date in the past.</p>
            </div>
          </div>

          {/* Chaos Engineering */}
          <div className="space-y-8 bg-[#2c2d2d]/40 p-8 rounded-2xl border border-red-900/40 relative overflow-hidden shadow-2xl">
            <div className="absolute top-0 right-0 bg-red-900/80 text-red-100 text-[9px] font-bold px-4 py-1.5 uppercase tracking-[0.2em] rounded-bl-xl shadow-lg flex items-center gap-2">
              <AlertTriangle size={10} /> Danger Zone
            </div>
            
            <div>
              <h3 className="text-sm font-bold text-red-400 uppercase tracking-widest mb-1 flex items-center gap-2">
                Chaos Toggles
              </h3>
              <p className="text-[#7a8a9c] text-xs mb-8">Intentionally fail subsystems to demo resilience.</p>
            </div>
            
            <div className="space-y-6">
              {Object.entries({
                weather_down: "Kill IMD Weather Service (503)",
                force_rate_limit: "Force LLM 429 Rate Limit",
                agri_raster_missing: "Corrupt ISRO Satellite Raster Data",
                vector_down: "Take down Pinecone Vector DB"
              }).map(([key, label]) => (
                <label key={key} className="flex items-center justify-between cursor-pointer group">
                  <span className="text-sm text-[#e6edf3] group-hover:text-white transition-colors">{label}</span>
                  <div className={`w-12 h-6 rounded-full p-1 transition-colors relative shadow-inner ${chaos[key as keyof typeof chaos] ? 'bg-red-500 shadow-[0_0_15px_rgba(239,68,68,0.4)]' : 'bg-[#1f2a36]'}`}>
                    <div className={`w-4 h-4 bg-white rounded-full transition-all duration-300 shadow-md ${chaos[key as keyof typeof chaos] ? 'translate-x-6' : 'translate-x-0'}`}></div>
                  </div>
                  <input 
                    type="checkbox" 
                    className="hidden" 
                    checked={chaos[key as keyof typeof chaos]} 
                    onChange={() => setChaos(prev => ({...prev, [key]: !prev[key as keyof typeof chaos]}))}
                  />
                </label>
              ))}
            </div>

            <div className="pt-8 mt-8 border-t border-red-900/30">
               <div className="flex justify-between items-center mb-4">
                 <span className="text-sm text-[#e6edf3]">Simulate Network Latency</span>
                 <span className="text-xs font-mono text-red-400 bg-red-900/30 px-2 py-1 rounded">+{slowNetwork}ms</span>
               </div>
               <input 
                 type="range" 
                 min="0" max="5000" step="100" 
                 value={slowNetwork} 
                 onChange={(e) => setSlowNetwork(Number(e.target.value))}
                 className="w-full accent-red-500 h-1.5 bg-[#1f2a36] rounded-full appearance-none outline-none cursor-pointer"
               />
               <div className="flex justify-between mt-2 text-[10px] font-mono text-[#7a8a9c]">
                 <span>0ms</span>
                 <span>5000ms</span>
               </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
