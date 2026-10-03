import re

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\globals.css', 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(
    r"@import url\('https://fonts.googleapis.com/css2\?family=Inter\+Tight:wght@400;500;600;700&family=JetBrains\+Mono:wght@400;500;700&display=swap'\);",
    "@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap');\n@import url('https://api.fontshare.com/v2/css?f[]=satoshi@900,700,500,300,400&display=swap');",
    content
)

content = re.sub(
    r"font-family: 'Inter Tight', sans-serif;",
    "font-family: 'Satoshi', sans-serif;",
    content
)

with open(r'd:\Some_stuffs\Codeutsava X.0\NIT-Raipur\app\src\app\globals.css', 'w', encoding='utf-8') as f:
    f.write(content)
