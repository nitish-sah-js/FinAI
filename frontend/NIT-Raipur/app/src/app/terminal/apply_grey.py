import re

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'r', encoding='utf-8') as f:
    content = f.read()

replacements = {
    r'bg-\[#121821\]/80': 'bg-[#595959]/90',
    r'bg-\[#121821\]/90': 'bg-[#595959]/90',
    r'bg-\[#121821\]': 'bg-[#595959]',
    r'bg-\[#0b0f14\]': 'bg-[#595959]',
    r'bg-black/40': 'bg-[#595959]/40',
    r'bg-black/30': 'bg-[#595959]/40',
    r'bg-black/90': 'bg-[#595959]/90',
    r'bg-\[#1f2a36\]/30': 'bg-black/20',
    r'border-\[#1f2a36\]': 'border-white/20'
}

for old, new in replacements.items():
    content = re.sub(old, new, content)

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'w', encoding='utf-8') as f:
    f.write(content)
