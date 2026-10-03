import re

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'r', encoding='utf-8') as f:
    content = f.read()

new_content = re.sub(
    r'<div className="w-full max-w-3xl bg-\[#121821\]/60.*?Execute <Play size=\{16\} fill="currentColor" />\s*</button>\s*</div>',
    '''<div className="w-full max-w-3xl bg-[#121821]/80 backdrop-blur-2xl border border-[#1f2a36] rounded-3xl p-4 shadow-[0_0_40px_rgba(0,0,0,0.5)] flex flex-col transition-all focus-within:border-[#3ddc97]/50 focus-within:shadow-[0_0_30px_rgba(61,220,151,0.15)] group">
                 <textarea 
                   className="w-full bg-transparent border-none outline-none text-xl text-[#e6edf3] font-sans placeholder-[#7a8a9c]/50 resize-none h-24" 
                   placeholder="How can I help you today?"
                   onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); setIsLanding(false); } }}
                   autoFocus
                 />
                 <div className="flex justify-between items-center mt-2">
                   <div className="flex items-center">
                     <button className="p-2 text-[#7a8a9c] hover:text-[#e6edf3] rounded-full hover:bg-white/5 transition-colors">
                       <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                     </button>
                   </div>
                   <button 
                     onClick={() => setIsLanding(false)}
                     className="bg-white hover:bg-[#3ddc97] text-black px-6 py-2.5 rounded-xl font-bold font-sans transition-all flex items-center gap-2 group-focus-within:bg-[#3ddc97]"
                   >
                     Execute <Play size={14} fill="currentColor" />
                   </button>
                 </div>
               </div>''',
    content,
    flags=re.DOTALL
)

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'w', encoding='utf-8') as f:
    f.write(new_content)
