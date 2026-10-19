import { build } from "esbuild";
import { cpSync, mkdirSync, rmSync, writeFileSync } from "node:fs";

const OUT = ["www", "../server/static"];
const FONTS = [
  ["barlow", "Barlow", 400], ["barlow", "Barlow", 500], ["barlow", "Barlow", 600],
  ["barlow-condensed", "Barlow Condensed", 600],
];
const RANGES = {
  latin: "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD",
  "latin-ext": "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF",
};

for (const dir of OUT) {
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(`${dir}/fonts`, { recursive: true });

  await build({
    entryPoints: ["src/app.js"],
    bundle: true,
    minify: true,
    format: "iife",
    target: ["chrome90", "safari15"],
    outfile: `${dir}/app.js`,
  });
  for (const f of ["index.html", "app.css", "manifest.webmanifest", "sw.js", "icons"]) {
    cpSync(`src/${f}`, `${dir}/${f}`, { recursive: true });
  }

  let css = "";
  for (const [pkg, family, weight] of FONTS) {
    for (const [subset, range] of Object.entries(RANGES)) {
      const file = `${pkg}-${subset}-${weight}-normal.woff2`;
      cpSync(`node_modules/@fontsource/${pkg}/files/${file}`, `${dir}/fonts/${file}`);
      css += `@font-face{font-family:"${family}";font-style:normal;font-display:swap;font-weight:${weight};src:url(${file}) format("woff2");unicode-range:${range}}\n`;
    }
  }
  writeFileSync(`${dir}/fonts/fonts.css`, css);
}
console.log("Built www/ and ../server/static/");
