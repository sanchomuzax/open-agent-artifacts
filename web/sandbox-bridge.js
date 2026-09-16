(() => {
  "use strict";

  const script = document.currentScript;
  const nonce = script?.dataset.nonce || "";
  const versionId = script?.dataset.versionId || "";

  function send(payload) {
    window.parent.postMessage({ ...payload, schema: 2, nonce, version_id: versionId }, "*");
  }

  function reportHeight() {
    send({ type: "oaa-resize", height: Math.ceil(document.documentElement.scrollHeight) });
  }

  function reportSelection() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.toString().trim()) return;
    const range = selection.getRangeAt(0);
    const exact = selection.toString().trim();
    const before = document.createRange();
    before.selectNodeContents(document.body);
    before.setEnd(range.startContainer, range.startOffset);
    const selected = range.toString();
    const leading = Math.max(0, selected.indexOf(exact));
    const start = Array.from(before.toString()).length + Array.from(selected.slice(0, leading)).length;
    const length = Array.from(exact).length;
    const points = Array.from(document.body.textContent || "");
    send({ type: "oaa-selection", anchor: {
      schema: 2,
      type: "text-range",
      coordinate_space: "unicode-code-points",
      start,
      end: start + length,
      quote: {
        exact,
        prefix: points.slice(Math.max(0, start - 80), start).join(""),
        suffix: points.slice(start + length, start + length + 80).join(""),
      },
    } });
  }

  document.addEventListener("mouseup", reportSelection);
  document.addEventListener("keyup", reportSelection);

  if (window.ResizeObserver) new ResizeObserver(reportHeight).observe(document.documentElement);
  window.addEventListener("load", reportHeight);
  reportHeight();
})();
