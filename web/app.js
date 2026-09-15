(() => {
  "use strict";

  const state = {
    artifacts: [],
    artifact: null,
    versions: [],
    version: null,
    comments: [],
    selection: null,
    viewMode: localStorage.getItem("oaa_view_mode") || "list",
    pinnedOnly: false,
    commentsVisible: true,
  };
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
    if (!element) return;
    element.textContent = message || "";
    element.className = `status ${kind}`;
  }

  function shortText(value, limit = 180) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
  }

  function sortArtifacts(items) {
    const sort = $("sort-select")?.value || "updated";
    return [...items].sort((left, right) => {
      if (sort === "title") return left.title.localeCompare(right.title);
      if (sort === "pinned") return Number(right.pinned) - Number(left.pinned) || right.updated_at.localeCompare(left.updated_at);
      return Number(right.pinned) - Number(left.pinned) || right.updated_at.localeCompare(left.updated_at);
    });
  }

  function renderCatalog() {
    const list = $("artifact-list");
    if (!list) return;
    const query = $("search").value.trim().toLowerCase();
    const filtered = state.artifacts.filter((item) => {
      const matchesQuery = !query || `${item.title} ${item.slug} ${item.preview || ""}`.toLowerCase().includes(query);
      return matchesQuery && (!state.pinnedOnly || item.pinned);
    });
    const items = sortArtifacts(filtered);
    list.className = `artifact-list ${state.viewMode}-view`;
    list.replaceChildren();
    $("artifact-count").textContent = String(items.length);
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "no-comments catalog-empty";
      empty.textContent = state.pinnedOnly ? "No pinned artifacts." : (query ? "No matching artifacts." : "No artifacts yet.");
      list.append(empty);
      return;
    }
    items.forEach((item) => list.append(renderArtifactItem(item)));
  }

  function renderArtifactItem(item) {
    const card = document.createElement("article");
    card.className = `artifact-item${state.artifact?.id === item.id ? " active" : ""}${item.pinned ? " pinned" : ""}`;
    const open = document.createElement("button");
    open.className = "artifact-open";
    open.type = "button";
    open.addEventListener("click", () => loadArtifact(item.id));

    const head = document.createElement("div");
    head.className = "artifact-item-head";
    const title = document.createElement("strong");
    title.textContent = item.title;
    const type = document.createElement("span");
    type.className = "kind-badge";
    type.textContent = item.kind;
    head.append(title, type);

    const meta = document.createElement("span");
    meta.className = "artifact-meta";
    meta.textContent = `v${item.current_version_sequence || 1} · ${item.comment_count || 0} comment${item.comment_count === 1 ? "" : "s"}`;
    const preview = document.createElement("p");
    preview.className = "artifact-preview";
    preview.textContent = shortText(item.preview || "No preview available.", 220);
    const footer = document.createElement("span");
    footer.className = "artifact-open-hint";
    footer.textContent = "Open workspace →";
    open.append(head, meta, preview, footer);

    const pin = document.createElement("button");
    pin.className = "pin-button";
    pin.type = "button";
    pin.setAttribute("aria-pressed", String(Boolean(item.pinned)));
    pin.title = item.pinned ? "Unpin artifact" : "Pin artifact";
    pin.textContent = item.pinned ? "★" : "☆";
    pin.addEventListener("click", async (event) => {
      event.stopPropagation();
      await setPinned(item.id, !item.pinned);
    });

    card.append(open, pin);
    return card;
  }

  async function setPinned(artifactId, pinned) {
    try {
      const updated = await api(`/api/artifacts/${encodeURIComponent(artifactId)}/${pinned ? "pin" : "unpin"}`, {
        method: "POST",
        body: "{}",
      });
      state.artifacts = state.artifacts.map((item) => item.id === artifactId ? { ...item, ...updated } : item);
      if (state.artifact?.id === artifactId) state.artifact = { ...state.artifact, ...updated };
      renderCatalog();
      renderDetailPin();
    } catch (error) {
      setStatus($("catalog-status"), error.message, "error");
    }
  }

  async function loadCatalog() {
    setStatus($("catalog-status"), "Loading catalog…");
    try {
      state.artifacts = await api("/api/artifacts");
      setStatus($("catalog-status"), state.artifacts.length ? "" : "Catalog is empty.");
      renderCatalog();
    } catch (error) {
      setStatus($("catalog-status"), error.message, "error");
    }
  }

  function routeId() {
    const match = location.hash.match(/^#\/artifact\/([^/]+)$/);
    return match ? decodeURIComponent(match[1]) : null;
  }

  function renderEmptyState() {
    document.body.classList.remove("detail-mode");
    state.artifact = null;
    state.versions = [];
    state.version = null;
    state.comments = [];
    $("detail").innerHTML = `
      <div class="empty-state">
        <div class="empty-mark">◎</div>
        <p class="eyebrow">READY FOR REVIEW</p>
        <h2>Pick an artifact to open its workspace</h2>
        <p>Browse a preview, open immutable versions, pin important work, and leave highlighted feedback in one place.</p>
        <button id="empty-new-artifact" class="button" type="button">Create first artifact</button>
      </div>`;
    $("empty-new-artifact").addEventListener("click", openCreateDialog);
    renderCatalog();
  }

  async function loadArtifact(id, updateHash = true) {
    if (updateHash) {
      const nextHash = `#/artifact/${encodeURIComponent(id)}`;
      if (location.hash !== nextHash) {
        location.hash = nextHash;
        return;
      }
    }
    try {
      state.artifact = await api(`/api/artifacts/${encodeURIComponent(id)}`);
      state.versions = await api(`/api/artifacts/${encodeURIComponent(id)}/versions`);
      const current = state.versions.find((item) => item.id === state.artifact.current_version_id) || state.versions[0];
      document.body.classList.add("detail-mode");
      renderCatalog();
      renderDetailShell();
      await loadVersion(current.id);
    } catch (error) {
      $("detail").replaceChildren();
      const errorState = document.createElement("div");
      errorState.className = "empty-state error-state";
      errorState.textContent = error.message;
      $("detail").append(errorState);
    }
  }

  function renderDetailPin() {
    const button = $("detail-pin");
    if (!button || !state.artifact) return;
    button.setAttribute("aria-pressed", String(Boolean(state.artifact.pinned)));
    button.textContent = state.artifact.pinned ? "★ Pinned" : "☆ Pin";
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
    renderDetailPin();
    select.addEventListener("change", () => loadVersion(select.value));
    $("detail-pin").addEventListener("click", () => setPinned(state.artifact.id, !state.artifact.pinned));
    $("back-catalog").addEventListener("click", () => {
      history.replaceState(null, "", location.pathname + location.search);
      renderEmptyState();
    });
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
    $("comments-toggle").addEventListener("click", () => {
      state.commentsVisible = !state.commentsVisible;
      renderCommentVisibility();
    });
    $("new-version").addEventListener("click", openVersionEditor);
    $("cancel-version").addEventListener("click", closeVersionEditor);
    $("new-version-form").addEventListener("submit", submitVersion);
    renderCommentVisibility();
  }

  async function loadVersion(versionId) {
    state.version = await api(`/api/versions/${encodeURIComponent(versionId)}`);
    const select = $("version-select");
    if (select) select.value = versionId;
    $("version-badge").textContent = `v${state.version.sequence}${versionId === state.artifact.current_version_id ? " · current" : ""}`;
    state.selection = null;
    $("selected-text").textContent = "None";
    $("comment-submit").disabled = true;
    $("selection-hint").textContent = "Select text above to attach a highlighted comment to this version.";
    renderSourceContent([]);
    renderCompareState();
    await loadComments();
  }

  function buildLineDiff(oldContent, newContent) {
    const oldLines = oldContent.split("\n");
    const newLines = newContent.split("\n");
    if (oldLines.length > 1000 || newLines.length > 1000) return "Diff omitted: this version is too large for an in-browser comparison.";
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
        result.push(` ${oldLines[oldIndex]}`); oldIndex += 1; newIndex += 1;
      } else if (newIndex < newLines.length && (oldIndex === oldLines.length || table[oldIndex][newIndex + 1] >= table[oldIndex + 1][newIndex])) {
        result.push(`+${newLines[newIndex]}`); newIndex += 1;
      } else { result.push(`-${oldLines[oldIndex]}`); oldIndex += 1; }
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

  function anchorStart(source, anchor) {
    const exact = anchor?.exact;
    if (!exact) return -1;
    const candidates = [];
    let cursor = 0;
    while (true) {
      const index = source.indexOf(exact, cursor);
      if (index < 0) break;
      candidates.push(index);
      cursor = index + Math.max(exact.length, 1);
    }
    if (candidates.length === 1) return candidates[0];
    const contextual = candidates.find((index) => {
      const prefix = anchor.prefix || "";
      const suffix = anchor.suffix || "";
      return (!prefix || source.slice(Math.max(0, index - prefix.length), index).endsWith(prefix))
        && (!suffix || source.slice(index + exact.length, index + exact.length + suffix.length).startsWith(suffix));
    });
    return contextual === undefined ? -1 : contextual;
  }

  function renderSourceContent(comments) {
    const content = $("artifact-content");
    if (!content || !state.version) return;
    content.replaceChildren();
    const ranges = comments.map((comment) => {
      const start = anchorStart(state.version.content, comment.anchor);
      return start < 0 ? null : { start, end: start + comment.anchor.exact.length, comment };
    }).filter(Boolean).sort((left, right) => left.start - right.start);
    let cursor = 0;
    ranges.forEach((range) => {
      if (range.start < cursor) return;
      content.append(document.createTextNode(state.version.content.slice(cursor, range.start)));
      const mark = document.createElement("mark");
      mark.className = "highlighted-quote";
      mark.dataset.commentId = range.comment.id;
      mark.title = "Open comment";
      mark.textContent = state.version.content.slice(range.start, range.end);
      mark.addEventListener("click", () => focusComment(range.comment.id));
      content.append(mark);
      cursor = range.end;
    });
    content.append(document.createTextNode(state.version.content.slice(cursor)));
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
      state.selection = { kind: "text", exact, prefix: source.slice(Math.max(0, start - 80), start), suffix: source.slice(start + exact.length, start + exact.length + 80) };
    }
    $("selected-text").textContent = exact.length > 180 ? `${exact.slice(0, 177)}…` : exact;
    $("comment-submit").disabled = Boolean(state.selection.ambiguous);
    $("selection-hint").textContent = state.selection.ambiguous
      ? "This selection occurs more than once; select a more specific passage."
      : "Selection attached to this version. Submit to keep it highlighted.";
  }

  async function loadComments() {
    state.comments = await api(`/api/versions/${encodeURIComponent(state.version.id)}/comments`);
    const container = $("comments");
    container.replaceChildren();
    $("comment-count").textContent = String(state.comments.length);
    if (!state.comments.length) {
      const empty = document.createElement("div");
      empty.className = "no-comments";
      empty.textContent = "No comments on this version.";
      container.append(empty);
    } else {
      state.comments.forEach((comment) => container.append(renderComment(comment)));
    }
    renderSourceContent(state.comments);
    renderCommentVisibility();
  }

  function renderComment(comment) {
    const card = document.createElement("article");
    card.id = `comment-${comment.id}`;
    card.className = `comment${comment.status === "resolved" ? " resolved" : ""}`;
    const body = document.createElement("div");
    body.className = "comment-body";
    body.textContent = comment.body;
    const quote = document.createElement("blockquote");
    quote.className = "comment-quote";
    quote.textContent = comment.anchor?.exact || "No text quote";
    quote.addEventListener("click", () => focusComment(comment.id));
    const footer = document.createElement("div");
    footer.className = "comment-footer";
    const status = document.createElement("span");
    status.className = "comment-status-badge";
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
    return card;
  }

  function focusComment(commentId) {
    const card = $(`comment-${commentId}`);
    if (!card) return;
    if (!state.commentsVisible) {
      state.commentsVisible = true;
      renderCommentVisibility();
    }
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    card.classList.add("comment-focus");
    window.setTimeout(() => card.classList.remove("comment-focus"), 1200);
  }

  function renderCommentVisibility() {
    const container = $("comments");
    const toggle = $("comments-toggle");
    if (!container || !toggle) return;
    container.hidden = !state.commentsVisible;
    toggle.setAttribute("aria-expanded", String(state.commentsVisible));
    toggle.textContent = state.commentsVisible ? "Hide comments" : "Show comments";
  }

  async function submitComment(event) {
    event.preventDefault();
    const body = $("comment-body").value.trim();
    const status = $("comment-status");
    if (!body || !state.selection || state.selection.ambiguous) return;
    setStatus(status, "Saving…");
    try {
      await api(`/api/versions/${encodeURIComponent(state.version.id)}/comments`, {
        method: "POST",
        body: JSON.stringify({ artifact_id: state.artifact.id, body, anchor: state.selection }),
      });
      $("comment-body").value = "";
      state.selection = null;
      $("selected-text").textContent = "None";
      $("comment-submit").disabled = true;
      setStatus(status, "Comment saved and highlighted.", "success");
      await loadComments();
      await loadCatalog();
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
      await loadCatalog();
    } catch (error) {
      setStatus($("comment-status"), error.message, "error");
    }
  }

  function openVersionEditor() {
    const current = state.versions.find((version) => version.id === state.artifact.current_version_id) || state.version;
    $("version-content").value = current.content;
    $("version-summary").value = "";
    $("version-status").textContent = "";
    $("version-editor").hidden = false;
    $("version-content").focus();
  }

  function closeVersionEditor() {
    $("version-editor").hidden = true;
  }

  async function submitVersion(event) {
    event.preventDefault();
    const status = $("version-status");
    const content = $("version-content").value;
    if (!content.trim()) return;
    setStatus(status, "Publishing…");
    try {
      await api(`/api/artifacts/${encodeURIComponent(state.artifact.id)}/versions`, {
        method: "POST",
        body: JSON.stringify({
          content,
          created_by: "web",
          expected_current_version_id: state.artifact.current_version_id,
          change_summary: $("version-summary").value.trim(),
        }),
      });
      closeVersionEditor();
      await loadCatalog();
      await loadArtifact(state.artifact.id);
    } catch (error) {
      setStatus(status, error.message, "error");
    }
  }

  function openCreateDialog() {
    const dialog = $("new-artifact-dialog");
    if (!dialog.open) dialog.showModal();
    $("new-artifact-title").focus();
  }

  function closeCreateDialog() {
    const dialog = $("new-artifact-dialog");
    if (dialog.open) dialog.close();
    $("new-artifact-status").textContent = "";
  }

  async function submitNewArtifact(event) {
    event.preventDefault();
    const status = $("new-artifact-status");
    setStatus(status, "Creating…");
    try {
      const artifact = await api("/api/artifacts", {
        method: "POST",
        body: JSON.stringify({
          title: $("new-artifact-title").value.trim(),
          kind: $("new-artifact-kind").value,
          content: $("new-artifact-content").value,
          created_by: "web",
        }),
      });
      closeCreateDialog();
      $("new-artifact-form").reset();
      await loadCatalog();
      await loadArtifact(artifact.id);
    } catch (error) {
      setStatus(status, error.message, "error");
    }
  }

  function setViewMode(mode) {
    state.viewMode = mode;
    localStorage.setItem("oaa_view_mode", mode);
    ["list", "cards"].forEach((name) => {
      const button = $(`${name}-view`);
      const active = name === mode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    renderCatalog();
  }

  $("reload").addEventListener("click", loadCatalog);
  $("search").addEventListener("input", renderCatalog);
  $("sort-select").addEventListener("change", renderCatalog);
  $("list-view").addEventListener("click", () => setViewMode("list"));
  $("cards-view").addEventListener("click", () => setViewMode("cards"));
  $("pinned-filter").addEventListener("click", () => {
    state.pinnedOnly = !state.pinnedOnly;
    $("pinned-filter").setAttribute("aria-pressed", String(state.pinnedOnly));
    $("pinned-filter").classList.toggle("active", state.pinnedOnly);
    renderCatalog();
  });
  $("new-artifact").addEventListener("click", openCreateDialog);
  $("empty-new-artifact").addEventListener("click", openCreateDialog);
  $("close-new-artifact").addEventListener("click", closeCreateDialog);
  $("cancel-new-artifact").addEventListener("click", closeCreateDialog);
  $("new-artifact-form").addEventListener("submit", submitNewArtifact);
  $("new-artifact-dialog").addEventListener("click", (event) => {
    if (event.target === $("new-artifact-dialog")) closeCreateDialog();
  });
  window.addEventListener("hashchange", () => {
    const id = routeId();
    if (id) loadArtifact(id, false);
    else renderEmptyState();
  });
  setViewMode(state.viewMode);
  loadCatalog().then(() => {
    const id = routeId();
    if (id) loadArtifact(id, false);
  });
})();
