(() => {
  "use strict";

  const state = { artifacts: [], artifact: null, versions: [], version: null, selection: null };
  const $ = (id) => document.getElementById(id);
  const apiToken = () => localStorage.getItem("oaa_api_token") || "";

  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (options.body) headers["Content-Type"] = "application/json";
    if (apiToken()) headers.Authorization = `Bearer ${apiToken()}`;
    const response = await fetch(path, { ...options, headers, cache: "no-store" });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) throw new Error(payload.message || `${response.status} ${response.statusText}`);
    return payload;
  }

  function setStatus(element, message, kind = "muted") {
    element.textContent = message || "";
    element.className = `status ${kind}`;
  }

  function renderCatalog() {
    const list = $("artifact-list");
    const query = $("search").value.trim().toLowerCase();
    list.replaceChildren();
    const items = state.artifacts.filter((item) => !query || `${item.title} ${item.slug}`.toLowerCase().includes(query));
    $("artifact-count").textContent = String(items.length);
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "no-comments";
      empty.textContent = query ? "No matching artifacts." : "No artifacts yet.";
      list.append(empty);
      return;
    }
    items.forEach((item) => {
      const button = document.createElement("button");
      button.className = `artifact-item${state.artifact?.id === item.id ? " active" : ""}`;
      button.type = "button";
      const title = document.createElement("strong");
      title.textContent = item.title;
      const meta = document.createElement("span");
      meta.textContent = `${item.kind} · ${item.slug}`;
      button.append(title, meta);
      button.addEventListener("click", () => loadArtifact(item.id));
      list.append(button);
    });
  }

  async function loadCatalog() {
    setStatus($("catalog-status"), "Loading catalog…");
    try {
      state.artifacts = await api("/api/artifacts");
      setStatus($("catalog-status"), state.artifacts.length ? "" : "Catalog is empty.");
      renderCatalog();
      if (!state.artifact && state.artifacts.length) await loadArtifact(state.artifacts[0].id, false);
    } catch (error) {
      setStatus($("catalog-status"), error.message, "error");
    }
  }

  function routeId() {
    const match = location.hash.match(/^#\/artifact\/([^/]+)$/);
    return match ? decodeURIComponent(match[1]) : null;
  }

  async function loadArtifact(id, updateHash = true) {
    if (updateHash) history.replaceState(null, "", `#/artifact/${encodeURIComponent(id)}`);
    try {
      state.artifact = await api(`/api/artifacts/${encodeURIComponent(id)}`);
      state.versions = await api(`/api/artifacts/${encodeURIComponent(id)}/versions`);
      const current = state.versions.find((item) => item.id === state.artifact.current_version_id) || state.versions[0];
      renderCatalog();
      renderDetailShell();
      await loadVersion(current.id);
    } catch (error) {
      $("detail").replaceChildren();
      const errorState = document.createElement("div");
      errorState.className = "empty-state";
      errorState.textContent = error.message;
      $("detail").append(errorState);
    }
  }

  function renderDetailShell() {
    const template = $("detail-template").content.cloneNode(true);
    $("detail").replaceChildren(template);
    $("detail-title").textContent = state.artifact.title;
    $("detail-kind").textContent = state.artifact.kind;
    $("detail-meta").textContent = `${state.artifact.slug} · ${state.versions.length} immutable version${state.versions.length === 1 ? "" : "s"}`;
    const select = $("version-select");
    state.versions.forEach((version) => {
      const option = document.createElement("option");
      option.value = version.id;
      option.textContent = `v${version.sequence}${version.id === state.artifact.current_version_id ? " · current" : ""}`;
      select.append(option);
    });
    select.addEventListener("change", () => loadVersion(select.value));
    $("compare-toggle").addEventListener("click", () => {
      const panel = $("diff-panel");
      if (panel && state.version.id !== state.artifact.current_version_id) {
        panel.hidden = !panel.hidden;
        renderCompareState();
      }
    });
    $("artifact-content").addEventListener("mouseup", captureSelection);
    $("artifact-content").addEventListener("keyup", captureSelection);
    $("comment-form").addEventListener("submit", submitComment);
  }

  async function loadVersion(versionId) {
    state.version = await api(`/api/versions/${encodeURIComponent(versionId)}`);
    const select = $("version-select");
    if (select) select.value = versionId;
    $("artifact-content").textContent = state.version.content;
    $("version-badge").textContent = `v${state.version.sequence}${versionId === state.artifact.current_version_id ? " · current" : ""}`;
    state.selection = null;
    $("selected-text").textContent = "None";
    $("comment-submit").disabled = true;
    $("selection-hint").textContent = "Select text above to attach a comment to this version.";
    renderCompareState();
    await loadComments();
  }

  function buildLineDiff(oldContent, newContent) {
    const oldLines = oldContent.split("\n");
    const newLines = newContent.split("\n");
    if (oldLines.length > 1000 || newLines.length > 1000) {
      return "Diff omitted: this version is too large for an in-browser comparison.";
    }
    const table = Array.from({ length: oldLines.length + 1 }, () => Array(newLines.length + 1).fill(0));
    for (let oldIndex = oldLines.length - 1; oldIndex >= 0; oldIndex -= 1) {
      for (let newIndex = newLines.length - 1; newIndex >= 0; newIndex -= 1) {
        table[oldIndex][newIndex] = oldLines[oldIndex] === newLines[newIndex]
          ? table[oldIndex + 1][newIndex + 1] + 1
          : Math.max(table[oldIndex + 1][newIndex], table[oldIndex][newIndex + 1]);
      }
    }
    const result = [];
    let oldIndex = 0;
    let newIndex = 0;
    while (oldIndex < oldLines.length || newIndex < newLines.length) {
      if (oldIndex < oldLines.length && newIndex < newLines.length && oldLines[oldIndex] === newLines[newIndex]) {
        result.push(` ${oldLines[oldIndex]}`);
        oldIndex += 1;
        newIndex += 1;
      } else if (newIndex < newLines.length && (oldIndex === oldLines.length || table[oldIndex][newIndex + 1] >= table[oldIndex + 1][newIndex])) {
        result.push(`+${newLines[newIndex]}`);
        newIndex += 1;
      } else {
        result.push(`-${oldLines[oldIndex]}`);
        oldIndex += 1;
      }
    }
    return result.join("\n");
  }

  function renderCompareState() {
    const toggle = $("compare-toggle");
    const panel = $("diff-panel");
    if (!toggle || !panel || !state.version) return;
    const isCurrent = state.version.id === state.artifact.current_version_id;
    toggle.disabled = isCurrent;
    toggle.textContent = isCurrent ? "Current version" : (panel.hidden ? "Compare current" : "Hide diff");
    if (isCurrent) panel.hidden = true;
    if (!isCurrent) {
      const current = state.versions.find((version) => version.id === state.artifact.current_version_id);
      $("diff-content").textContent = current ? buildLineDiff(state.version.content, current.content) : "Current version unavailable.";
    }
  }

  function captureSelection() {
    const content = $("artifact-content");
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.toString()) return;
    if (!content.contains(selection.anchorNode) || !content.contains(selection.focusNode)) return;
    const exact = selection.toString();
    const source = state.version.content;
    const start = source.indexOf(exact);
    if (start < 0 || source.indexOf(exact, start + 1) >= 0) {
      state.selection = { kind: "text", exact, prefix: "", suffix: "", ambiguous: true };
    } else {
      state.selection = {
        kind: "text",
        exact,
        prefix: source.slice(Math.max(0, start - 80), start),
        suffix: source.slice(start + exact.length, start + exact.length + 80),
      };
    }
    $("selected-text").textContent = exact.length > 180 ? `${exact.slice(0, 177)}…` : exact;
    $("comment-submit").disabled = Boolean(state.selection.ambiguous);
    $("selection-hint").textContent = state.selection.ambiguous ? "This selection occurs more than once; select a more specific passage." : "Selection attached to this version.";
  }

  async function loadComments() {
    const comments = await api(`/api/versions/${encodeURIComponent(state.version.id)}/comments`);
    const container = $("comments");
    container.replaceChildren();
    $("comment-count").textContent = String(comments.length);
    if (!comments.length) {
      const empty = document.createElement("div");
      empty.className = "no-comments";
      empty.textContent = "No feedback on this version.";
      container.append(empty);
      return;
    }
    comments.forEach((comment) => {
      const card = document.createElement("article");
      card.className = `comment${comment.status === "resolved" ? " resolved" : ""}`;
      const body = document.createElement("div");
      body.className = "comment-body";
      body.textContent = comment.body;
      const quote = document.createElement("blockquote");
      quote.className = "comment-quote";
      quote.textContent = comment.anchor?.exact || "No text quote";
      const footer = document.createElement("div");
      footer.className = "comment-footer";
      const status = document.createElement("span");
      status.textContent = comment.status;
      footer.append(status);
      if (comment.status !== "resolved") {
        const resolve = document.createElement("button");
        resolve.type = "button";
        resolve.textContent = "Resolve";
        resolve.addEventListener("click", () => changeCommentStatus(comment.id));
        footer.append(resolve);
      }
      card.append(body, quote, footer);
      container.append(card);
    });
  }

  async function submitComment(event) {
    event.preventDefault();
    const status = $("comment-status");
    if (!state.selection || state.selection.ambiguous) return;
    setStatus(status, "Saving…");
    try {
      await api(`/api/versions/${encodeURIComponent(state.version.id)}/comments`, {
        method: "POST",
        body: JSON.stringify({ artifact_id: state.artifact.id, body: $("comment-body").value, anchor: state.selection }),
      });
      $("comment-body").value = "";
      setStatus(status, "Feedback saved.", "success");
      await loadComments();
    } catch (error) {
      setStatus(status, error.message, "error");
    }
  }

  async function changeCommentStatus(commentId) {
    try {
      await api(`/api/comments/${encodeURIComponent(commentId)}/events`, {
        method: "POST",
        body: JSON.stringify({ status: "resolved", actor: "web" }),
      });
      await loadComments();
    } catch (error) {
      setStatus($("comment-status"), error.message, "error");
    }
  }

  $("reload").addEventListener("click", loadCatalog);
  $("search").addEventListener("input", renderCatalog);
  $("token-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = $("api-token").value;
    if (value) localStorage.setItem("oaa_api_token", value);
    else localStorage.removeItem("oaa_api_token");
    loadCatalog();
  });
  window.addEventListener("hashchange", () => {
    const id = routeId();
    if (id) loadArtifact(id, false);
  });
  loadCatalog().then(() => {
    const id = routeId();
    if (id) loadArtifact(id, false);
  });
})();
