
const fs = require("fs");
const path = require("path");
const file = path.join(__dirname, "src/app/terminal/page.tsx");
let content = fs.readFileSync(file, "utf8");
content = content.replace(/^\uFEFF/, "");
content = content.replace(/\u200B/g, ""); // zero width space
content = content.replace(/\uFFFD/g, ""); // replacement character
fs.writeFileSync(file, content, "utf8");
console.log("Stripped BOM");

