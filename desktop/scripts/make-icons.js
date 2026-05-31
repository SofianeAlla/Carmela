// Convert moose-logo.svg into PNG (multiple sizes) + a multi-resolution ICO
// for Electron window + electron-builder installer icons.
//   node scripts/make-icons.js
const sharp = require('sharp');
const fs = require('node:fs');
const path = require('node:path');

const SRC = path.join(__dirname, '..', 'renderer', 'assets', 'moose-logo.svg');
const OUT_DIR = path.join(__dirname, '..', 'renderer', 'assets');

async function main() {
  if (!fs.existsSync(SRC)) {
    console.error('SVG not found:', SRC);
    process.exit(1);
  }
  const sizes = [16, 24, 32, 48, 64, 128, 256, 512];
  const buffers = {};
  for (const sz of sizes) {
    const buf = await sharp(SRC, { density: 384 })
      .resize(sz, sz, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
      .png()
      .toBuffer();
    buffers[sz] = buf;
    if (sz === 256 || sz === 512) {
      const p = path.join(OUT_DIR, `moose-logo-${sz}.png`);
      fs.writeFileSync(p, buf);
      console.log('wrote', p, `(${(buf.length / 1024).toFixed(1)} KB)`);
    }
  }
  // Primary PNG used by the HTML brand mark.
  fs.writeFileSync(path.join(OUT_DIR, 'moose-logo.png'), buffers[256]);
  console.log('wrote moose-logo.png');

  // Multi-resolution ICO (Windows). Build manually since sharp doesn't write ICO.
  const order = [256, 128, 64, 48, 32, 24, 16];
  const head = Buffer.alloc(6);
  head.writeUInt16LE(0, 0);        // reserved
  head.writeUInt16LE(1, 2);        // type = ICO
  head.writeUInt16LE(order.length, 4);
  const entries = [];
  const datas = [];
  let offset = 6 + 16 * order.length;
  for (const sz of order) {
    const data = buffers[sz];
    const entry = Buffer.alloc(16);
    entry.writeUInt8(sz === 256 ? 0 : sz, 0);  // width (0 means 256)
    entry.writeUInt8(sz === 256 ? 0 : sz, 1);  // height
    entry.writeUInt8(0, 2);                    // colors in palette
    entry.writeUInt8(0, 3);                    // reserved
    entry.writeUInt16LE(1, 4);                 // color planes
    entry.writeUInt16LE(32, 6);                // bpp
    entry.writeUInt32LE(data.length, 8);       // size
    entry.writeUInt32LE(offset, 12);           // file offset
    entries.push(entry);
    datas.push(data);
    offset += data.length;
  }
  const ico = Buffer.concat([head, ...entries, ...datas]);
  const icoPath = path.join(OUT_DIR, 'moose-logo.ico');
  fs.writeFileSync(icoPath, ico);
  console.log('wrote', icoPath, `(${(ico.length / 1024).toFixed(1)} KB, ${order.length} sizes)`);
}

main().catch((e) => { console.error(e); process.exit(1); });
