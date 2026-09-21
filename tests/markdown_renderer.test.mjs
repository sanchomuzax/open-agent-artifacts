import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { parseMarkdown } = require("../web/markdown-renderer.js");

function inlineTypes(tokens) {
  return tokens.map((token) => token.type);
}

test("parses GFM tables with alignment, escaped pipes, ragged rows, and inline tokens", () => {
  const [table] = parseMarkdown(
    "| Name | Details | Alignment |\n| :--- | :---: | ---: |\n| A\\|B | **bold with `code`** and [safe](https://example.com) | left |\n| Missing | only two |\n| Extra | more | right | preserved |"
  );

  assert.equal(table.type, "table");
  assert.deepEqual(table.alignments, ["left", "center", "right", ""]);
  assert.deepEqual(table.header.map((cell) => cell.map((token) => token.text || token.type)), [
    ["Name"],
    ["Details"],
    ["Alignment"],
    [],
  ]);
  assert.equal(table.rows.length, 3);
  assert.equal(table.rows[0][0][0].text, "A|B");
  assert.deepEqual(inlineTypes(table.rows[0][1]), ["strong", "text", "link"]);
  assert.deepEqual(table.rows[0][1][0].children.map((token) => token.type), ["text", "code"]);
  assert.equal(table.rows[0][1][2].href, "https://example.com/");
  assert.deepEqual(table.rows[1][2], []);
  assert.equal(table.rows[2][3][0].text, "preserved");
});

test("keeps pipe text and delimiter-looking lines in paragraphs when there is no header row", () => {
  const blocks = parseMarkdown(
    "Ordinary paragraph followed by a delimiter-looking row:\n| --- | --- |\nThis is still paragraph text."
  );

  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].type, "paragraph");
  assert.match(blocks[0].children.map((token) => token.text || "").join(""), /\| --- \| --- \|/);
});

test("parses ordered lists with their starting number", () => {
  const [list] = parseMarkdown("4. Starts at four\n5) Continues");

  assert.equal(list.type, "list");
  assert.equal(list.ordered, true);
  assert.equal(list.start, 4);
  assert.equal(list.items.length, 2);
});

test("recursively parses nested inline formatting and leaves unsafe links as text", () => {
  const [paragraph] = parseMarkdown(
    '**bold with `code`** and [safe](https://example.com) <script>alert(1)</script> [bad](javascript:alert(1))'
  );

  assert.deepEqual(inlineTypes(paragraph.children), ["strong", "text", "link", "text"]);
  assert.deepEqual(paragraph.children[0].children.map((token) => token.type), ["text", "code"]);
  assert.match(paragraph.children[3].text, /<script>alert\(1\)<\/script>/);
  assert.match(paragraph.children[3].text, /\[bad\]/);
  assert.equal(paragraph.children.some((token) => token.href?.startsWith("javascript:")), false);
});
