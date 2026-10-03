import re

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update the Settings button
content = re.sub(
    r'<button className="relative inline-flex items-center justify-center rounded-full p-\[1px\] bg-\[#1f2a36\] ml-1 transition-transform hover:scale-105">',
    '''<button 
              onClick={() => { setActiveTab('Settings'); setIsLanding(false); }}
              className="relative inline-flex items-center justify-center rounded-full p-[1px] bg-[#1f2a36] ml-1 transition-transform hover:scale-105"
            >''',
    content
)

content = re.sub(
    r'<span className="flex items-center justify-center h-full w-full rounded-full px-4 py-1\.5 text-\[12px\] font-medium transition-colors bg-black text-\[#e6edf3\] uppercase">\s*Settings\s*</span>',
    '''<span className={`flex items-center justify-center h-full w-full rounded-full px-4 py-1.5 text-[12px] font-medium transition-colors uppercase ${activeTab === 'Settings' && !isLanding ? 'bg-[#3ddc97] text-black shadow-[0_0_15px_rgba(61,220,151,0.5)]' : 'bg-black text-[#e6edf3]'}`}>
                Settings
              </span>''',
    content
)

# 2. Insert {activeTab === 'Settings' && <SettingsView />}
content = re.sub(
    r"\{activeTab === 'Health' && <HealthView />\}",
    "{activeTab === 'Health' && <HealthView />}\n           {activeTab === 'Settings' && <SettingsView />}",
    content
)

# 3. Append SettingsView
settings_view = '''
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
    <div className="flex flex-col h-full bg-[#121821]/80 backdrop-blur-md border border-[#1f2a36] overflow-y-auto custom-scrollbar p-10 font-sans shadow-inner">
      <div className="max-w-5xl w-full mx-auto">
        <h2 className="text-3xl font-display font-bold uppercase tracking-widest text-white mb-2">Control Center</h2>
        <p className="text-[#7a8a9c] mb-12 text-sm">Configure global agent states, mock API chaos, and system preferences.</p>
        
        <div className="grid grid-cols-2 gap-16">
          <div className="space-y-12">
            {/* LLM Mode */}
            <div>
              <h3 className="text-xs font-bold text-[#7a8a9c] uppercase tracking-widest mb-4 border-b border-[#1f2a36] pb-3">LLM Routing Mode</h3>
              <div className="flex bg-black/40 border border-[#1f2a36] rounded-lg p-1 w-fit shadow-inner">
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
              <p className="text-[#7a8a9c] text-[11px] mt-4 flex items-center gap-2 bg-[#1f2a36]/30 p-2 rounded-md">
                <Activity size={14} className="text-[#3ddc97]" />
                {llmMode === 'boost' ? "Boost mode uses Groq LPU (Llama-3-70B) for ultra-low latency." : llmMode === 'auto' ? "Auto routing: Fast queries to local, complex to Groq fallback." : "Local mode strictly uses 4-bit quantized local models."}
              </p>
            </div>

            {/* Language */}
            <div>
              <h3 className="text-xs font-bold text-[#7a8a9c] uppercase tracking-widest mb-4 border-b border-[#1f2a36] pb-3">Response Language</h3>
              <div className="flex gap-3">
                {[
                  {id: 'en', label: 'English'},
                  {id: 'hi', label: 'हिंदी (Hindi)'},
                  {id: 'hinglish', label: 'Hinglish'}
                ].map(l => (
                  <button 
                    key={l.id}
                    onClick={() => setLang(l.id)}
                    className={`px-5 py-2.5 border rounded-lg text-sm transition-all font-medium ${lang === l.id ? 'border-[#3ddc97] bg-[#3ddc97]/10 text-[#3ddc97] shadow-[0_0_15px_rgba(61,220,151,0.2)]' : 'border-[#1f2a36] bg-black/40 text-[#7a8a9c] hover:border-[#7a8a9c]'}`}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Time Machine */}
            <div>
              <h3 className="text-xs font-bold text-[#7a8a9c] uppercase tracking-widest mb-4 border-b border-[#1f2a36] pb-3">Time-Machine (As-Of Date)</h3>
              <div className="relative w-fit">
                <input 
                  type="date" 
                  value={asOf}
                  onChange={(e) => setAsOf(e.target.value)}
                  className="bg-black/40 border border-[#1f2a36] rounded-lg px-5 py-3 text-[#e6edf3] outline-none focus:border-[#3ddc97] transition-all font-mono text-sm"
                  style={{ colorScheme: 'dark' }}
                />
              </div>
              <p className="text-[#7a8a9c] text-[11px] mt-3">Trick the agent orchestration into thinking it's a specific date in the past.</p>
            </div>
          </div>

          {/* Chaos Engineering */}
          <div className="space-y-8 bg-black/40 p-8 rounded-2xl border border-red-900/40 relative overflow-hidden shadow-2xl">
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
'''

content = content + "\n\n" + settings_view

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'w', encoding='utf-8') as f:
    f.write(content)
