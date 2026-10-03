import re

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'r', encoding='utf-8') as f:
    content = f.read()

new_content = re.sub(
    r'<div className="w-full max-w-3xl bg-\[#121821\]/60.*?</button>\s*</div>',
    '''<div className="w-full max-w-3xl bg-[#f5e6d3] border border-[#e3d1c1] rounded-2xl p-2 shadow-[0_0_40px_rgba(0,0,0,0.2)] flex items-center transition-all focus-within:border-[#121821]/30 focus-within:shadow-[0_0_30px_rgba(0,0,0,0.1)] group">
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
                       className="bg-[#121821] hover:bg-black text-[#f5e6d3] px-8 py-4 rounded-xl font-bold font-sans transition-all mx-1 uppercase text-sm tracking-wider flex items-center gap-2"
                     >
                       Execute <Play size={16} fill="currentColor" />
                     </button>
                   </div>''',
    content,
    flags=re.DOTALL
)

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'w', encoding='utf-8') as f:
    f.write(new_content)
