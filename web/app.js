(() => {
  "use strict";

  const state = {
    catalog: {
      scope: "all",
      view: localStorage.getItem("oaa_view_mode") || "grid",
      query: "",
      snapshot: null,
      nextCursor: null,
      loading: false,
      generation: 0,
      controller: null,
      appendLoading: false,
    },
    items: [],
    me: null,
    artifact: null,
    versions: [],
    version: null,
    presentation: null,
    comments: [],
    commentsVisible: true,
    sourceMode: false,
    historyOpen: false,
    selection: null,
    composer: null,
  };

  const $ = (id) => document.getElementById(id);
  const apiToken = () => localStorage.getItem("oaa_api_token") || "";

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function button(label, className = "button secondary", type = "button") {
    const element = node("button", className, label);
    element.type = type;
    return element;
  }

  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (apiToken()) headers.Authorization = `Bearer ${apiToken()}`;
    const response = await fetch(path, { ...options, headers, cache: "no-store" });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("json") ? await response.json() : await response.text();
    if (!response.ok) throw new Error(payload.message || `${response.status} ${response.statusText}`);
    return payload;
  }

  function setStatus(message, kind = "muted") {
    const element = $("catalog-status");
    if (!element) return;
    element.textContent = message || "";
    element.className = `status ${kind}`;
  }

  function formatDate(value) {
    if (!value) return "Unknown time";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(date);
  }

  function formatDateTime(value) {
    if (!value) return "Unknown time";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function formatGroup(value) {
    if (!value) return "Earlier";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Earlier";
    const now = new Date();
    if (date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate()) return "Today";
    if (date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth()) return "This month";
    return new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(date);
  }

  function persistCatalogState() {
    sessionStorage.setItem("oaa_catalog_state", JSON.stringify({
      scope: state.catalog.scope,
      view: state.catalog.view,
      query: state.catalog.query,
    }));
  }

  function restoreCatalogState() {
    try {
      const stored = JSON.parse(sessionStorage.getItem("oaa_catalog_state") || "null");
      if (stored && typeof stored === "object") {
        if (["all", "yours", "shared"].includes(stored.scope)) state.catalog.scope = stored.scope;
        if (["grid", "list"].includes(stored.view)) state.catalog.view = stored.view;
        if (typeof stored.query === "string") state.catalog.query = stored.query;
      }
    } catch (_) {
      sessionStorage.removeItem("oaa_catalog_state");
    }
  }

  function updateUrlForCatalog(replace = false) {
    persistCatalogState();
    const params = new URLSearchParams();
    if (state.catalog.scope !== "all") params.set("tab", state.catalog.scope);
    if (state.catalog.view !== "grid") params.set("view", state.catalog.view);
    if (state.catalog.query) params.set("q", state.catalog.query);
    const hash = `#/${params.toString() ? `?${params.toString()}` : ""}`;
    if (replace) history.replaceState({ catalog: true }, "", `${location.pathname}${location.search}${hash}`);
    else history.pushState({ catalog: true }, "", `${location.pathname}${location.search}${hash}`);
  }

  function safeUrl(value) {
    try {
      const url = new URL(value, location.origin);
      return ["http:", "https:", "mailto:"].includes(url.protocol) ? url.href : null;
    } catch (_) {
      return null;
    }
  }

  function anchorExact(anchor) {
    return anchor?.quote?.exact || anchor?.exact || "";
  }

  function appendInline(parent, value) {
    const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*|\[[^\]]+\]\([^\)]+\))/g;
    let cursor = 0;
    for (const match of value.matchAll(pattern)) {
      const start = match.index || 0;
      if (start > cursor) parent.append(document.createTextNode(value.slice(cursor, start)));
      const token = match[0];
      if (token.startsWith("**")) {
        parent.append(node("strong", "", token.slice(2, -2)));
      } else if (token.startsWith("`")) {
        parent.append(node("code", "inline-code", token.slice(1, -1)));
      } else if (token.startsWith("*")) {
        parent.append(node("em", "", token.slice(1, -1)));
      } else {
        const link = token.match(/^\[([^\]]+)\]\(([^\)]+)\)$/);
        const href = link && safeUrl(link[2]);
        if (href) {
          const anchor = node("a", "", link[1]);
          anchor.href = href;
          anchor.target = "_blank";
          anchor.rel = "noreferrer noopener";
          parent.append(anchor);
        } else {
          parent.append(document.createTextNode(token));
        }
      }
      cursor = start + token.length;
    }
    if (cursor < value.length) parent.append(document.createTextNode(value.slice(cursor)));
  }

  function renderMarkdown(parent, source) {
    const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) { index += 1; continue; }
      if (/^```/.test(line)) {
        const language = line.slice(3).trim();
        const codeLines = [];
        index += 1;
        while (index < lines.length && !/^```/.test(lines[index])) codeLines.push(lines[index++]);
        if (index < lines.length) index += 1;
        const pre = node("pre", "markdown-code");
        if (language) pre.dataset.language = language;
        pre.textContent = codeLines.join("\n");
        parent.append(pre);
        continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) {
        const h = node(`h${heading[1].length}`, "markdown-heading");
        appendInline(h, heading[2]);
        parent.append(h);
        index += 1;
        continue;
      }
      if (/^[-*+]\s+/.test(line)) {
        const list = node("ul", "markdown-list");
        while (index < lines.length && /^[-*+]\s+/.test(lines[index])) {
          const item = node("li");
          appendInline(item, lines[index].replace(/^[-*+]\s+/, ""));
          list.append(item);
          index += 1;
        }
        parent.append(list);
        continue;
      }
      if (/^>\s?/.test(line)) {
        const quote = node("blockquote", "markdown-quote");
        appendInline(quote, line.replace(/^>\s?/, ""));
        parent.append(quote);
        index += 1;
        continue;
      }
      const paragraphLines = [line];
      index += 1;
      while (index < lines.length && lines[index].trim() && !/^(#{1,6})\s|^```|^[-*+]\s+|^>\s?/.test(lines[index])) paragraphLines.push(lines[index++]);
      const paragraph = node("p", "markdown-paragraph");
      appendInline(paragraph, paragraphLines.join(" "));
      parent.append(paragraph);
    }
  }

  function sanitizeHtmlForSandbox(source, versionId) {
    const parsed = new DOMParser().parseFromString(String(source || ""), "text/html");
    const trusted = document.implementation.createHTMLDocument("");
    const allowedTags = new Set([
      "a", "abbr", "article", "aside", "b", "blockquote", "br", "caption", "code", "col", "colgroup",
      "dd", "del", "details", "div", "dl", "dt", "em", "figcaption", "figure", "footer", "h1", "h2",
      "h3", "h4", "h5", "h6", "header", "hr", "i", "img", "kbd", "li", "main", "mark", "nav", "ol",
      "p", "pre", "q", "s", "samp", "section", "small", "span", "strong", "sub", "summary", "sup", "table",
      "tbody", "td", "tfoot", "th", "thead", "time", "tr", "u", "ul", "var",
    ]);
    const safeAttributes = new Set(["class", "id", "title", "role", "style", "colspan", "rowspan", "scope", "alt", "width", "height"]);
    const copy = (input, outputParent) => {
      if (input.nodeType === Node.TEXT_NODE) {
        outputParent.append(trusted.createTextNode(input.nodeValue || ""));
        return;
      }
      if (input.nodeType !== Node.ELEMENT_NODE) return;
      const tag = input.localName.toLowerCase();
      if (!allowedTags.has(tag)) return;
      const output = trusted.createElement(tag);
      [...input.attributes].forEach((attribute) => {
        const name = attribute.name.toLowerCase();
        if (/^on/.test(name)) return;
        if (name === "src" && tag === "img" && /^data:image\/(?:png|gif|jpeg|webp);base64,/i.test(attribute.value)) {
          output.setAttribute("src", attribute.value);
        } else if (name === "href" && attribute.value.trim().startsWith("#")) {
          output.setAttribute("href", attribute.value.trim());
        } else if (safeAttributes.has(name) || name.startsWith("aria-") || name.startsWith("data-")) {
          output.setAttribute(attribute.name, attribute.value);
        }
      });
      outputParent.append(output);
      [...input.childNodes].forEach((child) => copy(child, output));
    };
    [...parsed.body.childNodes].forEach((child) => copy(child, trusted.body));
    const nonce = "oaa-bridge-v1";
    const csp = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'self'; base-uri 'none'; form-action 'none'";
    const cspMeta = trusted.createElement("meta");
    cspMeta.httpEquiv = "Content-Security-Policy";
    cspMeta.content = csp;
    const viewport = trusted.createElement("meta");
    viewport.name = "viewport";
    viewport.content = "width=device-width, initial-scale=1";
    const style = trusted.createElement("style");
    style.textContent = "html,body{overflow:hidden}body{font:16px/1.6 system-ui,sans-serif;margin:1.4rem;color:#edf5f6;background:#101820}img{max-width:100%;height:auto}a{color:#8bdaca}";
    trusted.head.append(cspMeta, viewport, style);
    const bridge = trusted.createElement("script");
    bridge.src = "/sandbox-bridge.js";
    bridge.nonce = nonce;
    bridge.dataset.nonce = nonce;
    bridge.dataset.versionId = String(versionId);
    trusted.body.append(bridge);
    return `<!doctype html>${trusted.documentElement.outerHTML}`;
  }

  function highlightExact(parent, exact, commentId) {
    if (!exact || !parent) return false;
    const walker = document.createTreeWalker(parent, NodeFilter.SHOW_TEXT);
    const matches = [];
    let current;
    while ((current = walker.nextNode())) {
      if (current.parentElement && current.parentElement.closest("mark")) continue;
      const position = current.nodeValue.indexOf(exact);
      if (position >= 0) matches.push({ node: current, position });
    }
    const match = matches[0];
    if (!match) return false;
    const range = document.createRange();
    range.setStart(match.node, match.position);
    range.setEnd(match.node, match.position + exact.length);
    const mark = node("mark", "highlighted-quote");
    mark.dataset.commentId = commentId || "";
    mark.title = "Open comment";
    mark.append(range.extractContents());
    range.insertNode(mark);
    mark.addEventListener("click", () => focusComment(commentId));
    return true;
  }

  function renderPresentation(parent, presentation, comments = []) {
    parent.replaceChildren();
    parent.className = "presentation-panel";
    if (!presentation) {
      parent.append(node("p", "status", "Presentation unavailable."));
      return;
    }
    if (state.sourceMode || presentation.mode === "escaped-source" || presentation.mode === "unsupported") {
      const label = node("p", "presentation-label", presentation.mode === "unsupported" ? "Unsupported presentation · source fallback" : "Source view");
      const source = node("pre", "source-fallback");
      source.tabIndex = 0;
      source.textContent = presentation.content || "";
      parent.append(label, source);
      comments.forEach((comment) => highlightExact(source, anchorExact(comment.anchor), comment.id));
      return;
    }
    if (presentation.mode === "rendered") {
      const documentView = node("article", "rendered-markdown");
      renderMarkdown(documentView, presentation.content);
      parent.append(documentView);
      comments.forEach((comment) => highlightExact(documentView, anchorExact(comment.anchor), comment.id));
      return;
    }
    if (presentation.mode === "isolated-html") {
      const frame = document.createElement("iframe");
      frame.className = "html-frame";
      frame.title = state.artifact ? `${state.artifact.title} rendered HTML` : "Rendered HTML artifact";
      frame.setAttribute("sandbox", "allow-scripts");
      frame.setAttribute("scrolling", "no");
      frame.setAttribute("height", "360");
      frame.dataset.versionId = presentation.version_id;
      frame.referrerPolicy = "no-referrer";
      frame.srcdoc = sanitizeHtmlForSandbox(presentation.content, presentation.version_id);
      frame.addEventListener("load", () => {
        comments.forEach((comment) => {
          // The opaque frame owns its DOM; markers are represented in the side thread.
          if (anchorExact(comment.anchor)) frame.dataset.commentCount = String(comments.length);
        });
      });
      parent.append(frame);
      return;
    }
    const source = node("pre", "source-fallback");
    source.textContent = presentation.content || "";
    parent.append(source);
  }

  function previewMount(item) {
    const mount = node("button", "card-preview");
    mount.type = "button";
    mount.setAttribute("aria-label", `Open “${item.title}” preview`);
    mount.dataset.previewVersion = item.preview?.version_id || "";
    mount.addEventListener("click", () => openArtifact(item.id));
    const excerpt = node("span", "mini-source");
    excerpt.textContent = `${item.preview?.excerpt || ""}${item.preview?.truncated ? "…" : ""}`;
    mount.append(excerpt);
    return mount;
  }

  function catalogItem(item) {
    const article = node("article", `artifact-card${item.pinned ? " is-pinned" : ""}`);
    const open = node("button", "artifact-card-open");
    open.type = "button";
    open.setAttribute("aria-label", `Open “${item.title}” (${item.kind}, version ${item.current_version_sequence || 1})`);
    open.addEventListener("click", () => openArtifact(item.id));
    const body = node("div", "artifact-card-body");
    const title = node("h3", "artifact-title", item.title);
    const kind = node("span", "kind-badge", item.kind);
    const titleLine = node("div", "artifact-title-line");
    titleLine.append(title, kind);
    body.append(titleLine);
    const activity = item.activity || { kind: "Edited", time: item.content_updated_at };
    body.append(node("p", "artifact-activity", `${activity.kind} ${formatDateTime(activity.time)}`));
    const footer = node("div", "artifact-card-footer");
    footer.append(node("span", "artifact-comments", `${item.comment_count || 0} comments`));
    footer.append(node("span", "artifact-version", `v${item.current_version_sequence || 1}`));
    body.append(footer);
    open.append(body);
    const pin = button(item.pinned ? "★" : "☆", "pin-button");
    pin.title = item.pinned ? "Unpin artifact" : "Pin artifact";
    pin.setAttribute("aria-label", `${item.pinned ? "Unpin" : "Pin"} “${item.title}”`);
    pin.setAttribute("aria-pressed", String(Boolean(item.pinned)));
    pin.addEventListener("click", async (event) => {
      event.stopPropagation();
      await togglePin(item.id, !item.pinned);
    });
    article.append(previewMount(item), open, pin);
    return article;
  }

  function renderCatalog() {
    const list = $("artifact-list");
    if (!list) return;
    list.className = `artifact-grid ${state.catalog.view}-view`;
    list.replaceChildren();
    const items = state.items;
    $("artifact-count").textContent = String(items.length);
    if (!items.length) {
      const empty = node("section", "catalog-empty");
      empty.append(node("div", "empty-mark", "✦"));
      empty.append(node("h2", "", state.catalog.scope === "pinned" ? "No pinned artifacts" : (state.catalog.query ? "No matching artifacts" : "Your workspace is ready")));
      empty.append(node("p", "", state.catalog.query ? "Try a different search." : "Create an artifact to start building your workspace."));
      const create = button("New artifact", "button primary");
      create.addEventListener("click", openCreateDialog);
      empty.append(create);
      list.append(empty);
    } else if (state.catalog.view === "list") {
      renderListGroups(list, items);
    } else {
      const pinned = items.filter((item) => item.pinned);
      const rest = items.filter((item) => !item.pinned);
      if (pinned.length) appendCatalogGroup(list, "Pinned", pinned, "pinned-group");
      rest.forEach((item) => list.append(catalogItem(item)));
    }
    $("catalog-more").hidden = !state.catalog.nextCursor;
    document.querySelectorAll(".scope-tab").forEach((tab) => {
      const active = tab.dataset.scope === state.catalog.scope;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    document.querySelectorAll(".view-button").forEach((control) => {
      const active = control.dataset.view === state.catalog.view;
      control.classList.toggle("active", active);
      control.setAttribute("aria-pressed", String(active));
    });
  }

  function appendCatalogGroup(parent, label, items, className = "") {
    const section = node("section", `catalog-group ${className}`);
    section.append(node("h2", "group-heading", label));
    const grid = node("div", "group-grid");
    items.forEach((item) => grid.append(catalogItem(item)));
    section.append(grid);
    parent.append(section);
  }

  function renderListGroup(parent, label, items, className = "") {
    const section = node("section", `catalog-group list-group ${className}`);
    section.append(node("h2", "group-heading", label));
    const rows = node("div", "artifact-list-rows");
    items.forEach((item) => {
      const row = node("article", "artifact-row");
      row.tabIndex = 0;
      row.addEventListener("click", () => openArtifact(item.id));
      row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openArtifact(item.id); } });
      row.append(node("div", `row-kind kind-${item.kind}`, item.kind.slice(0, 1).toUpperCase()));
      const info = node("div", "row-info");
      info.append(node("strong", "", item.title));
      const activity = item.activity || { kind: "Edited", time: item.content_updated_at };
      info.append(node("span", "artifact-activity", `${activity.kind} ${formatDateTime(activity.time)}`));
      row.append(info);
      row.append(node("span", "row-meta", `v${item.current_version_sequence || 1}`));
      row.append(node("span", "row-pin", item.pinned ? "★" : ""));
      rows.append(row);
    });
    section.append(rows);
    parent.append(section);
  }

  function renderListGroups(parent, items) {
    const pinned = items.filter((item) => item.pinned);
    const rest = items.filter((item) => !item.pinned);
    if (pinned.length) renderListGroup(parent, "Pinned", pinned, "pinned-group");
    const groups = new Map();
    rest.forEach((item) => {
      const key = formatGroup(item.content_updated_at || item.updated_at);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    });
    groups.forEach((group, label) => renderListGroup(parent, label, group));
  }

  async function loadCatalog(append = false) {
    if (append && (state.catalog.appendLoading || !state.catalog.nextCursor)) return;
    if (!append && state.catalog.controller) state.catalog.controller.abort();
    const generation = append ? state.catalog.generation : ++state.catalog.generation;
    const controller = new AbortController();
    if (append) state.catalog.appendLoading = true;
    else {
      state.catalog.controller = controller;
      state.catalog.loading = true;
    }
    const identity = `${state.catalog.scope}\n${state.catalog.view}\n${state.catalog.query}`;
    const cursor = append ? state.catalog.nextCursor : null;
    setStatus(append ? "Loading more…" : "Loading artifacts…");
    const params = new URLSearchParams({ scope: state.catalog.scope, view: state.catalog.view, limit: "40" });
    if (state.catalog.query) params.set("q", state.catalog.query);
    if (cursor) params.set("cursor", cursor);
    try {
      const result = await api(`/api/artifacts?${params.toString()}`, { signal: controller.signal });
      const currentIdentity = `${state.catalog.scope}\n${state.catalog.view}\n${state.catalog.query}`;
      if (generation !== state.catalog.generation || identity !== currentIdentity || (append && cursor !== state.catalog.nextCursor)) return;
      state.items = append ? state.items.concat(result.items || []) : (result.items || []);
      state.catalog.snapshot = result.snapshot;
      state.catalog.nextCursor = result.next_cursor;
      setStatus(state.items.length ? "" : "No artifacts in this view.");
      renderCatalog();
    } catch (error) {
      if (error.name !== "AbortError" && generation === state.catalog.generation) setStatus(error.message, "error");
    } finally {
      if (append) state.catalog.appendLoading = false;
      else if (generation === state.catalog.generation) {
        state.catalog.loading = false;
        state.catalog.controller = null;
      }
    }
  }

  async function togglePin(artifactId, pinned) {
    try {
      await api(`/api/artifacts/${encodeURIComponent(artifactId)}/${pinned ? "pin" : "unpin"}`, { method: "POST", body: "{}" });
      await loadCatalog();
      if (state.artifact?.id === artifactId) {
        state.artifact.pinned = pinned;
        renderWorkspaceHeader();
      }
    } catch (error) {
      setStatus(error.message, "error");
    }
  }

  function route() {
    const match = location.hash.match(/^#\/artifact\/([^/?]+)(?:\?version=([^&]+))?$/);
    if (match) {
      showWorkspace(decodeURIComponent(match[1]), match[2] ? decodeURIComponent(match[2]) : null);
    } else {
      showCatalog();
    }
  }

  function openArtifact(artifactId, versionId = null) {
    persistCatalogState();
    const suffix = versionId ? `?version=${encodeURIComponent(versionId)}` : "";
    history.pushState({ artifact: artifactId, version: versionId, catalog: state.catalog }, "", `#/artifact/${encodeURIComponent(artifactId)}${suffix}`);
    route();
  }

  function showCatalog() {
    $("workspace").hidden = true;
    $("catalog").hidden = false;
    document.body.classList.remove("workspace-active");
    restoreCatalogState();
    $("catalog-search").value = state.catalog.query;
    loadCatalog();
  }

  function goHome() {
    updateUrlForCatalog(false);
    route();
  }

  function workspaceButton(label, className = "button secondary") {
    return button(label, className);
  }

  function showWorkspace(artifactId, versionId) {
    $("catalog").hidden = true;
    $("workspace").hidden = false;
    document.body.classList.add("workspace-active");
    if (state.artifact?.id === artifactId && state.version?.id === versionId) return;
    state.artifact = null;
    state.version = null;
    state.presentation = null;
    state.comments = [];
    state.versions = [];
    const workspace = $("workspace");
    workspace.replaceChildren(node("div", "workspace-loading", "Opening artifact workspace…"));
    Promise.all([
      api(`/api/artifacts/${encodeURIComponent(artifactId)}`),
      api(`/api/artifacts/${encodeURIComponent(artifactId)}/versions`),
    ]).then(async ([artifact, versions]) => {
      state.artifact = artifact;
      state.versions = versions;
      const selected = versionId ? versions.find((item) => item.id === versionId) : versions.find((item) => item.id === artifact.current_version_id) || versions[0];
      if (!selected) throw new Error("Artifact has no version.");
      state.version = await api(`/api/versions/${encodeURIComponent(selected.id)}`);
      state.presentation = await api(`/api/versions/${encodeURIComponent(selected.id)}/presentation`);
      state.comments = await api(`/api/versions/${encodeURIComponent(selected.id)}/comments`);
      await api(`/api/artifacts/${encodeURIComponent(artifactId)}/visit`, { method: "POST", body: "{}" });
      renderWorkspace();
    }).catch((error) => {
      workspace.replaceChildren(node("div", "workspace-error", error.message));
    });
  }

  function renderWorkspace() {
    const workspace = $("workspace");
    workspace.replaceChildren();
    const header = node("header", "workspace-header");
    const trail = node("div", "workspace-trail");
    trail.append(node("span", "breadcrumb", "Artifacts"), node("span", "breadcrumb-separator", "/"));
    const titleButton = workspaceButton(state.artifact.title, "title-menu-trigger");
    titleButton.addEventListener("click", toggleTitleMenu);
    trail.append(titleButton);
    header.append(trail);
    const actions = node("div", "workspace-actions");
    const versionButton = workspaceButton(`Version ${state.version.sequence} ▾`, "version-switcher");
    versionButton.id = "version-switcher";
    versionButton.addEventListener("click", () => { state.historyOpen = true; renderVersionDrawer(); });
    actions.append(versionButton);
    const commentsButton = workspaceButton(`Comments ${state.comments.length}`, "comments-toolbar-button");
    commentsButton.id = "comments-toggle";
    commentsButton.setAttribute("aria-expanded", String(state.commentsVisible));
    commentsButton.addEventListener("click", () => {
      state.commentsVisible = !state.commentsVisible;
      renderCommentsPanel();
    });
    actions.append(commentsButton);
    const pin = workspaceButton(state.artifact.pinned ? "★ Pinned" : "☆ Pin", "workspace-pin");
    pin.id = "workspace-pin";
    pin.setAttribute("aria-pressed", String(Boolean(state.artifact.pinned)));
    pin.addEventListener("click", () => togglePin(state.artifact.id, !state.artifact.pinned));
    actions.append(pin);
    header.append(actions);
    workspace.append(header);

    const menu = node("div", "title-menu");
    menu.id = "title-menu";
    menu.hidden = true;
    menu.append(node("p", "title-menu-heading", state.artifact.title));
    const all = workspaceButton("All artifacts", "menu-item");
    all.addEventListener("click", goHome);
    const pinItem = workspaceButton(state.artifact.pinned ? "Unpin" : "Pin", "menu-item");
    pinItem.addEventListener("click", () => { togglePin(state.artifact.id, !state.artifact.pinned); menu.hidden = true; });
    const history = workspaceButton(`Version history (${state.versions.length})`, "menu-item");
    history.addEventListener("click", () => { state.historyOpen = true; renderVersionDrawer(); menu.hidden = true; });
    const refresh = workspaceButton("Refresh", "menu-item");
    refresh.addEventListener("click", () => {
      const artifactId = state.artifact.id;
      const versionId = state.version.id;
      menu.hidden = true;
      state.artifact = null;
      showWorkspace(artifactId, versionId);
    });
    menu.append(all, pinItem, history, refresh);
    workspace.append(menu);

    const meta = node("div", "workspace-meta");
    meta.append(node("span", "kind-badge", state.artifact.kind));
    meta.append(node("span", "", `Version ${state.version.sequence}`));
    if (state.version.id !== state.artifact.current_version_id) meta.append(node("span", "historical-badge", "Viewing historical version · read-only"));
    workspace.append(meta);

    const contentLayout = node("div", "workspace-layout");
    const main = node("main", "artifact-main");
    const toolbar = node("div", "presentation-toolbar");
    toolbar.append(node("span", "presentation-title", state.artifact.kind === "markdown" ? "Rendered Markdown" : state.artifact.kind === "html" ? "Rendered HTML" : "Artifact preview"));
    const sourceToggle = workspaceButton(state.sourceMode ? "Rendered view" : "Source", "button secondary");
    sourceToggle.addEventListener("click", () => { state.sourceMode = !state.sourceMode; renderPresentation($("presentation"), state.presentation, state.comments); });
    toolbar.append(sourceToggle);
    if (state.version.id === state.artifact.current_version_id) {
      const newVersion = workspaceButton("New version", "button primary");
      newVersion.addEventListener("click", openVersionEditor);
      toolbar.append(newVersion);
    }
    main.append(toolbar);
    const presentation = node("section", "presentation-panel");
    presentation.id = "presentation";
    renderPresentation(presentation, state.presentation, state.comments);
    main.append(presentation);
    const versionEditor = buildVersionEditor();
    versionEditor.hidden = true;
    main.append(versionEditor);
    main.append(buildInlineCommentHint());
    contentLayout.append(main);
    const aside = node("aside", "comments-panel");
    aside.id = "comments-panel";
    contentLayout.append(aside);
    workspace.append(contentLayout);
    renderCommentsPanel();
    renderVersionDrawer();
  }

  function renderWorkspaceHeader() {
    const buttonElement = $("workspace-pin");
    if (!buttonElement || !state.artifact) return;
    buttonElement.textContent = state.artifact.pinned ? "★ Pinned" : "☆ Pin";
    buttonElement.setAttribute("aria-pressed", String(Boolean(state.artifact.pinned)));
  }

  function toggleTitleMenu() {
    const menu = $("title-menu");
    if (menu) menu.hidden = !menu.hidden;
  }

  function buildInlineCommentHint() {
    const hint = node("p", "selection-hint", "Select text in the rendered document to attach a highlighted comment.");
    hint.id = "selection-hint";
    return hint;
  }

  function buildVersionEditor() {
    const section = node("section", "version-editor");
    section.id = "version-editor";
    const heading = node("div", "editor-heading");
    heading.append(node("h2", "", "Publish a new version"));
    const close = button("×", "icon-button");
    close.setAttribute("aria-label", "Close editor");
    close.addEventListener("click", () => { section.hidden = true; });
    heading.append(close);
    const form = document.createElement("form");
    form.id = "new-version-form";
    const contentLabel = node("label", "", "Content");
    const content = document.createElement("textarea");
    content.id = "version-content";
    content.rows = 14;
    content.required = true;
    content.value = state.version.content;
    const summaryLabel = node("label", "", "Change summary");
    const summary = document.createElement("input");
    summary.id = "version-summary";
    summary.maxLength = 2048;
    const submit = button("Publish new version", "button primary", "submit");
    const status = node("p", "status");
    status.id = "version-status";
    form.append(contentLabel, content, summaryLabel, summary, submit, status);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      status.textContent = "Publishing…";
      try {
        await api(`/api/artifacts/${encodeURIComponent(state.artifact.id)}/versions`, {
          method: "POST",
          body: JSON.stringify({ content: content.value, created_by: "web", expected_current_version_id: state.artifact.current_version_id, change_summary: summary.value.trim() }),
        });
        section.hidden = true;
        showWorkspace(state.artifact.id, null);
      } catch (error) {
        status.textContent = error.message;
        status.className = "status error";
        submit.disabled = false;
      }
    });
    section.append(heading, form);
    return section;
  }

  function openVersionEditor() {
    const editor = $("version-editor");
    if (editor) editor.hidden = false;
  }

  function renderCommentsPanel() {
    const panel = $("comments-panel");
    if (!panel) return;
    const toolbar = $("comments-toggle");
    if (toolbar) {
      toolbar.textContent = `Comments ${state.comments.length}`;
      toolbar.setAttribute("aria-expanded", String(state.commentsVisible));
    }
    panel.replaceChildren();
    const heading = node("div", "comments-heading");
    heading.append(node("h2", "", "Comments"), node("span", "comment-count", String(state.comments.length)));
    const toggle = button(state.commentsVisible ? "Hide all comments" : "Show all comments", "button secondary");
    toggle.id = "comments-toggle-panel";
    toggle.addEventListener("click", () => { state.commentsVisible = !state.commentsVisible; renderCommentsPanel(); renderPresentation($("presentation"), state.presentation, state.commentsVisible ? state.comments : []); });
    heading.append(toggle);
    panel.append(heading);
    const list = node("div", "comments-list");
    list.hidden = !state.commentsVisible;
    if (!state.comments.length) list.append(node("p", "no-comments", "No comments on this version."));
    state.comments.forEach((comment) => list.append(renderComment(comment)));
    panel.append(list);
  }

  function renderComment(comment) {
    const card = node("article", `comment-card${comment.status === "resolved" ? " resolved" : ""}`);
    card.id = `comment-${comment.id}`;
    card.append(node("p", "comment-body", comment.body));
    const quote = node("blockquote", "comment-quote", anchorExact(comment.anchor) || "Selection unavailable");
    quote.addEventListener("click", () => focusComment(comment.id));
    card.append(quote);
    const footer = node("div", "comment-footer");
    footer.append(node("span", "comment-status", comment.status));
    if (comment.status !== "resolved") {
      const resolve = button("Resolve", "text-button");
      resolve.addEventListener("click", () => resolveComment(comment.id));
      footer.append(resolve);
    }
    card.append(footer);
    return card;
  }

  function focusComment(commentId) {
    const card = $(`comment-${commentId}`);
    if (!card) return;
    if (!state.commentsVisible) { state.commentsVisible = true; renderCommentsPanel(); }
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    card.classList.add("comment-focus");
    window.setTimeout(() => card.classList.remove("comment-focus"), 1200);
  }

  async function resolveComment(commentId) {
    try {
      await api(`/api/comments/${encodeURIComponent(commentId)}/events`, { method: "POST", body: JSON.stringify({ status: "resolved", actor: "web" }) });
      state.comments = await api(`/api/versions/${encodeURIComponent(state.version.id)}/comments`);
      renderCommentsPanel();
      renderPresentation($("presentation"), state.presentation, state.commentsVisible ? state.comments : []);
    } catch (error) {
      setStatus(error.message, "error");
    }
  }

  function showComposer(rect, selection) {
    closeComposer();
    const composer = node("form", "inline-composer");
    composer.id = "inline-composer";
    composer.style.left = `${Math.max(12, Math.min(window.innerWidth - 340, rect.left))}px`;
    composer.style.top = `${Math.min(window.innerHeight - 190, rect.bottom + 10)}px`;
    composer.append(node("p", "composer-label", "Comment on selection"));
    composer.append(node("blockquote", "composer-quote", selection.exact));
    const textarea = document.createElement("textarea");
    textarea.rows = 3;
    textarea.maxLength = 16384;
    textarea.required = true;
    textarea.placeholder = "What should change?";
    const actions = node("div", "composer-actions");
    const cancel = button("Cancel", "button secondary");
    cancel.addEventListener("click", closeComposer);
    const send = button("Send comment", "button primary", "submit");
    actions.append(cancel, send);
    const status = node("p", "status");
    composer.append(textarea, actions, status);
    composer.addEventListener("submit", async (event) => {
      event.preventDefault();
      send.disabled = true;
      status.textContent = "Saving…";
      try {
        await api(`/api/versions/${encodeURIComponent(state.version.id)}/comments`, {
          method: "POST",
          body: JSON.stringify({ artifact_id: state.artifact.id, body: textarea.value.trim(), anchor: selection }),
        });
        closeComposer();
        state.comments = await api(`/api/versions/${encodeURIComponent(state.version.id)}/comments`);
        renderCommentsPanel();
        renderPresentation($("presentation"), state.presentation, state.comments);
        await loadCatalog();
      } catch (error) {
        status.textContent = error.message;
        status.className = "status error";
        send.disabled = false;
      }
    });
    document.body.append(composer);
    textarea.focus();
    state.composer = composer;
  }

  function closeComposer() {
    if (state.composer) state.composer.remove();
    state.composer = null;
    state.selection = null;
  }

  function selectionFromRange(range, exact) {
    const container = $("presentation");
    const text = container?.textContent || "";
    if (!range || !container) return null;
    const before = document.createRange();
    before.selectNodeContents(container);
    before.setEnd(range.startContainer, range.startOffset);
    const selected = range.toString();
    const leading = Math.max(0, selected.indexOf(exact));
    const start = Array.from(before.toString()).length + Array.from(selected.slice(0, leading)).length;
    const exactLength = Array.from(exact).length;
    const points = Array.from(text);
    return {
      schema: 2,
      type: "text-range",
      coordinate_space: "unicode-code-points",
      start: Math.max(0, start),
      end: start + exactLength,
      quote: {
        exact,
        prefix: points.slice(Math.max(0, start - 80), start).join(""),
        suffix: points.slice(start + exactLength, start + exactLength + 80).join(""),
      },
    };
  }

  function captureSelection(range, exact, rect) {
    if (!state.version || !exact.trim()) return;
    state.selection = selectionFromRange(range, exact);
    if (!state.selection) return;
    showComposer(rect, state.selection);
  }

  function renderVersionDrawer() {
    const existing = $("version-drawer");
    if (existing) existing.remove();
    if (!state.historyOpen || !state.artifact) return;
    const drawer = node("aside", "version-drawer");
    drawer.id = "version-drawer";
    const heading = node("div", "drawer-heading");
    heading.append(node("h2", "", "Version history"));
    const close = button("×", "icon-button");
    close.setAttribute("aria-label", "Close version history");
    close.addEventListener("click", () => { state.historyOpen = false; renderVersionDrawer(); });
    heading.append(close);
    drawer.append(heading);
    state.versions.forEach((version) => {
      const row = node("button", `version-row${state.version?.id === version.id ? " active" : ""}`);
      row.type = "button";
      row.append(node("span", "version-number", `v${version.sequence}`));
      const info = node("span", "version-info");
      info.append(node("strong", "", version.id === state.artifact.current_version_id ? "Current" : (version.change_summary || "Immutable version")));
      info.append(node("small", "", formatDateTime(version.created_at)));
      row.append(info);
      row.addEventListener("click", () => {
        state.historyOpen = true;
        openArtifact(state.artifact.id, version.id);
      });
      drawer.append(row);
    });
    if (state.version?.id !== state.artifact.current_version_id) {
      const current = button("Back to current", "button primary");
      current.addEventListener("click", () => { state.historyOpen = false; openArtifact(state.artifact.id); });
      drawer.append(current);
      const restore = button("Restore this version", "button secondary");
      restore.addEventListener("click", async () => {
        restore.disabled = true;
        restore.textContent = "Restoring…";
        try {
          await api(`/api/artifacts/${encodeURIComponent(state.artifact.id)}/restore`, {
            method: "POST",
            body: JSON.stringify({ version_id: state.version.id, created_by: "web", expected_current_version_id: state.artifact.current_version_id }),
          });
          state.historyOpen = false;
          openArtifact(state.artifact.id);
        } catch (error) {
          restore.disabled = false;
          restore.textContent = error.message;
        }
      });
      drawer.append(restore);
    }
    $("workspace").append(drawer);
  }

  function handleSandboxMessage(event) {
    const data = event.data;
    const frame = $("presentation")?.querySelector("iframe.html-frame");
    if (!frame || event.source !== frame.contentWindow || !data || data.schema !== 2 || data.nonce !== "oaa-bridge-v1") return;
    if (!state.version || data.version_id !== state.version.id) return;
    if (data.type === "oaa-resize") {
      const height = Number(data.height);
      if (Number.isFinite(height) && height > 0) frame.setAttribute("height", String(Math.max(320, Math.min(20000, Math.ceil(height + 8)))));
      return;
    }
    if (data.type !== "oaa-selection" || !data.anchor || typeof data.anchor.quote?.exact !== "string") return;
    const rect = frame.getBoundingClientRect();
    state.selection = data.anchor;
    showComposer({ left: rect.left + 16, bottom: rect.top + 70 }, state.selection);
  }

  function bindEvents() {
    $("new-artifact").addEventListener("click", openCreateDialog);
    $("catalog-search").addEventListener("input", (event) => {
      state.catalog.query = event.target.value.trim();
      state.catalog.nextCursor = null;
      persistCatalogState();
      loadCatalog();
    });
    document.querySelectorAll(".scope-tab").forEach((tab) => tab.addEventListener("click", () => {
      if (tab.classList.contains("identity-disabled")) return;
      state.catalog.scope = tab.dataset.scope;
      state.catalog.nextCursor = null;
      updateUrlForCatalog(true);
      loadCatalog();
    }));
    document.querySelectorAll(".view-button").forEach((control) => control.addEventListener("click", () => {
      state.catalog.view = control.dataset.view;
      localStorage.setItem("oaa_view_mode", state.catalog.view);
      state.catalog.nextCursor = null;
      updateUrlForCatalog(true);
      renderCatalog();
      loadCatalog();
    }));
    $("load-more").addEventListener("click", () => loadCatalog(true));
    $("close-new-artifact").addEventListener("click", closeCreateDialog);
    $("cancel-new-artifact").addEventListener("click", closeCreateDialog);
    $("new-artifact-form").addEventListener("submit", submitNewArtifact);
    $("new-artifact-dialog").addEventListener("click", (event) => { if (event.target === $("new-artifact-dialog")) closeCreateDialog(); });
    document.addEventListener("selectionchange", () => {
      const selection = window.getSelection();
      const container = $("presentation");
      if (!selection || selection.isCollapsed || !container || !container.contains(selection.anchorNode) || !container.contains(selection.focusNode)) return;
      const exact = selection.toString().trim();
      if (!exact) return;
      captureSelection(selection.getRangeAt(0), exact, selection.getRangeAt(0).getBoundingClientRect());
    });
    window.addEventListener("message", handleSandboxMessage);
    window.addEventListener("popstate", route);
    window.addEventListener("hashchange", route);
    window.addEventListener("keydown", (event) => {
      if (event.key === "/" && document.activeElement?.tagName !== "INPUT" && document.activeElement?.tagName !== "TEXTAREA") {
        event.preventDefault();
        $("catalog-search")?.focus();
      }
      if (event.key === "Escape") {
        closeComposer();
        if (state.historyOpen) {
          state.historyOpen = false;
          renderVersionDrawer();
        }
        const titleMenu = $("title-menu");
        if (titleMenu) titleMenu.hidden = true;
      }
    });
    document.addEventListener("click", (event) => {
      const target = event.target;
      if (state.historyOpen && !target.closest("#version-drawer, #version-switcher, #title-menu")) {
        state.historyOpen = false;
        renderVersionDrawer();
      }
      const titleMenu = $("title-menu");
      if (titleMenu && !titleMenu.hidden && !target.closest("#title-menu, .title-menu-trigger")) titleMenu.hidden = true;
    });
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
    const submit = event.submitter;
    if (submit) submit.disabled = true;
    status.textContent = "Creating…";
    try {
      const artifact = await api("/api/artifacts", {
        method: "POST",
        body: JSON.stringify({ title: $("new-artifact-title").value.trim(), kind: $("new-artifact-kind").value, content: $("new-artifact-content").value, created_by: "web" }),
      });
      closeCreateDialog();
      $("new-artifact-form").reset();
      openArtifact(artifact.id);
    } catch (error) {
      status.textContent = error.message;
      status.className = "status error";
      if (submit) submit.disabled = false;
    }
  }

  async function initialize() {
    restoreCatalogState();
    $("catalog-search").value = state.catalog.query;
    try {
      state.me = await api("/api/me");
      const enabled = Boolean(state.me.multi_user && state.me.capabilities?.identity_scopes);
      document.querySelectorAll(".identity-tab").forEach((tab) => {
        tab.hidden = !enabled;
        tab.classList.toggle("identity-disabled", !enabled);
      });
      $("scope-tabs").hidden = !enabled;
      if (!enabled && ["yours", "shared"].includes(state.catalog.scope)) state.catalog.scope = "all";
    } catch (_) {
      document.querySelectorAll(".identity-tab").forEach((tab) => { tab.hidden = true; tab.classList.add("identity-disabled"); });
      $("scope-tabs").hidden = true;
    }
    bindEvents();
    route();
  }

  initialize();
})();
