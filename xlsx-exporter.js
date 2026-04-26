(() => {
  if (globalThis.__IM_ARCHIVE_XLSX_READY__) {
    return;
  }

  function escapeXml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&apos;");
  }

  function colName(index) {
    let n = index + 1;
    let name = "";
    while (n > 0) {
      const remainder = (n - 1) % 26;
      name = String.fromCharCode(65 + remainder) + name;
      n = Math.floor((n - 1) / 26);
    }
    return name;
  }

  function createSheetXml(rows) {
    const xmlRows = rows.map((row, rowIndex) => {
      const cells = row.map((cell, colIndex) => {
        const ref = `${colName(colIndex)}${rowIndex + 1}`;
        return `<c r="${ref}" t="inlineStr"><is><t>${escapeXml(cell)}</t></is></c>`;
      }).join("");
      return `<row r="${rowIndex + 1}">${cells}</row>`;
    }).join("");

    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>${xmlRows}</sheetData>
</worksheet>`;
  }

  async function buildWorkbookBuffer(workbook) {
    if (!globalThis.JSZip) {
      throw new Error("JSZip 未注入");
    }

    const zip = new globalThis.JSZip();
    zip.file("[Content_Types].xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  ${workbook.sheets.map((_, index) => `<Override PartName="/xl/worksheets/sheet${index + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join("\n  ")}
</Types>`);

    zip.folder("_rels").file(".rels", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>`);

    zip.folder("xl").file("workbook.xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    ${workbook.sheets.map((sheet, index) => `<sheet name="${escapeXml(sheet.name)}" sheetId="${index + 1}" r:id="rId${index + 1}"/>`).join("\n    ")}
  </sheets>
</workbook>`);

    zip.folder("xl").folder("_rels").file("workbook.xml.rels", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  ${workbook.sheets.map((_, index) => `<Relationship Id="rId${index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${index + 1}.xml"/>`).join("\n  ")}
  <Relationship Id="rId${workbook.sheets.length + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>`);

    zip.folder("xl").file("styles.xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border/></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
</styleSheet>`);

    const worksheets = zip.folder("xl").folder("worksheets");
    workbook.sheets.forEach((sheet, index) => {
      worksheets.file(`sheet${index + 1}.xml`, createSheetXml(sheet.rows));
    });

    return zip.generateAsync({ type: "uint8array" });
  }

  function uint8ArrayToBase64(bytes) {
    let binary = "";
    const chunkSize = 0x8000;
    for (let i = 0; i < bytes.length; i += chunkSize) {
      const chunk = bytes.subarray(i, i + chunkSize);
      binary += String.fromCharCode(...chunk);
    }
    return btoa(binary);
  }

  async function downloadWorkbook(workbook, filename) {
    const bytes = await buildWorkbookBuffer(workbook);
    const base64 = uint8ArrayToBase64(bytes);
    const url = `data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,${base64}`;
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.style.display = "none";
    document.documentElement.appendChild(anchor);
    anchor.click();
    setTimeout(() => anchor.remove(), 1500);
    return { filename, size: bytes.length };
  }

  globalThis.__IM_ARCHIVE_XLSX_READY__ = true;
  globalThis.__IM_ARCHIVE_XLSX__ = {
    downloadWorkbook
  };
})();
