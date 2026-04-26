// 用 Node.js 生成简单的 PNG 图标（蓝色文档图标）
const fs = require('fs');
const path = require('path');

function createPNG(size) {
  // 简单的 PNG 文件生成
  // 这是一个最小化的有效 PNG 文件（1x1 蓝色像素 + 缩放）
  
  const header = Buffer.from([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, // PNG signature
  ]);
  
  // IHDR chunk
  function ihdr(w, h) {
    const data = Buffer.alloc(13);
    data.writeUInt32BE(w, 0);
    data.writeUInt32BE(h, 4);
    data[8] = 8;  // bit depth
    data[9] = 2;  // color type (RGB)
    data[10] = 0; // compression
    data[11] = 0; // filter
    data[12] = 0; // interlace
    return chunk('IHDR', data);
  }

  // IDAT chunk (raw pixel data)
  function idat(w, h) {
    const raw = Buffer.alloc(h * (1 + w * 3)); // filter byte per row + RGB pixels
    for (let y = 0; y < h; y++) {
      raw[y * (1 + w * 3)] = 0; // no filter
      for (let x = 0; x < w; x++) {
        const offset = y * (1 + w * 3) + 1 + x * 3;
        // Blue gradient background with document-like appearance
        const cx = w / 2, cy = h / 2;
        const dist = Math.sqrt((x - cx) ** 2 + (y - cy) ** 2);
        const maxDist = Math.sqrt(cx ** 2 + cy ** 2);
        
        if (size >= 48 && x > w*0.15 && x < w*0.85 && y > h*0.1 && y < h*0.9) {
          // White document area
          raw[offset] = 255;     // R
          raw[offset+1] = 255;   // G
          raw[offset+2] = 255;   // B
          
          // Blue border on left edge
          if (x < w * 0.18 || x > w * 0.82 || y < h * 0.12 || y > h * 0.88) {
            raw[offset] = 22;     // R (#1677ff)
            raw[offset+1] = 119;  // G
            raw[offset+2] = 255;  // B
          }
        } else {
          // Transparent/white background
          raw[offset] = 22;
          raw[offset+1] = 119;
          raw[offset+2] = 255;
        }
      }
    }
    
    // Deflate compress
    const zlib = require('zlib');
    const compressed = zlib.deflateSync(raw);
    return chunk('IDAT', compressed);
  }

  function chunk(type, data) {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length, 0);
    const typeB = Buffer.from(type);
    const crcData = Buffer.concat([typeB, data]);
    const crc = crc32(crcData);
    const crcBuf = Buffer.alloc(4);
    crcBuf.writeUInt32BE(crc >>> 0, 0);
    return Buffer.concat([len, typeB, data, crcBuf]);
  }

  function crc32(buf) {
    let c = 0xFFFFFFFF;
    for (let i = 0; i < buf.length; i++) {
      c ^= buf[i];
      for (let j = 0; j < 8; j++) c = (c >>> 1) ^ (c & 1 ? 0xEDB88320 : 0);
    }
    return (c ^ 0xFFFFFFFF) >>> 0;
  }

  // IEND
  const iend = chunk('IEND', Buffer.alloc(0));

  return Buffer.concat([header, ihdr(size, size), idat(size, size), iend]);
}

const dir = __dirname;
[16, 48, 128].forEach(s => {
  const png = createPNG(s);
  fs.writeFileSync(path.join(dir, `icon${s}.png`), png);
  console.log(`Generated icon${s}.png (${png.length} bytes)`);
});
