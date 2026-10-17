// CoursePilot brand assets (Phase 7.1). Reproducible: `node scripts/brand-assets.mjs`.
//
// SOURCE OF TRUTH: brand/source/coursepilot-logo-source.jpg - the supplied
// logo, byte-for-byte, never modified. A 1254x1254 JPEG: blue cap + white
// arrow on a BAKED black background (JPEG has no alpha channel).
//
// Transparency is EXTRACTED, not faked with CSS: the mark is light on pure
// black, so per pixel alpha = smoothstep(LO, HI, max(r, g, b)) and edge colours
// are un-premultiplied (c / alpha) so no black fringe survives. Every pixel
// inside the mark is far brighter than HI, so the mark itself stays opaque.
//
// Outputs (public/brand/, src/app/): see the ASSETS table in brand/README.md.

import { mkdir, writeFile, copyFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = path.join(ROOT, "brand/source/coursepilot-logo-source.jpg");
const OUT = path.join(ROOT, "public/brand");
const APP = path.join(ROOT, "src/app");

const LO = 14;   // at or below: background (JPEG noise on black)
const HI = 56;   // at or above: fully opaque mark
const BLACK = { r: 0, g: 0, b: 0, alpha: 1 };   // the source's own background

function smoothstep(lo, hi, x) {
  const t = Math.min(1, Math.max(0, (x - lo) / (hi - lo)));
  return t * t * (3 - 2 * t);
}

async function extractMark() {
  const { data, info } = await sharp(SOURCE).removeAlpha().raw()
    .toBuffer({ resolveWithObject: true });
  const { width, height } = info;
  const out = Buffer.alloc(width * height * 4);
  let minX = width, minY = height, maxX = 0, maxY = 0;
  for (let i = 0, j = 0; i < data.length; i += 3, j += 4) {
    const r = data[i], g = data[i + 1], b = data[i + 2];
    const a = smoothstep(LO, HI, Math.max(r, g, b));
    if (a > 0) {
      out[j] = Math.min(255, Math.round(r / a));
      out[j + 1] = Math.min(255, Math.round(g / a));
      out[j + 2] = Math.min(255, Math.round(b / a));
      out[j + 3] = Math.round(a * 255);
      if (a > 0.02) {
        const p = i / 3, x = p % width, y = Math.floor(p / width);
        minX = Math.min(minX, x); maxX = Math.max(maxX, x);
        minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      }
    }
  }
  const crop = { left: minX, top: minY, width: maxX - minX + 1, height: maxY - minY + 1 };
  const trimmed = await sharp(out, { raw: { width, height, channels: 4 } })
    .extract(crop).png().toBuffer();
  return { trimmed, crop };
}

// Square canvas with the mark centred, occupying `fill` of the side.
async function square(mark, crop, side, fill, background) {
  const inner = Math.round(side * fill);
  const resized = await sharp(mark).resize(inner, inner, { fit: "inside",
    kernel: "lanczos3" }).png().toBuffer();
  const meta = await sharp(resized).metadata();
  return sharp({ create: { width: side, height: side, channels: 4,
    background: background ?? { r: 0, g: 0, b: 0, alpha: 0 } } })
    .composite([{ input: resized, left: Math.round((side - meta.width) / 2),
                  top: Math.round((side - meta.height) / 2) }])
    .png({ compressionLevel: 9 }).toBuffer();
}

async function main() {
  await mkdir(OUT, { recursive: true });
  const { trimmed, crop } = await extractMark();
  const written = [];
  const put = async (file, buf) => {
    await writeFile(file, buf);
    written.push(path.relative(ROOT, file));
  };

  // The transparent mark, at the sizes the product uses.
  for (const side of [1024, 512, 256, 128, 64]) {
    await put(path.join(OUT, `coursepilot-mark-${side}.png`),
      await square(trimmed, crop, side, 0.96));
  }
  // Favicons: tighter crop so the cap reads at 16-48 px (no other change).
  await put(path.join(APP, "icon.png"), await square(trimmed, crop, 64, 1.0));
  // Apple touch icon: iOS fills transparency with black anyway; use the
  // source's own black so the icon matches the original artwork exactly.
  await put(path.join(APP, "apple-icon.png"), await square(trimmed, crop, 180, 0.78, BLACK));
  // PWA: "any" icons transparent; maskable keeps the mark inside the 80% safe zone.
  await put(path.join(OUT, "pwa-192.png"), await square(trimmed, crop, 192, 0.92));
  await put(path.join(OUT, "pwa-512.png"), await square(trimmed, crop, 512, 0.92));
  await put(path.join(OUT, "pwa-maskable-512.png"),
    await square(trimmed, crop, 512, 0.62, BLACK));
  // Native app-icon SOURCE (future iOS/Android): opaque, the original's black.
  await put(path.join(OUT, "app-icon-source-1024.png"),
    await square(trimmed, crop, 1024, 0.74, BLACK));
  // Social/OG image background-safe copy of the mark on black.
  await copyFile(SOURCE, path.join(OUT, "coursepilot-logo-original.jpg"));
  written.push("public/brand/coursepilot-logo-original.jpg");

  console.log(`mark bbox in source: ${JSON.stringify(crop)}`);
  console.log(written.join("\n"));
}

main().catch((e) => { console.error(e); process.exit(1); });
