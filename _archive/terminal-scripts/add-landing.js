
const fs = require("fs");
const file = "d:/Some_stuffs/Codeutsava X.0/NIT-Raipur/app/src/app/terminal/page.tsx";
let content = fs.readFileSync(file, "utf8");

// 1. Add isLanding state
content = content.replace(
  "const [activeTab, setActiveTab] = useState('Overview');",
  "const [isLanding, setIsLanding] = useState(true);\n  const [activeTab, setActiveTab] = useState('Overview');"
);

// 2. Hide Pill Navbar and Ticker Tape when isLanding
content = content.replace(
  "{/* Floating Pill Navbar */}",
  "{/* Floating Pill Navbar */}\n        {!isLanding ? ("
);

content = content.replace(
  "</button>\n          </div>\n        </div>\n\n        <div className=\"flex items-center justify-end gap-3 w-1/3\"",
  "</button>\n          </div>\n        </div>\n        ) : <div className=\"flex-1\"></div>}\n\n        <div className=\"flex items-center justify-end gap-3 w-1/3\""
);

content = content.replace(
  "{/* Ticker Tape */}\n      <div className=\"w-full bg-[#121821]/80",
  "{/* Ticker Tape */}\n      {!isLanding && (\n      <div className=\"w-full bg-[#121821]/80"
);

content = content.replace(
  "</span>\n         </div>\n      </div>\n\n      {/* Main Content Area */}",
  "</span>\n         </div>\n      </div>\n      )}\n\n      {/* Main Content Area */}"
);

// 3. Add Landing View inside Main Content Area
const landingJSX = `      <div className="flex-1 overflow-hidden relative z-10 flex flex-col">
         {isLanding ? (
            <div className="flex-1 flex flex-col items-center justify-center -mt-20 px-4">
               <div className="mb-8 flex items-center gap-3">
                 <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-400 to-indigo-600 shadow-[0_0_20px_rgba(99,102,241,0.6)] animate-pulse"></div>
                 <h1 className="text-4xl font-display text-white tracking-widest uppercase">Sigma</h1>
               </div>
               
               <h2 className="text-xl font-mono text-[#7a8a9c] mb-10 tracking-widest uppercase">What do you want to know?</h2>
               
               <div className="w-full max-w-3xl bg-[#121821]/60 backdrop-blur-2xl border border-[#1f2a36] rounded-2xl p-2 shadow-[0_0_40px_rgba(0,0,0,0.5)] flex items-center transition-all focus-within:border-[#3ddc97]/50 focus-within:shadow-[0_0_30px_rgba(61,220,151,0.15)] group">
                 <div className="text-[#3ddc97] ml-6 mr-4 opacity-50 group-focus-within:opacity-100 transition-opacity">
                    <Search size={24} />
                 </div>
                 <input 
                   type="text" 
                   className="flex-1 bg-transparent border-none outline-none text-xl text-[#e6edf3] font-sans placeholder-[#7a8a9c]/50 py-5" 
                   placeholder="e.g., Cyclone in Bay of Bengal + FMCG stocks impact..."
                   onKeyDown={(e) => { if (e.key === "Enter") setIsLanding(false); }}
                   autoFocus
                 />
                 <button 
                   onClick={() => setIsLanding(false)}
                   className="bg-white hover:bg-[#3ddc97] text-black px-8 py-4 rounded-xl font-bold font-sans transition-all mx-1 uppercase text-sm tracking-wider flex items-center gap-2 group-focus-within:bg-[#3ddc97]"
                 >
                   Execute <Play size={16} fill="currentColor" />
                 </button>
               </div>
               
               <div className="mt-12 flex flex-wrap justify-center gap-3 text-xs font-mono text-[#7a8a9c] max-w-4xl">
                  {["Analyze Reliance Q3 Earnings vs expectations", "Macro impact of recent Fed rate cut on Indian IT", "Run full portfolio stress test against 2008 analog", "Scan for insider buying in mid-cap pharma"].map((prompt, i) => (
                    <button 
                      key={i} 
                      className="bg-black/40 border border-[#1f2a36] px-4 py-2.5 rounded-lg cursor-pointer hover:border-[#3ddc97]/50 hover:text-[#e6edf3] hover:bg-[#3ddc97]/10 transition-all text-left" 
                      onClick={() => setIsLanding(false)}
                    >
                      {prompt}
                    </button>
                  ))}
               </div>
            </div>
         ) : (
           <div className="flex-1 p-4 overflow-auto custom-scrollbar relative">
`;

content = content.replace(
  "{/* Main Content Area */}\n      <div className=\"flex-1 p-4 overflow-auto relative z-10 custom-scrollbar\">\n",
  "{/* Main Content Area */}\n" + landingJSX + "\n"
);

// Close the wrapper
content = content.replace(
  "         {activeTab === 'Health' && <HealthView />}\n      </div>\n      </div>",
  "         {activeTab === 'Health' && <HealthView />}\n           </div>\n         )}\n      </div>\n      </div>"
);

fs.writeFileSync(file, content, "utf8");
console.log("Landing page added");

