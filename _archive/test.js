/**
 * test.js - 最小docx動作確認
 * Usage: node test.js
 * output/test.docx が生成される
 */
"use strict";

const { Document, Packer, Paragraph, TextRun } = require("docx");
const fs = require("fs");
const path = require("path");

const doc = new Document({
  sections: [
    {
      children: [
        new Paragraph({
          children: [new TextRun("テスト - ポーカーGTO")],
        }),
      ],
    },
  ],
});

Packer.toBuffer(doc).then((buf) => {
  fs.mkdirSync("output", { recursive: true });
  fs.writeFileSync(path.join("output", "test.docx"), buf);
  console.log("done: output/test.docx");
});
