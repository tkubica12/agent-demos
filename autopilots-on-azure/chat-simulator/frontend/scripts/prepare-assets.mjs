import { copyFileSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";

const source = new URL("../node_modules/@lobehub/fluent-emoji-3d/assets/", import.meta.url);
const target = new URL("../public/emoji/", import.meta.url);
const files = new Set(readdirSync(source).filter((name) => name.endsWith(".webp")));
const data = JSON.parse(readFileSync(new URL("../node_modules/emojibase-data/en/data.json", import.meta.url)));
mkdirSync(target, { recursive: true });

const entries = [];
const seen = new Set();
function add(item) {
  const hex = (text) => [...text].map((c) => c.codePointAt(0).toString(16).padStart(4, "0")).join("-");
  const variants = [item.hexcode.toLowerCase(), hex(item.emoji), hex(item.emoji).replace(/-fe0f/g, "")];
  const name = variants.map((code) => `${code}.webp`).find((candidate) => files.has(candidate));
  if (name && !seen.has(item.emoji)) {
    seen.add(item.emoji);
    entries.push({
      emoji: item.emoji, label: item.label, tags: item.tags ?? [],
      group: item.group ?? 8, order: item.order ?? Number.MAX_SAFE_INTEGER,
      url: `/emoji/${name}`,
    });
  }
  for (const skin of item.skins ?? []) add(skin);
}
for (const item of data) add(item);
if (entries.length < 1000) throw new Error(`Unexpectedly small Fluent 3D catalogue: ${entries.length}`);
entries.sort((a, b) => a.order - b.order);
for (const name of new Set(entries.map((entry) => entry.url.slice("/emoji/".length)))) {
  copyFileSync(new URL(name, source), new URL(name, target));
}
writeFileSync(new URL("catalogue.json", target), JSON.stringify({
  assetPackage: "@lobehub/fluent-emoji-3d@1.1.0", metadataPackage: "emojibase-data@18.0.0",
  entries,
}));

let notices = readFileSync(new URL("../THIRD-PARTY-NOTICES.txt", import.meta.url), "utf8");
for (const name of ["react", "react-dom", "@fluentui/react-components", "emojibase-data"]) {
  const directory = new URL(`../node_modules/${name}/`, import.meta.url);
  const license = readdirSync(directory).find((file) => /^license(?:\.|$)/i.test(file));
  if (!license) throw new Error(`Cannot distribute ${name} without its license notice`);
  notices += `\n\n--- ${name} ---\n${readFileSync(new URL(license, directory), "utf8")}`;
}
writeFileSync(new URL("../public/third-party-notices.txt", import.meta.url), notices);
writeFileSync(new URL("../public/favicon.svg", import.meta.url),
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#0f6cbd"/><path d="M7 8h18v13H14l-6 5v-5H7z" fill="#fff"/><path d="M11 12h10M11 16h7" stroke="#0f6cbd" stroke-width="2"/></svg>\n');
console.log(`Prepared ${entries.length} labelled Fluent 3D emoji; all assets local, no CDN requests.`);
