// Rasterize the SVG icon into the PNG sizes iOS / Android / desktop
// PWAs all want. Runs before `vite build` (see package.json `prebuild`).
//
// We keep the SVG as the source of truth and generate PNGs from it on
// every build, so designers can iterate on icon.svg without touching
// binary blobs.

import sharp from "sharp";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const pub = resolve(__dirname, "..", "public");

const sources = [
  { svg: "icon.svg", out: "apple-touch-icon.png", size: 180 },
  { svg: "icon.svg", out: "icon-192.png", size: 192 },
  { svg: "icon.svg", out: "icon-512.png", size: 512 },
  // Maskable icon — Android wants safe-zone padding (~10%). The
  // simplest way is to render the regular icon centred on a solid
  // dark background — the SVG already has its own dark rounded
  // background, so we just resize.
  { svg: "icon.svg", out: "icon-mask-512.png", size: 512 },
];

for (const { svg, out, size } of sources) {
  const buf = readFileSync(resolve(pub, svg));
  await sharp(buf)
    .resize(size, size, { fit: "contain", background: "#070A14" })
    .png({ compressionLevel: 9 })
    .toFile(resolve(pub, out));
  console.log(`✓ ${out}  (${size}×${size})`);
}
