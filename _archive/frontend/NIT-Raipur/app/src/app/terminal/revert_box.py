import re

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'r', encoding='utf-8') as f:
    content = f.read()

new_content = re.sub(
    r'<div className="w-full max-w-3xl bg-\[#121821\]/80.*?Execute <Play size=\{14\} fill="currentColor" />\s*</button>\s*</div>\s*</div>',
    '''<div className="w-full max-w-3xl bg-[#121821]/60 backdrop-blur-2xl border border-[#1f2a36] rounded-2xl p-2 shadow-[0_0_40px_rgba(0,0,0,0.5)] flex items-center transition-all focus-within:border-[#3ddc97]/50 focus-within:shadow-[0_0_30px_rgba(61,220,151,0.15)] group">
                   <div className="text-[#3ddc97] ml-6 mr-4 opacity-50 group-focus-within:opacity-100 transition-opacity">
                      <Search size={24} />
                   </div>
                   <input 
                     type="text" 
                     className="flex-1 bg-transparent border-none outline-none text-xl text-[#e6edf3] font-sans placeholder-[#7a8a9c]/50 py-5" 
                     placeholder="e.g., Cyclone in Bay of Bengal + FMCG stocks impact..."
                     onKeyDown={(e) => { if (e.key === 'Enter') setIsLanding(false); }}
                     autoFocus
                   />
                   <button 
                     onClick={() => setIsLanding(false)}
                     className="bg-white hover:bg-[#3ddc97] text-black px-8 py-4 rounded-xl font-bold font-sans transition-all mx-1 uppercase text-sm tracking-wider flex items-center gap-2 group-focus-within:bg-[#3ddc97]"
                   >
                     Execute <Play size={16} fill="currentColor" />
                   </button>
                 </div>''',
    content,
    flags=re.DOTALL
)

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'w', encoding='utf-8') as f:
    f.write(new_content)
