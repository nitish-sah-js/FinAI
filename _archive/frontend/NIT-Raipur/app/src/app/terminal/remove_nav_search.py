import re

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(
    r'<div className="relative flex items-center px-3 py-1\.5 bg-\[#2c2d2d\]/40 border border-white/20 rounded-full shadow-inner">\s*<Search size=\{14\} className="text-\[#7a8a9c\] mr-2" />\s*<input\s*type="text"\s*placeholder="Search\.\.\."\s*className="bg-transparent border-none outline-none text-sm w-28 placeholder-\[#7a8a9c\] text-\[#e6edf3\]"\s*/>\s*</div>',
    '',
    content
)

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'w', encoding='utf-8') as f:
    f.write(content)
