import re

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'r', encoding='utf-8') as f:
    content = f.read()

new_content = re.sub(
    r'<div className="mt-12 flex flex-wrap justify-center gap-3 text-xs font-mono text-\[#7a8a9c\] max-w-4xl">',
    '<div className="mt-12 w-full max-w-3xl flex flex-wrap justify-start gap-3 text-xs font-mono text-[#7a8a9c]">',
    content
)

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\terminal\page.tsx', 'w', encoding='utf-8') as f:
    f.write(new_content)
