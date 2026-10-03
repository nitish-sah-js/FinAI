import re

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'r', encoding='utf-8') as f:
    content = f.read()

new_content = re.sub(
    r'className="bg-black/40 border border-\[#1f2a36\] px-4 py-2\.5 rounded-lg cursor-pointer hover:border-\[#3ddc97\]/50 hover:text-\[#e6edf3\] hover:bg-\[#3ddc97\]/10 transition-all text-left"',
    'className="bg-black/40 border border-[#1f2a36] px-4 py-2.5 rounded-lg cursor-pointer hover:border-white/50 hover:text-white hover:bg-white/10 transition-all text-left text-white/70"',
    content
)

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'w', encoding='utf-8') as f:
    f.write(new_content)
