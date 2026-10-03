
const fs = require("fs");
const path = require("path");
const file = path.join(__dirname, "src/app/terminal/page.tsx");
let content = fs.readFileSync(file, "utf8");
// if the read returns invalid replacement chars, we can just do a regex replace
content = content.replace(/-\?/g, "?");
content = content.replace(/-/g, "?");
content = content.replace(/A/g, "·");
content = content.replace(/\?"/g, "—\"");
content = content.replace(//g, ""); // strip remaining corrupted chars
fs.writeFileSync(file, content, "utf8");
console.log("Fixed!");

