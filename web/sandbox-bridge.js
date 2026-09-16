(() => {
  "use strict";

  const script = document.currentScript;
  const nonce = script?.dataset.nonce || "";
  const versionId = script?.dataset.versionId || "";

  function send(payload) {
    window.parent.postMessage({ ...payload, schema: 1, nonce, version_id: versionId }, "*");
  }

  function reportHeight() {
    send({ type: "oaa-resize", height: Math.ceil(document.documentElement.scrollHeight) });
  }

  document.addEventListener("mouseup", () => {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.toString().trim()) return;
    send({ type: "oaa-selection", exact: selection.toString() });
  });

  if (window.ResizeObserver) new ResizeObserver(reportHeight).observe(document.documentElement);
  window.addEventListener("load", reportHeight);
  reportHeight();
})();
