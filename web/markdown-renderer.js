(function attachMarkdownRenderer(global, factory) {
  const renderer = factory();
  if (typeof module === "object" && module.exports) module.exports = renderer;
  else global.OAAMarkdownRenderer = renderer;
})(typeof globalThis === "undefined" ? this : globalThis, () => {
  function findInlineClose(value, marker, start) {
    let cursor = start;
    while ((cursor = value.indexOf(marker, cursor)) >= 0) {
      let slashes = 0;
      for (let index = cursor - 1; index >= 0 && value[index] === "\\"; index -= 1) slashes += 1;
      if (slashes % 2 === 0) return cursor;
      cursor += marker.length;
    }
    return -1;
  }

  function parseInlineLink(value, start) {
    if (value[start] !== "[") return null;
    const labelEnd = findInlineClose(value, "]", start + 1);
    if (labelEnd < 0 || value[labelEnd + 1] !== "(") return null;
    const hrefEnd = findInlineClose(value, ")", labelEnd + 2);
    if (hrefEnd < 0) return null;
    return {
      end: hrefEnd + 1,
      label: value.slice(start + 1, labelEnd),
      href: value.slice(labelEnd + 2, hrefEnd),
    };
  }

  function safeUrl(value, baseUrl) {
    try {
      const url = new URL(value, baseUrl || "https://open-agent-artifacts.invalid/");
      return ["http:", "https:", "mailto:"].includes(url.protocol) ? url.href : null;
    } catch (_) {
      return null;
    }
  }

  function parseInline(value, baseUrl) {
    const text = String(value || "");
    const tokens = [];
    let index = 0;
    let textStart = 0;
    const pushText = (content) => {
      if (!content) return;
      const previous = tokens[tokens.length - 1];
      if (previous?.type === "text") previous.text += content;
      else tokens.push({ type: "text", text: content });
    };
    const flushText = (end) => {
      if (end > textStart) pushText(text.slice(textStart, end));
    };
    while (index < text.length) {
      if (text.startsWith("**", index)) {
        const close = findInlineClose(text, "**", index + 2);
        if (close >= 0) {
          flushText(index);
          tokens.push({ type: "strong", children: parseInline(text.slice(index + 2, close), baseUrl) });
          index = close + 2;
          textStart = index;
          continue;
        }
      }
      if (text[index] === "`") {
        const close = findInlineClose(text, "`", index + 1);
        if (close >= 0) {
          flushText(index);
          tokens.push({ type: "code", text: text.slice(index + 1, close) });
          index = close + 1;
          textStart = index;
          continue;
        }
      }
      if (text[index] === "*" && text[index + 1] !== "*") {
        const close = findInlineClose(text, "*", index + 1);
        if (close >= 0 && text[close + 1] !== "*") {
          flushText(index);
          tokens.push({ type: "emphasis", children: parseInline(text.slice(index + 1, close), baseUrl) });
          index = close + 1;
          textStart = index;
          continue;
        }
      }
      const link = parseInlineLink(text, index);
      if (link) {
        flushText(index);
        const href = safeUrl(link.href, baseUrl);
        if (href) tokens.push({ type: "link", href, children: parseInline(link.label, baseUrl) });
        else pushText(text.slice(index, link.end));
        index = link.end;
        textStart = index;
        continue;
      }
      index += 1;
    }
    flushText(text.length);
    return tokens;
  }

  function splitTableRow(line) {
    const text = String(line || "");
    const cells = [];
    let cell = "";
    let hasSeparator = false;
    for (let index = 0; index < text.length; index += 1) {
      if (text[index] === "\\" && text[index + 1] === "|") {
        cell += "|";
        index += 1;
      } else if (text[index] === "|") {
        cells.push(cell.trim());
        cell = "";
        hasSeparator = true;
      } else {
        cell += text[index];
      }
    }
    if (!hasSeparator) return null;
    cells.push(cell.trim());
    if (cells[0] === "") cells.shift();
    if (cells[cells.length - 1] === "") cells.pop();
    return cells.length >= 2 ? cells : null;
  }

  function isTableDelimiter(cells) {
    return Boolean(cells && cells.length >= 2 && cells.every((cell) => /^:?-{3,}:?$/.test(cell)));
  }

  function readMarkdownTable(lines, start, baseUrl) {
    const header = splitTableRow(lines[start]);
    const delimiter = splitTableRow(lines[start + 1]);
    if (!header || !isTableDelimiter(delimiter)) return null;
    const rows = [];
    let index = start + 2;
    while (index < lines.length && lines[index].trim()) {
      const row = splitTableRow(lines[index]);
      if (!row) break;
      rows.push(row);
      index += 1;
    }
    const columnCount = Math.max(header.length, delimiter.length, ...rows.map((row) => row.length));
    const alignments = Array.from({ length: columnCount }, (_, column) => {
      const marker = delimiter[column] || "";
      return marker.startsWith(":") && marker.endsWith(":")
        ? "center"
        : marker.endsWith(":")
          ? "right"
          : marker.startsWith(":")
            ? "left"
            : "";
    });
    const inlineRow = (row) => Array.from({ length: columnCount }, (_, column) => parseInline(row[column] || "", baseUrl));
    return {
      type: "table",
      header: inlineRow(header),
      alignments,
      rows: rows.map(inlineRow),
      next: index,
    };
  }

  function parseMarkdown(source, options = {}) {
    const baseUrl = options.baseUrl;
    const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
    const blocks = [];
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) {
        index += 1;
        continue;
      }
      if (/^```/.test(line)) {
        const language = line.slice(3).trim();
        const codeLines = [];
        index += 1;
        while (index < lines.length && !/^```/.test(lines[index])) codeLines.push(lines[index++]);
        if (index < lines.length) index += 1;
        blocks.push({ type: "code", language, text: codeLines.join("\n") });
        continue;
      }
      const table = readMarkdownTable(lines, index, baseUrl);
      if (table) {
        blocks.push(table);
        index = table.next;
        continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) {
        blocks.push({ type: "heading", level: heading[1].length, children: parseInline(heading[2], baseUrl) });
        index += 1;
        continue;
      }
      const ordered = line.match(/^(\d+)[.)]\s+(.+)$/);
      if (ordered) {
        const items = [];
        while (index < lines.length) {
          const item = lines[index].match(/^(\d+)[.)]\s+(.+)$/);
          if (!item) break;
          items.push(parseInline(item[2], baseUrl));
          index += 1;
        }
        blocks.push({ type: "list", ordered: true, start: Number(ordered[1]), items });
        continue;
      }
      if (/^[-*+]\s+/.test(line)) {
        const items = [];
        while (index < lines.length && /^[-*+]\s+/.test(lines[index])) {
          items.push(parseInline(lines[index].replace(/^[-*+]\s+/, ""), baseUrl));
          index += 1;
        }
        blocks.push({ type: "list", ordered: false, items });
        continue;
      }
      if (/^>\s?/.test(line)) {
        blocks.push({ type: "quote", children: parseInline(line.replace(/^>\s?/, ""), baseUrl) });
        index += 1;
        continue;
      }
      const paragraphLines = [line];
      index += 1;
      while (
        index < lines.length
        && lines[index].trim()
        && !readMarkdownTable(lines, index, baseUrl)
        && !/^(#{1,6})\s|^```|^\d+[.)]\s+|^[-*+]\s+|^>\s?/.test(lines[index])
      ) paragraphLines.push(lines[index++]);
      blocks.push({ type: "paragraph", children: parseInline(paragraphLines.join(" "), baseUrl) });
    }
    return blocks;
  }

  return { parseMarkdown };
});
