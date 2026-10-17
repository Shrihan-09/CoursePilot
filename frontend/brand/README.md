# CoursePilot brand assets

## Source of truth

`source/coursepilot-logo-source.jpg` is the supplied CoursePilot logo,
**byte-for-byte**:

| Property | Value |
|---|---|
| SHA-256 | `cf1f8d2ff27b494c68256a8007cf3944659c92d3e918b87d4f54d3859ddad2fd` |
| Format | baseline JPEG, 1254×1254, 3 channels (no alpha) |
| Background | a **baked-in pure black** background |

It is never modified. A second reference ("logo one.jpg") was supplied, but it
was byte-identical to this file, so this is the only source.

## Derived assets

Every derived asset is regenerated with:

```
node scripts/brand-assets.mjs
```

**Transparency is extracted, not faked with CSS.** The mark is light on pure
black, so:

1. each pixel's alpha is `smoothstep(14, 56, max(r, g, b))`;
2. edge colours are un-premultiplied (`c / alpha`), so no black fringe remains;
3. the mark is trimmed to its bounding box, then re-padded.

Every pixel inside the mark is far brighter than the threshold, so the mark
stays fully opaque. It was checked composited on white, light grey and
near-black.

| File | Size | Background | Used for |
|---|---|---|---|
| `public/brand/coursepilot-mark-{64,128,256,512,1024}.png` | square | transparent | in-app mark, sign-in, generation animation |
| `src/app/icon.png` | 64 | transparent, tight crop | favicon |
| `src/app/apple-icon.png` | 180 | the source's black | iOS home screen (iOS fills transparency anyway) |
| `public/brand/pwa-{192,512}.png` | 192, 512 | transparent | web app manifest ("any") |
| `public/brand/pwa-maskable-512.png` | 512 | black, mark inside the 80% safe zone | manifest ("maskable") |
| `public/brand/app-icon-source-1024.png` | 1024 | black, opaque | **source for future native app icons** |
| `public/brand/coursepilot-logo-original.jpg` | 1254 | original | reference / social |

## Simplifications

- **No redrawing, no recolouring.** Every size is the original artwork.
- **Favicon sizes use a tighter crop** (the mark fills the canvas), so the cap
  reads at 16–48 px.
- **At 16 px the arrow's tail merges into the cap.** The cap silhouette and the
  white arrowhead still identify the mark, so no hand-simplified variant was
  made. If a future platform requires one (for example a single-colour menu-bar
  icon), it should be drawn from this artwork and documented here.
