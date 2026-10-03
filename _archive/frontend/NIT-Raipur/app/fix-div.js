
const fs = require("fs");
const file = "d:/Some_stuffs/Codeutsava X.0/NIT-Raipur/app/src/app/terminal/page.tsx";
let content = fs.readFileSync(file, "utf8");
content = content.replace(/<div\s*\n\s*background="#050505"/g, "<BrailleTerrainBackground \n      background=\"#050505\"");
content = content.replace(/<\/div>\s*<\/div>\s*\);\s*\}/g, "</div>\n    </BrailleTerrainBackground>\n  );\n}");
fs.writeFileSync(file, content, "utf8");

