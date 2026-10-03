
const fs = require("fs");
const file = "d:/Some_stuffs/Codeutsava X.0/NIT-Raipur/app/src/app/terminal/page.tsx";
let content = fs.readFileSync(file, "utf8");
content = content.split("`n").join("\n");
fs.writeFileSync(file, content, "utf8");

