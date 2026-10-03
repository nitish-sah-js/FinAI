
const fs = require("fs");
const path = require("path");
const file = path.join(__dirname, "src/app/terminal/page.tsx");
let content = fs.readFileSync(file, "utf8");
content = content.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
fs.writeFileSync(file, content, "utf8");
console.log("Fixed CRLF!");

