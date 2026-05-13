import {
  detectSource,
  fileSource,
  getEntrySourceKey,
  getEntryTitle,
  getLinkTitle,
  getSource,
} from "./sources/registry.js?v=20260511-instant-add";
import { getCommonLogoCandidates } from "./sources/logos.js?v=20260511-instant-add";

const DB_NAME = "clipy-bookmarks";
const DB_VERSION = 1;
const STORE_NAME = "entries";
const LINK_BACKUP_KEY = "clipy-link-backup-v1";
const API_ROOT = "/api";

const state = {
  db: null,
  entries: [],
  pendingFiles: [],
  expandedEntries: new Set(),
  verificationPolls: new Map(),
  profileOpenPolls: new Map(),
  browserSessions: [],
  repositories: [],
  composerAnchorTop: 0,
  activeFilter: "all",
  activeRepositoryId: "",
  repositoryEditOpen: false,
  repositoryMenuId: "",
  repositoryPickerQuery: "",
  repositoryRenamingId: "",
  storageMode: "browser",
  storagePath: "",
  isAdding: false,
  isSessionLoading: false,
  sessionPanelOpen: false,
};

const elements = {
  addButton: document.querySelector("#addButton"),
  addRepositoryButton: document.querySelector("#addRepositoryButton"),
  composer: document.querySelector(".composer"),
  composerSpacer: document.querySelector("#composerSpacer"),
  dropZone: document.querySelector("#dropZone"),
  emptyState: document.querySelector("#emptyState"),
  entryCount: document.querySelector("#entryCount"),
  fileInput: document.querySelector("#fileInput"),
  filePreview: document.querySelector("#filePreview"),
  filters: document.querySelector(".filters"),
  filterChips: document.querySelector("#filterChips"),
  linkInput: document.querySelector("#linkInput"),
  notice: document.querySelector("#notice"),
  pickFilesButton: document.querySelector("#pickFilesButton"),
  repositoryChips: document.querySelector("#repositoryChips"),
  repositoryDoneButton: document.querySelector("#repositoryDoneButton"),
  repositoryManageButton: document.querySelector("#repositoryManageButton"),
  repositoryMeta: document.querySelector("#repositoryMeta"),
  repositoryNameInput: document.querySelector("#repositoryNameInput"),
  repositoryPanel: document.querySelector("#repositoryPanel"),
  repositoryPanelHeader: document.querySelector("#repositoryPanelHeader"),
  repositoryPicker: document.querySelector("#repositoryPicker"),
  sessionButton: document.querySelector("#sessionButton"),
  sessionCloseButton: document.querySelector("#sessionCloseButton"),
  sessionList: document.querySelector("#sessionList"),
  sessionPanel: document.querySelector("#sessionPanel"),
  sessionStatus: document.querySelector("#sessionStatus"),
  syncPill: document.querySelector(".sync-pill"),
  timeline: document.querySelector("#timeline"),
  fileChipTemplate: document.querySelector("#fileChipTemplate"),
  timelineItemTemplate: document.querySelector("#timelineItemTemplate"),
};

async function boot() {
  try {
    const diskData = await fetchDiskEntries();
    if (diskData) {
      state.storageMode = "disk";
      state.storagePath = diskData.storagePath || "";
      setStorageStatus();
      state.entries = sortEntries(diskData.entries || []);
      state.repositories = sortRepositories(diskData.repositories || []);
      updateLinkBackup(state.entries);
      render();
      return;
    }

    state.storageMode = "browser";
    setStorageStatus();
    state.db = await openDatabase();
    state.entries = await mergeBackedUpLinks(await getAllEntries());
    render();
  } catch (error) {
    showNotice("本地保存没有打开，请重启本地服务再试。", true);
    console.error(error);
  }
}

async function fetchDiskEntries() {
  try {
    const response = await fetch(`${API_ROOT}/entries`, { cache: "no-store" });
    const contentType = response.headers.get("content-type") || "";
    if (!response.ok || !contentType.includes("application/json")) return null;
    return await response.json();
  } catch {
    return null;
  }
}

async function refreshDiskEntries() {
  if (!isDiskStorage()) return;
  const diskData = await fetchDiskEntries();
  if (!diskData) return;
  state.storagePath = diskData.storagePath || state.storagePath;
  state.entries = sortEntries(diskData.entries || []);
  state.repositories = sortRepositories(diskData.repositories || state.repositories || []);
  updateLinkBackup(state.entries);
  render();
}

function scheduleDiskRefresh() {
  if (!isDiskStorage()) return;
  [1500, 5000, 12000].forEach((delay) => {
    window.setTimeout(() => {
      refreshDiskEntries().catch((error) => console.error(error));
    }, delay);
  });
}

async function migrateBrowserEntriesToDisk(browserEntries, diskEntries) {
  const diskKeys = new Set(diskEntries.map(getDiskIdentity));
  const missingEntries = browserEntries.filter((entry) => !diskKeys.has(getDiskIdentity(entry)));

  for (const entry of missingEntries) {
    if (entry.kind === "link") {
      await saveDiskLink(entry);
    } else if (entry.kind === "file" && entry.fileBlob) {
      await saveDiskFile(entry.fileBlob, entry);
    }
  }
}

function getDiskIdentity(entry) {
  return entry.kind === "link" && entry.url ? `link:${entry.url}` : `${entry.kind}:${entry.id}`;
}

function isDiskStorage() {
  return state.storageMode === "disk";
}

function setStorageStatus() {
  if (!elements.syncPill) return;
  elements.syncPill.innerHTML = '<span class="status-dot"></span>';
  elements.syncPill.append(isDiskStorage() ? "本地磁盘" : "浏览器保存");
  elements.syncPill.title = isDiskStorage()
    ? `数据保存在 ${state.storagePath || "项目的 clipy_library 文件夹"}`
    : "当前没有本地服务，数据暂存在浏览器里";
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: "id" });
        store.createIndex("createdAt", "createdAt");
        store.createIndex("kind", "kind");
      }
    };

    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function runStore(mode, callback) {
  return new Promise((resolve, reject) => {
    const transaction = state.db.transaction(STORE_NAME, mode);
    const store = transaction.objectStore(STORE_NAME);
    const request = callback(store);

    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function getAllEntries() {
  const entries = await runStore("readonly", (store) => store.getAll());
  return sortEntries(entries);
}

async function saveEntry(entry) {
  await runStore("readwrite", (store) => store.put(entry));
}

async function deleteEntry(id) {
  await runStore("readwrite", (store) => store.delete(id));
}

async function saveDiskLink(entry) {
  const response = await fetch(`${API_ROOT}/entries/link`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(entry),
  });

  if (!response.ok) throw new Error("Link save failed");
  const payload = await response.json();
  return payload.entry;
}

async function saveDiskFile(file, entry) {
  const response = await fetch(`${API_ROOT}/entries/file`, {
    method: "POST",
    headers: {
      "X-Clipy-Id": entry.id,
      "X-File-Name": encodeURIComponent(file.name),
      "X-File-Type": encodeURIComponent(file.type || ""),
      "X-Created-At": String(entry.createdAt),
      "X-Batch-Order": String(entry.batchOrder || 0),
    },
    body: file,
  });

  if (!response.ok) throw new Error("File save failed");
  const payload = await response.json();
  return payload.entry;
}

async function saveDiskFileBatch(files, entry) {
  let savedEntry = entry;

  for (const file of files) {
    const fileRecord = entry.files.find((item) => item.fileBlob === file);
    const response = await fetch(`${API_ROOT}/entries/file`, {
      method: "POST",
      headers: {
        "X-Clipy-Id": entry.id,
        "X-Clipy-File-Id": fileRecord.id,
        "X-Clipy-Batch-Count": String(files.length),
        "X-Clipy-Batch-Title": encodeURIComponent(entry.title),
        "X-File-Name": encodeURIComponent(file.name),
        "X-File-Type": encodeURIComponent(file.type || ""),
        "X-Created-At": String(entry.createdAt),
        "X-Batch-Order": String(fileRecord.batchOrder || 0),
      },
      body: file,
    });

    if (!response.ok) throw new Error("File batch save failed");
    const payload = await response.json();
    savedEntry = payload.entry;
  }

  return savedEntry;
}

async function deleteStoredEntry(id) {
  if (isDiskStorage()) {
    await fetch(`${API_ROOT}/entries/${encodeURIComponent(id)}`, { method: "DELETE" });
    return;
  }

  await deleteEntry(id);
}

async function renameStoredEntry(entry, title) {
  const updatedEntry = {
    ...entry,
    title,
    renamedAt: Date.now(),
  };

  if (isDiskStorage()) {
    const response = await fetch(`${API_ROOT}/entries/${encodeURIComponent(entry.id)}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ title }),
    });
    if (!response.ok) throw new Error("Entry rename failed");
    const payload = await response.json();
    return payload.entry;
  }

  await saveEntry(updatedEntry);
  return updatedEntry;
}

async function requestEntryVerification(entry) {
  if (!isDiskStorage()) throw new Error("Verification requires disk storage");
  const response = await fetch(`${API_ROOT}/entries/${encodeURIComponent(entry.id)}/verify`, {
    method: "POST",
  });
  if (!response.ok) throw new Error("Verification failed");
  return response.json();
}

async function requestEntryReparse(entry) {
  if (!isDiskStorage()) throw new Error("Reparse requires disk storage");
  const response = await fetch(`${API_ROOT}/entries/${encodeURIComponent(entry.id)}/reparse`, {
    method: "POST",
  });
  if (!response.ok) throw new Error("Reparse failed");
  return response.json();
}

async function requestEntryProfileOpen(entry) {
  if (!isDiskStorage()) throw new Error("Profile open requires disk storage");
  const response = await fetch(`${API_ROOT}/open/${encodeURIComponent(entry.id)}`, {
    method: "POST",
  });
  if (!response.ok) throw new Error("Profile open failed");
  return response.json();
}

async function requestCreateRepository(name) {
  if (!isDiskStorage()) throw new Error("Repository requires disk storage");
  const response = await fetch(`${API_ROOT}/repositories`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ name }),
  });
  if (!response.ok) throw new Error("Repository create failed");
  return response.json();
}

async function requestUpdateRepository(repository, fields) {
  if (!isDiskStorage()) throw new Error("Repository requires disk storage");
  const response = await fetch(`${API_ROOT}/repositories/${encodeURIComponent(repository.id)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(fields),
  });
  if (!response.ok) throw new Error("Repository update failed");
  return response.json();
}

async function requestDeleteRepository(repository) {
  if (!isDiskStorage()) throw new Error("Repository requires disk storage");
  const response = await fetch(`${API_ROOT}/repositories/${encodeURIComponent(repository.id)}`, {
    method: "DELETE",
  });
  if (!response.ok) throw new Error("Repository delete failed");
  return response.json();
}

async function deleteBatchFile(entry, file) {
  if (!isDiskStorage()) throw new Error("Batch file deletion requires disk storage");

  const response = await fetch(
    `${API_ROOT}/entries/${encodeURIComponent(entry.id)}/files/${encodeURIComponent(file.id)}`,
    { method: "DELETE" },
  );
  if (!response.ok) throw new Error("Batch file deletion failed");
  return response.json();
}

async function fetchBrowserSessions() {
  const response = await fetch(`${API_ROOT}/browser-sessions`, { cache: "no-store" });
  if (!response.ok) throw new Error("Browser session fetch failed");
  return response.json();
}

async function requestBrowserSessionLogout(session) {
  const response = await fetch(`${API_ROOT}/browser-sessions/logout`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ siteKey: session.siteKey }),
  });
  if (!response.ok) throw new Error("Browser session logout failed");
  return response.json();
}

async function requestBrowserSessionLogin(session) {
  const response = await fetch(`${API_ROOT}/browser-sessions/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ siteKey: session.siteKey, url: getSessionTargetUrl(session) }),
  });
  if (!response.ok) throw new Error("Browser session login failed");
  return response.json();
}

async function mergeBackedUpLinks(entries) {
  const backedUpLinks = getBackedUpLinks();
  if (backedUpLinks.length === 0) return sortEntries(entries);

  const existingKeys = new Set(entries.map(getBackupIdentity));
  const missingLinks = backedUpLinks.filter((entry) => !existingKeys.has(getBackupIdentity(entry)));

  for (const entry of missingLinks) {
    await saveEntry(entry);
  }

  const mergedEntries = sortEntries([...entries, ...missingLinks]);
  updateLinkBackup(mergedEntries);
  return mergedEntries;
}

function getBackedUpLinks() {
  try {
    const parsed = JSON.parse(localStorage.getItem(LINK_BACKUP_KEY) || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((entry) => entry && entry.kind === "link" && entry.url)
      .map((entry) => ({
        id: entry.id || createId(),
        kind: "link",
        sourceKey: entry.sourceKey || "web",
        sourceName: entry.sourceName || "网页",
        title: entry.title || "未命名收藏",
        url: entry.url,
        rawText: entry.rawText || entry.url,
        createdAt: Number(entry.createdAt) || Date.now(),
      }));
  } catch {
    return [];
  }
}

function updateLinkBackup(entries = state.entries) {
  try {
    const links = sortEntries(entries)
      .filter((entry) => entry.kind === "link" && entry.url)
      .map(({ id, kind, sourceKey, sourceName, title, url, rawText, createdAt }) => ({
        id,
        kind,
        sourceKey,
        sourceName,
        title,
        url,
        rawText,
        createdAt,
      }));
    localStorage.setItem(LINK_BACKUP_KEY, JSON.stringify(links));
  } catch {
    showNotice("浏览器备份空间不足，链接仍已保存到主数据库。", true);
  }
}

function getBackupIdentity(entry) {
  return entry.kind === "link" && entry.url ? entry.url : entry.id;
}

function setupEvents() {
  elements.sessionButton?.addEventListener("click", () => {
    if (state.sessionPanelOpen) {
      closeSessionPanel();
    } else {
      openSessionPanel();
    }
  });

  elements.sessionCloseButton?.addEventListener("click", closeSessionPanel);

  elements.pickFilesButton.addEventListener("click", () => elements.fileInput.click());
  elements.fileInput.addEventListener("change", () => {
    addPendingFiles(elements.fileInput.files);
    elements.fileInput.value = "";
  });

  elements.addButton.addEventListener("click", handleAdd);
  elements.addRepositoryButton?.addEventListener("click", handleCreateRepository);
  elements.linkInput.addEventListener("input", () => {
    resizeTextInput();
    updateAddButton();
    clearNotice();
  });

  elements.linkInput.addEventListener("paste", (event) => {
    const files = [...event.clipboardData.files];
    if (files.length > 0) {
      event.preventDefault();
      addPendingFiles(files);
    }
  });

  elements.linkInput.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      handleAdd();
    }
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    elements.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      elements.dropZone.classList.add("dragging");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    elements.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      if (event.type === "dragleave" && elements.dropZone.contains(event.relatedTarget)) return;
      elements.dropZone.classList.remove("dragging");
    });
  });

  elements.dropZone.addEventListener("drop", (event) => {
    const files = [...event.dataTransfer.files];
    if (files.length > 0) {
      addPendingFiles(files);
      return;
    }

    const text = event.dataTransfer.getData("text/uri-list") || event.dataTransfer.getData("text/plain");
    if (text.trim()) {
      elements.linkInput.value = text.trim();
      resizeTextInput();
      updateAddButton();
    }
  });

  elements.filters.addEventListener("click", (event) => {
    const menuAction = event.target.closest("[data-repository-action]");
    if (menuAction) {
      event.stopPropagation();
      handleRepositoryMenuAction(menuAction.dataset.repositoryId || "", menuAction.dataset.repositoryAction || "");
      return;
    }

    const manageButton = event.target.closest("[data-repository-manage-id]");
    if (manageButton) {
      event.stopPropagation();
      state.repositoryMenuId = state.repositoryMenuId === manageButton.dataset.repositoryManageId ? "" : manageButton.dataset.repositoryManageId;
      render();
      return;
    }

    const repositoryButton = event.target.closest("[data-repository-id]");
    if (repositoryButton) {
      const repositoryId = repositoryButton.dataset.repositoryId || "";
      const isAlreadyActive = state.activeFilter === "repository" && state.activeRepositoryId === repositoryId;
      if (isAlreadyActive) {
        startRepositoryRename();
        return;
      }

      state.activeFilter = "repository";
      state.activeRepositoryId = repositoryId;
      state.repositoryEditOpen = false;
      state.repositoryMenuId = "";
      state.repositoryPickerQuery = "";
      state.repositoryRenamingId = "";
      render();
      return;
    }

    const button = event.target.closest("[data-filter]");
    if (!button) return;
    state.activeFilter = button.dataset.filter;
    state.activeRepositoryId = "";
    state.repositoryEditOpen = false;
    state.repositoryMenuId = "";
    state.repositoryPickerQuery = "";
    state.repositoryRenamingId = "";
    render();
  });

  elements.repositoryPanelHeader?.addEventListener("click", (event) => {
    if (event.target.closest("button")) return;
    if (event.target.closest("input") && state.repositoryRenamingId === state.activeRepositoryId) return;
    toggleRepositoryPicker();
  });

  elements.repositoryPanelHeader?.addEventListener("keydown", (event) => {
    if (event.target.closest("input, button")) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggleRepositoryPicker();
    }
  });

  document.addEventListener("click", (event) => {
    if (!state.repositoryMenuId || event.target.closest(".repository-chip-wrap")) return;
    state.repositoryMenuId = "";
    render();
  });

  elements.repositoryNameInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      event.currentTarget.blur();
    }
  });

  elements.repositoryNameInput?.addEventListener("click", (event) => {
    event.stopPropagation();
    startRepositoryRename();
  });

  elements.repositoryNameInput?.addEventListener("blur", handleRepositoryNameBlur);
}

async function handleCreateRepository() {
  if (!isDiskStorage()) {
    showNotice("仓库需要本地服务保存。", true);
    return;
  }

  const nextIndex = state.repositories.length + 1;
  try {
    const payload = await requestCreateRepository(`新仓库 ${nextIndex}`);
    const repository = payload.repository;
    state.repositories = sortRepositories(payload.repositories || [repository, ...state.repositories]);
    state.activeFilter = "repository";
    state.activeRepositoryId = repository.id;
    state.repositoryEditOpen = false;
    state.repositoryMenuId = "";
    state.repositoryPickerQuery = "";
    state.repositoryRenamingId = repository.id;
    clearNotice();
    render();
    window.requestAnimationFrame(() => {
      elements.repositoryNameInput?.focus();
      elements.repositoryNameInput?.select();
    });
  } catch (error) {
    console.error(error);
    showNotice("新建仓库失败，请确认本地服务还在运行。", true);
  }
}

function toggleRepositoryPicker() {
  if (!getActiveRepository()) return;
  state.repositoryEditOpen = !state.repositoryEditOpen;
  state.repositoryMenuId = "";
  state.repositoryRenamingId = "";
  render();
}

function startRepositoryRename() {
  const repository = getActiveRepository();
  if (!repository || state.repositoryRenamingId === repository.id) return;
  state.repositoryMenuId = "";
  state.repositoryRenamingId = repository.id;
  render();
  window.requestAnimationFrame(() => {
    elements.repositoryNameInput?.focus();
    elements.repositoryNameInput?.select();
  });
}

async function handleRepositoryMenuAction(repositoryId, action) {
  const repository = state.repositories.find((item) => item.id === repositoryId);
  if (!repository) return;

  state.activeFilter = "repository";
  state.activeRepositoryId = repository.id;
  state.repositoryMenuId = "";

  if (action === "rename") {
    state.repositoryEditOpen = false;
    startRepositoryRename();
    return;
  }

  if (action === "delete") {
    const confirmed = window.confirm(`删除仓库“${repository.name}”？收藏本身会保留。`);
    if (!confirmed) {
      render();
      return;
    }

    try {
      const payload = await requestDeleteRepository(repository);
      state.repositories = sortRepositories(payload.repositories || state.repositories.filter((item) => item.id !== repository.id));
      if (state.activeRepositoryId === repository.id) {
        state.activeFilter = "all";
        state.activeRepositoryId = "";
        state.repositoryEditOpen = false;
        state.repositoryRenamingId = "";
      }
      clearNotice();
      render();
    } catch (error) {
      console.error(error);
      showNotice("删除仓库失败。", true);
      render();
    }
  }
}

async function handleRepositoryNameBlur(event) {
  const repository = getActiveRepository();
  if (!repository) return;
  const nextName = event.currentTarget.value.trim();
  state.repositoryRenamingId = "";
  if (!nextName || nextName === repository.name) {
    event.currentTarget.value = repository.name;
    render();
    return;
  }

  try {
    const payload = await requestUpdateRepository(repository, { name: nextName });
    state.repositories = sortRepositories(payload.repositories || state.repositories.map((item) => (item.id === repository.id ? payload.repository : item)));
    clearNotice();
    render();
  } catch (error) {
    console.error(error);
    event.currentTarget.value = repository.name;
    showNotice("仓库重命名失败。", true);
    render();
  }
}

async function openSessionPanel() {
  if (!isDiskStorage()) {
    showNotice("登录状态需要本地服务和 Clipy 浏览器 profile。", true);
    return;
  }

  state.sessionPanelOpen = true;
  elements.sessionPanel?.removeAttribute("hidden");
  elements.sessionPanel?.classList.remove("hidden");
  elements.sessionButton?.setAttribute("aria-expanded", "true");
  await refreshBrowserSessions();
  window.requestAnimationFrame(refreshComposerPin);
}

function closeSessionPanel() {
  state.sessionPanelOpen = false;
  elements.sessionPanel?.setAttribute("hidden", "");
  elements.sessionPanel?.classList.add("hidden");
  elements.sessionButton?.setAttribute("aria-expanded", "false");
  elements.sessionList?.replaceChildren();
  state.isSessionLoading = false;
  window.requestAnimationFrame(refreshComposerPin);
}

async function refreshBrowserSessions() {
  if (!isDiskStorage()) return;
  state.isSessionLoading = true;
  renderSessionPanel();

  try {
    const payload = await fetchBrowserSessions();
    state.browserSessions = sortBrowserSessions(payload.sessions || []);
    state.isSessionLoading = false;
    renderSessionPanel();
  } catch (error) {
    console.error(error);
    state.isSessionLoading = false;
    if (elements.sessionStatus) elements.sessionStatus.textContent = "没有读到登录状态，请确认本地服务还在运行。";
    if (elements.sessionList) {
      elements.sessionList.replaceChildren(renderSessionEmpty("暂时没有读到 Clipy 浏览器状态。"));
    }
  }
}

function renderSessionPanel() {
  if (!elements.sessionList || !elements.sessionStatus) return;

  if (state.isSessionLoading) {
    elements.sessionStatus.textContent = "正在读取 Clipy 浏览器 profile。";
    elements.sessionList.replaceChildren(renderSessionEmpty("读取中..."));
    return;
  }

  const sessions = sortBrowserSessions(state.browserSessions);
  const loggedInCount = sessions.filter((session) => session.status === "logged_in").length;
  const loggedOutCount = sessions.length - loggedInCount;
  elements.sessionStatus.textContent =
    sessions.length > 0
      ? `${loggedInCount} 个已登录，${loggedOutCount} 个未登录；这里只显示抓取时需要登录或验证的链接。`
      : "还没有需要登录或验证的链接。";

  if (sessions.length === 0) {
    elements.sessionList.replaceChildren();
    return;
  }

  elements.sessionList.replaceChildren(...sessions.map(renderSessionRow));
}

function renderSessionEmpty(message) {
  const empty = document.createElement("p");
  empty.className = "session-empty";
  empty.textContent = message;
  return empty;
}

function renderSessionRow(session) {
  const row = document.createElement("div");
  row.className = `session-row session-${session.status || "logged_out"}`;
  row.tabIndex = 0;
  row.setAttribute("role", "link");
  row.title = getSessionTargetUrl(session) ? "打开最近保存的这个来源链接" : "";
  row.addEventListener("click", () => openSessionTarget(session));
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openSessionTarget(session);
    }
  });

  const icon = document.createElement("span");
  const sourceKey = getSessionSourceKey(session);
  renderSourceIcon(icon, sourceKey, {
    kind: "link",
    sourceKey,
    sourceName: session.displayName || getSource(sourceKey).name,
    url: getSessionIconUrl(session),
  });
  icon.classList.add("session-source-icon");

  const copy = document.createElement("div");
  copy.className = "session-copy";

  const titleLine = document.createElement("div");
  titleLine.className = "session-title-line";

  const site = document.createElement("strong");
  site.className = "session-site";
  site.textContent = session.displayName || session.siteKey || "未知站点";

  const status = document.createElement("span");
  status.className = `session-status session-status-${session.status || "logged_out"}`;
  status.textContent = getSessionStatusText(session);

  titleLine.append(site, status);

  const meta = document.createElement("p");
  meta.className = "session-meta";
  meta.textContent = getSessionMetaText(session);

  copy.append(titleLine, meta);

  const actions = document.createElement("div");
  actions.className = "session-row-actions";

  const toggle = document.createElement("button");
  const isLoggedIn = session.status === "logged_in";
  toggle.className = "session-toggle";
  toggle.type = "button";
  toggle.setAttribute("role", "switch");
  toggle.setAttribute("aria-checked", String(isLoggedIn));
  toggle.setAttribute("aria-label", isLoggedIn ? `退出 ${site.textContent}` : `登录 ${site.textContent}`);
  toggle.title = isLoggedIn ? "退出登录" : "重新登录";
  toggle.append(document.createElement("span"));
  toggle.addEventListener("click", async (event) => {
    event.stopPropagation();
    if (isLoggedIn) {
      await logoutBrowserSession(session);
    } else {
      await loginBrowserSession(session);
    }
  });

  actions.append(toggle);
  row.append(icon, copy, actions);
  return row;
}

async function logoutBrowserSession(session) {
  try {
    const payload = await requestBrowserSessionLogout(session);
    state.browserSessions = sortBrowserSessions(payload.sessions || []);
    showNotice(`${session.displayName || session.siteKey} 已退出 Clipy 浏览器登录。`);
    renderSessionPanel();
  } catch (error) {
    console.error(error);
    showNotice("退出登录失败，请确认 Clipy 浏览器没有卡住。", true);
  }
}

async function loginBrowserSession(session) {
  try {
    const payload = await requestBrowserSessionLogin(session);
    state.browserSessions = sortBrowserSessions(payload.sessions || []);
    showNotice(`已打开 ${session.displayName || session.siteKey}，请在 Clipy 浏览器里登录。`);
    renderSessionPanel();
    window.setTimeout(() => refreshBrowserSessions().catch((error) => console.error(error)), 4000);
  } catch (error) {
    console.error(error);
    showNotice("没有打开登录窗口，请稍后再试。", true);
  }
}

function sortBrowserSessions(sessions) {
  return [...sessions].sort((a, b) => {
    const aLoggedOut = a.status !== "logged_in";
    const bLoggedOut = b.status !== "logged_in";
    if (aLoggedOut !== bLoggedOut) return aLoggedOut ? 1 : -1;
    const aTime = a.lastAccessedAt || a.updatedAt || a.loginStartedAt || a.loggedOutAt || 0;
    const bTime = b.lastAccessedAt || b.updatedAt || b.loginStartedAt || b.loggedOutAt || 0;
    if (bTime !== aTime) return bTime - aTime;
    return String(a.siteKey || "").localeCompare(String(b.siteKey || ""));
  });
}

function getSessionStatusText(session) {
  return session.status === "logged_in" ? "已登录" : "未登录";
}

function getSessionMetaText(session) {
  const parts = [session.siteKey || session.host || ""];
  if (session.recentTitle) parts.push(`最近收藏：${shortStatus(session.recentTitle)}`);
  if (session.requiredReason) parts.push(session.requiredReason);
  if (session.status === "login_pending") parts.push("登录窗口已打开");
  const time = session.lastAccessedAt || session.updatedAt || session.loginStartedAt || session.loggedOutAt || 0;
  if (time) parts.push(`最近 ${formatTime(time)}`);
  return parts.filter(Boolean).join(" · ");
}

function getSessionTargetUrl(session) {
  return session.recentUrl || getRecentEntryForSession(session)?.url || session.origin || "";
}

function getSessionIconUrl(session) {
  return session.recentUrl || session.origin || `https://${session.siteKey || session.host || ""}`;
}

function openSessionTarget(session) {
  const targetUrl = getSessionTargetUrl(session);
  if (!targetUrl) return;
  window.open(targetUrl, "_blank", "noopener");
}

function getRecentEntryForSession(session) {
  const sessionKey = session.siteKey || session.host || "";
  if (!sessionKey) return null;
  return sortEntries(state.entries).find((entry) => {
    if (entry.kind !== "link" || !entry.url) return false;
    return siteKeyFromUrl(entry.url) === sessionKey;
  }) || null;
}

function getSessionSourceKey(session) {
  if (session.sourceKey) return session.sourceKey;
  const entry = getRecentEntryForSession(session);
  if (entry) return getEntrySourceKey(entry);
  try {
    return detectSource(new URL(getSessionIconUrl(session)));
  } catch {
    return "web";
  }
}

function siteKeyFromUrl(rawUrl) {
  try {
    return new URL(rawUrl).hostname.replace(/^www\./, "").toLowerCase();
  } catch {
    return "";
  }
}

async function handleAdd() {
  if (state.isAdding) return;
  state.isAdding = true;
  updateAddButton();
  clearNotice();

  try {
    const rawText = elements.linkInput.value.trim();

    if (rawText && state.pendingFiles.length > 0) {
      showNotice("链接和文件分开添加。", true);
      return;
    }

    if (rawText) {
      await addLinkEntry(rawText);
      return;
    }

    if (state.pendingFiles.length > 0) {
      await addFileEntries();
      return;
    }

    showNotice("先放一个链接或文件。", true);
  } catch (error) {
    console.error(error);
    showNotice("保存失败，请确认本地服务还在运行。", true);
  } finally {
    state.isAdding = false;
    updateAddButton();
  }
}

async function addLinkEntry(rawText) {
  const urls = extractUrls(rawText);
  if (urls.length === 0) {
    showNotice("没有识别到链接。", true);
    return;
  }

  if (urls.length > 1) {
    showNotice("一次只添加一个链接。", true);
    return;
  }

  const url = normalizeUrl(urls[0]);
  if (!url) {
    showNotice("这个链接看起来不完整。", true);
    return;
  }

  const sourceKey = detectSource(url);
  const source = getSource(sourceKey);
  const entry = {
    id: createId(),
    kind: "link",
    sourceKey,
    sourceName: source.name,
    title: getLinkTitle(rawText, url, sourceKey),
    url: url.href,
    rawText,
    createdAt: Date.now(),
  };

  elements.linkInput.value = "";
  resizeTextInput();
  clearNotice();
  const optimisticEntry = {
    ...entry,
    processingStatus: isDiskStorage() ? "processing" : "",
  };
  showEntryImmediately(optimisticEntry);

  persistLinkEntry(entry);
}

async function addFileEntries() {
  if (!isDiskStorage()) {
    showNotice("请先启动本地服务。文件会复制到 clipy_library/files 文件夹后再保存。", true);
    return;
  }

  const createdAt = Date.now();

  if (state.pendingFiles.length === 1) {
    const file = state.pendingFiles[0];
    const entry = {
      id: createId(),
      kind: "file",
      sourceKey: "file",
      sourceName: "文件",
      title: file.name,
      fileName: file.name,
      fileType: file.type || "未知类型",
      fileSize: file.size,
      fileBlob: file,
      createdAt,
      batchOrder: 0,
    };
    state.pendingFiles = [];
    renderPendingFiles();
    clearNotice();
    const optimisticEntry = {
      ...entry,
      processingStatus: isDiskStorage() ? "uploading" : "",
    };
    showEntryImmediately(optimisticEntry);

    persistSingleFileEntry(file, entry);
    return;
  }

  const batchEntry = buildFileBatchEntry(state.pendingFiles, createdAt);
  const filesToSave = [...state.pendingFiles];
  state.pendingFiles = [];
  renderPendingFiles();
  clearNotice();
  const optimisticEntry = {
    ...batchEntry,
    processingStatus: isDiskStorage() ? "uploading" : "",
  };
  showEntryImmediately(optimisticEntry);

  persistFileBatchEntry(filesToSave, batchEntry);
}

function persistLinkEntry(entry) {
  persistOptimisticEntry(entry, async () => {
    const savedEntry = isDiskStorage() ? await saveDiskLink(entry) : entry;
    if (!isDiskStorage()) await saveEntry(savedEntry);
    return savedEntry;
  });
}

function persistSingleFileEntry(file, entry) {
  persistOptimisticEntry(entry, async () => {
    const savedEntry = isDiskStorage() ? await saveDiskFile(file, entry) : entry;
    if (!isDiskStorage()) await saveEntry(savedEntry);
    return savedEntry;
  });
}

function persistFileBatchEntry(files, entry) {
  persistOptimisticEntry(entry, async () => {
    const savedEntry = isDiskStorage() ? await saveDiskFileBatch(files, entry) : entry;
    if (!isDiskStorage()) await saveEntry(savedEntry);
    return savedEntry;
  });
}

async function persistOptimisticEntry(entry, persist) {
  try {
    const savedEntry = await persist();
    replaceEntryInList(entry.id, savedEntry);
    updateLinkBackup();
    render();
    scheduleDiskRefresh();
  } catch (error) {
    console.error(error);
    markEntryFailed(entry.id, "保存失败，请确认本地服务还在运行");
  }
}

function showEntryImmediately(entry) {
  state.activeFilter = "all";
  state.activeRepositoryId = "";
  state.repositoryEditOpen = false;
  state.entries = sortEntries([entry, ...state.entries.filter((item) => item.id !== entry.id)]);
  updateLinkBackup();
  render();
}

function replaceEntryInList(entryId, nextEntry) {
  state.entries = sortEntries(state.entries.map((item) => (item.id === entryId ? nextEntry : item)));
}

function removeEntryFromList(entryId) {
  state.entries = state.entries.filter((item) => item.id !== entryId);
  removeEntryFromLocalRepositories(entryId);
  render();
}

function markEntryFailed(entryId, message) {
  state.entries = state.entries.map((item) => {
    if (item.id !== entryId) return item;
    return {
      ...item,
      processingStatus: "failed",
      processingError: message,
    };
  });
  render();
}

function buildFileBatchEntry(files, createdAt) {
  const fileRecords = files.map((file, index) => ({
    id: createId(),
    fileName: file.name,
    fileType: file.type || "未知类型",
    fileSize: file.size,
    fileBlob: file,
    batchOrder: index,
  }));

  return {
    id: createId(),
    kind: "file-batch",
    sourceKey: "file",
    sourceName: "文件",
    title: getFileBatchTitle(fileRecords),
    files: fileRecords,
    fileCount: fileRecords.length,
    fileSize: fileRecords.reduce((total, file) => total + file.fileSize, 0),
    createdAt,
  };
}

function getFileBatchTitle(files) {
  const firstFile = files[0];
  if (!firstFile) return "文件";
  if (files.length === 1) return firstFile.fileName || "文件";
  return `${firstFile.fileName || "文件"} 等 ${files.length} 个文件`;
}

function addPendingFiles(fileList) {
  const nextFiles = [...fileList].filter((file) => file && file.size >= 0);
  if (nextFiles.length === 0) return;

  state.pendingFiles = [...state.pendingFiles, ...nextFiles];
  renderPendingFiles();
  showNotice(`已准备 ${state.pendingFiles.length} 个文件。`);
  updateAddButton();
}

function removePendingFile(index) {
  state.pendingFiles.splice(index, 1);
  renderPendingFiles();
  updateAddButton();
  if (state.pendingFiles.length === 0) clearNotice();
}

function renderPendingFiles() {
  elements.filePreview.replaceChildren();

  state.pendingFiles.forEach((file, index) => {
    const chip = elements.fileChipTemplate.content.firstElementChild.cloneNode(true);
    renderFileIcon(chip.querySelector(".file-chip-icon"), {
      kind: "file",
      fileName: file.name,
      fileType: file.type || "未知类型",
      fileSize: file.size,
      fileBlob: file,
    }, undefined, { compact: true });
    chip.querySelector(".file-chip-name").textContent = file.name;
    chip.querySelector("button").addEventListener("click", () => removePendingFile(index));
    elements.filePreview.append(chip);
  });
}

function render() {
  const entries = sortEntries(state.entries);
  const activeRepository = getActiveRepository();
  if (state.activeFilter === "repository" && !activeRepository) {
    state.activeFilter = "all";
    state.activeRepositoryId = "";
    state.repositoryEditOpen = false;
  }
  const filteredEntries = entries.filter((entry) => {
    if (state.activeFilter === "all") return true;
    if (state.activeFilter === "file") return entry.kind === "file" || entry.kind === "file-batch";
    if (state.activeFilter === "repository") return activeRepositoryEntryIds().has(entry.id);
    return entry.kind === state.activeFilter;
  });

  elements.entryCount.textContent = `${entries.length} 条收藏`;
  elements.emptyState.classList.toggle("hidden", filteredEntries.length > 0);
  renderEmptyState(activeRepository);
  elements.timeline.replaceChildren();
  renderRepositoryChips();
  renderRepositoryPanel();

  document.querySelectorAll(".filter-chip").forEach((button) => {
    const isRepositoryChip = Boolean(button.dataset.repositoryId);
    const isActiveRepository = isRepositoryChip && state.activeFilter === "repository" && button.dataset.repositoryId === state.activeRepositoryId;
    const isActiveFilter = !isRepositoryChip && button.dataset.filter === state.activeFilter;
    button.classList.toggle("active", isActiveRepository || isActiveFilter);
  });

  filteredEntries.forEach((entry) => {
    elements.timeline.append(renderTimelineItem(entry));
  });

  updateAddButton();
}

function renderEmptyState(activeRepository) {
  const title = elements.emptyState?.querySelector("h2");
  const copy = elements.emptyState?.querySelector("p");
  if (!title || !copy) return;
  if (state.activeFilter === "repository" && activeRepository) {
    title.textContent = "这个仓库还没有内容";
    copy.textContent = "在上方列表勾选收藏，就会加入这里。";
    return;
  }
  title.textContent = "还没有收藏";
  copy.textContent = "把链接或文件放到上方，就会按时间进入这里。";
}

function renderRepositoryChips() {
  if (!elements.repositoryChips) return;
  const chips = state.repositories.map((repository) => {
    const wrap = document.createElement("span");
    wrap.className = "repository-chip-wrap";
    wrap.classList.toggle("repository-menu-open", state.repositoryMenuId === repository.id);

    const button = document.createElement("button");
    button.className = "filter-chip repository-chip";
    button.type = "button";
    button.dataset.repositoryId = repository.id;
    button.textContent = repository.name;
    button.title = `${repository.name} · ${(repository.entryIds || []).length} 条`;

    const manageButton = document.createElement("button");
    manageButton.className = "repository-chip-manage";
    manageButton.type = "button";
    manageButton.dataset.repositoryManageId = repository.id;
    manageButton.title = `管理 ${repository.name}`;
    manageButton.setAttribute("aria-label", `管理 ${repository.name}`);
    manageButton.setAttribute("aria-expanded", String(state.repositoryMenuId === repository.id));
    manageButton.innerHTML = `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z" />
        <path d="M19 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z" />
        <path d="M5 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z" />
      </svg>
    `;

    const menu = document.createElement("div");
    menu.className = "repository-chip-menu";
    menu.hidden = state.repositoryMenuId !== repository.id;
    menu.innerHTML = `
      <button type="button" data-repository-action="rename" data-repository-id="${repository.id}">重命名</button>
      <button type="button" data-repository-action="delete" data-repository-id="${repository.id}">删除仓库</button>
    `;

    wrap.append(button, manageButton, menu);
    return wrap;
  });
  elements.repositoryChips.replaceChildren(...chips);
}

function renderRepositoryPanel() {
  const repository = getActiveRepository();
  if (!repository || state.activeFilter !== "repository") {
    elements.repositoryPanel?.setAttribute("hidden", "");
    elements.repositoryPanel?.classList.add("hidden");
    elements.repositoryPanelHeader?.setAttribute("aria-expanded", "false");
    elements.repositoryPanelHeader?.classList.remove("repository-panel-header-expanded");
    return;
  }

  elements.repositoryPanel?.removeAttribute("hidden");
  elements.repositoryPanel?.classList.remove("hidden");
  elements.repositoryPanelHeader?.setAttribute("aria-expanded", String(state.repositoryEditOpen));
  elements.repositoryPanelHeader?.classList.toggle("repository-panel-header-expanded", state.repositoryEditOpen);
  const isRenaming = state.repositoryRenamingId === repository.id;
  if (document.activeElement !== elements.repositoryNameInput) {
    elements.repositoryNameInput.value = repository.name;
  }
  elements.repositoryNameInput.readOnly = !isRenaming;
  elements.repositoryNameInput.tabIndex = isRenaming ? 0 : -1;
  elements.repositoryNameInput.classList.toggle("repository-name-input-editing", isRenaming);
  resizeRepositoryNameInput(repository.name, isRenaming);
  const selectedCount = (repository.entryIds || []).length;
  elements.repositoryMeta.textContent = state.repositoryEditOpen
    ? `${selectedCount} 条内容 · 点击这一行收起`
    : `${selectedCount} 条内容 · 点击这一行选择内容`;
  elements.repositoryManageButton?.classList.add("hidden");
  elements.repositoryDoneButton?.classList.add("hidden");
  renderRepositoryPicker(repository);
}

function resizeRepositoryNameInput(name, isRenaming) {
  const input = elements.repositoryNameInput;
  if (!input) return;
  if (isRenaming) {
    input.style.width = "100%";
    return;
  }

  const styles = window.getComputedStyle(input);
  const canvas = resizeRepositoryNameInput.canvas || document.createElement("canvas");
  resizeRepositoryNameInput.canvas = canvas;
  const context = canvas.getContext("2d");
  if (!context) return;
  context.font = styles.font || `${styles.fontWeight} ${styles.fontSize} ${styles.fontFamily}`;
  const textWidth = Math.ceil(context.measureText(name || "新仓库").width);
  const maxWidth = Math.max(48, (elements.repositoryPanelHeader?.clientWidth || 640) - 88);
  input.style.width = `${Math.min(Math.max(textWidth + 4, 44), maxWidth)}px`;
}

function renderRepositoryPicker(repository) {
  if (!elements.repositoryPicker) return;
  elements.repositoryPicker.classList.toggle("hidden", !state.repositoryEditOpen);
  if (!state.repositoryEditOpen) {
    elements.repositoryPicker.replaceChildren();
    return;
  }

  const selectedIds = new Set(repository.entryIds || []);
  const searchShell = document.createElement("div");
  searchShell.className = "repository-picker-search";

  const searchInput = document.createElement("input");
  searchInput.type = "search";
  searchInput.placeholder = "搜索收藏内容";
  searchInput.value = state.repositoryPickerQuery;
  searchInput.setAttribute("aria-label", "搜索仓库可选内容");
  searchInput.addEventListener("input", () => {
    state.repositoryPickerQuery = searchInput.value;
    updateRepositoryPickerSearchResults();
  });
  searchShell.append(searchInput);

  const list = document.createElement("div");
  list.className = "repository-picker-list";

  const rows = sortEntries(state.entries).map((entry) => {
    const row = document.createElement("label");
    row.className = "repository-picker-row";
    row.dataset.search = getRepositoryEntrySearchText(entry);

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selectedIds.has(entry.id);
    checkbox.addEventListener("change", () => updateRepositoryEntrySelection(repository, entry.id, checkbox.checked));

    const iconWrap = document.createElement("span");
    iconWrap.className = "repository-picker-icon";
    const icon = document.createElement("span");
    renderSourceIcon(icon, getEntrySourceKey(entry), entry);
    iconWrap.append(icon);

    const copy = document.createElement("span");
    copy.className = "repository-picker-copy";

    const title = document.createElement("span");
    title.className = "repository-picker-title";
    title.textContent = getEntryTitle(entry, getEntrySourceKey(entry));

    const meta = document.createElement("span");
    meta.className = "repository-picker-meta";
    meta.textContent = getMetaText(entry);

    copy.append(title, meta);
    row.append(checkbox, iconWrap, copy);
    return row;
  });

  const emptySearch = document.createElement("p");
  emptySearch.className = "repository-picker-empty repository-picker-search-empty";
  emptySearch.textContent = "没有匹配的收藏。";

  if (rows.length === 0) {
    const empty = document.createElement("p");
    empty.className = "repository-picker-empty";
    empty.textContent = "还没有可以加入仓库的内容。";
    elements.repositoryPicker.replaceChildren(searchShell, empty);
    return;
  }

  list.append(...rows);
  elements.repositoryPicker.replaceChildren(searchShell, list, emptySearch);
  updateRepositoryPickerSearchResults();
}

function updateRepositoryPickerSearchResults() {
  if (!elements.repositoryPicker) return;
  const query = normalizeSearchText(state.repositoryPickerQuery);
  const rows = [...elements.repositoryPicker.querySelectorAll(".repository-picker-row")];
  let visibleCount = 0;
  rows.forEach((row) => {
    const matches = !query || row.dataset.search.includes(query);
    row.hidden = !matches;
    if (matches) visibleCount += 1;
  });
  const empty = elements.repositoryPicker.querySelector(".repository-picker-search-empty");
  if (empty) empty.hidden = rows.length === 0 || visibleCount > 0;
}

function getRepositoryEntrySearchText(entry) {
  const sourceKey = getEntrySourceKey(entry);
  const parts = [
    getEntryTitle(entry, sourceKey),
    getMetaText(entry),
    getSource(sourceKey).name,
    entry.sourceName,
    entry.url,
    entry.fileName,
    getBatchFiles(entry).map((file) => file.fileName).join(" "),
  ];
  return normalizeSearchText(parts.filter(Boolean).join(" "));
}

function normalizeSearchText(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

async function updateRepositoryEntrySelection(repository, entryId, shouldInclude) {
  const entryIds = new Set(repository.entryIds || []);
  if (shouldInclude) {
    entryIds.add(entryId);
  } else {
    entryIds.delete(entryId);
  }

  const optimistic = {
    ...repository,
    entryIds: [...entryIds],
    updatedAt: Date.now(),
  };
  state.repositories = sortRepositories(state.repositories.map((item) => (item.id === repository.id ? optimistic : item)));
  render();

  try {
    const payload = await requestUpdateRepository(optimistic, { entryIds: optimistic.entryIds });
    state.repositories = sortRepositories(payload.repositories || state.repositories.map((item) => (item.id === repository.id ? payload.repository : item)));
    clearNotice();
    render();
  } catch (error) {
    console.error(error);
    state.repositories = sortRepositories(state.repositories.map((item) => (item.id === repository.id ? repository : item)));
    showNotice("仓库内容保存失败。", true);
    render();
  }
}

function renderTimelineItem(entry) {
  const item = elements.timelineItemTemplate.content.firstElementChild.cloneNode(true);
  const article = item.querySelector(".entry");
  const sourceIcon = item.querySelector(".source-icon");
  const entryDetails = item.querySelector(".entry-details");
  const timelineDate = item.querySelector(".timeline-date");
  const timelineDay = item.querySelector(".timeline-day");
  const timelineHour = item.querySelector(".timeline-hour");
  const title = item.querySelector("h2");
  const meta = item.querySelector(".entry-meta");
  const renameButton = item.querySelector(".entry-rename");
  const deleteButton = item.querySelector(".entry-delete");
  const actions = item.querySelector(".entry-actions");
  const activeRepository = getActiveRepository();
  const isRepositoryView = state.activeFilter === "repository" && Boolean(activeRepository);
  const sourceKey = getEntrySourceKey(entry);
  const sourceName = getSource(sourceKey).name || entry.sourceName || "收藏";
  const timeParts = getTimelineTimeParts(entry.createdAt);

  item.classList.toggle("repository-timeline-item", isRepositoryView);

  renderSourceIcon(sourceIcon, sourceKey, entry);
  timelineDate.dateTime = new Date(entry.createdAt).toISOString();
  timelineDay.textContent = timeParts.day;
  timelineHour.textContent = timeParts.hour;
  renderEntryTitle(title, entry, sourceKey);
  const knowledgeStatus = getKnowledgeStatus(entry, sourceKey);
  meta.textContent = [getMetaText(entry), knowledgeStatus.label].filter(Boolean).join(" · ");
  article.setAttribute("aria-label", `${sourceName}：${title.textContent}`);
  article.classList.toggle("entry-batch", entry.kind === "file-batch");
  article.classList.toggle("entry-processing", knowledgeStatus.kind === "processing");
  article.classList.toggle("entry-muted", knowledgeStatus.kind === "failed");
  article.classList.toggle(
    "entry-warning",
    ["title_fallback", "skipped", "empty", "needs_login", "blocked", "verification_waiting", "verification_expired", "verification_failed"].includes(
      knowledgeStatus.kind,
    ),
  );
  if (knowledgeStatus.label) article.title = knowledgeStatus.label;

  if (shouldShowVerifyAction(entry, knowledgeStatus)) {
    const verifyButton = document.createElement("button");
    verifyButton.className = "entry-action entry-verify";
    verifyButton.type = "button";
    verifyButton.textContent = getVerifyButtonLabel(knowledgeStatus);
    verifyButton.title = "打开 Clipy 浏览器补全内容";
    verifyButton.setAttribute("aria-label", "打开 Clipy 浏览器补全内容");
    verifyButton.addEventListener("click", async (event) => {
      event.stopPropagation();
      await openEntry(entry);
    });
    actions.prepend(verifyButton);
  }

  if (shouldShowReparseAction(entry, knowledgeStatus)) {
    const reparseButton = document.createElement("button");
    reparseButton.className = "entry-action entry-verify";
    reparseButton.type = "button";
    reparseButton.textContent = "重新解析";
    reparseButton.title = "重新抓取并解析正文";
    reparseButton.setAttribute("aria-label", "重新抓取并解析正文");
    reparseButton.addEventListener("click", async (event) => {
      event.stopPropagation();
      await reparseEntry(entry);
    });
    actions.prepend(reparseButton);
  }

  if (entry.kind === "file-batch") {
    renderFileBatchDetails(entryDetails, entry);
    article.setAttribute("aria-expanded", String(state.expandedEntries.has(entry.id)));
  }

  article.addEventListener("click", async (event) => {
    if (event.target.closest(".entry-action, .entry-title-input, .batch-file-row")) return;
    if (entry.kind === "file-batch") {
      toggleBatchEntry(entry, article, entryDetails);
      return;
    }
    await openEntry(entry);
  });
  article.addEventListener("keydown", async (event) => {
    if (event.target.closest(".entry-title-input")) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (entry.kind === "file-batch") {
        toggleBatchEntry(entry, article, entryDetails);
        return;
      }
      await openEntry(entry);
    }
  });

  renameButton.addEventListener("click", async (event) => {
    event.stopPropagation();
    startInlineRename(entry, title);
  });

  if (isRepositoryView) {
    deleteButton.remove();
    const removeButton = document.createElement("button");
    removeButton.className = "repository-entry-remove";
    removeButton.type = "button";
    removeButton.title = "从这个仓库移除";
    removeButton.setAttribute("aria-label", `从仓库移除 ${getEntryTitle(entry, sourceKey)}`);
    removeButton.innerHTML = `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M18 6 6 18M6 6l12 12" />
      </svg>
    `;
    removeButton.addEventListener("click", async (event) => {
      event.stopPropagation();
      await updateRepositoryEntrySelection(activeRepository, entry.id, false);
    });
    item.append(removeButton);
  } else {
    deleteButton.addEventListener("click", async (event) => {
      event.stopPropagation();
      await deleteStoredEntry(entry.id);
      state.entries = state.entries.filter((itemEntry) => itemEntry.id !== entry.id);
      removeEntryFromLocalRepositories(entry.id);
      state.expandedEntries.delete(entry.id);
      updateLinkBackup();
      showNotice("已删除。");
      render();
    });
  }

  return item;
}

function shouldShowVerifyAction(entry, knowledgeStatus) {
  return (
    isDiskStorage() &&
    entry.kind === "link" &&
    ["empty", "needs_login", "blocked", "verification_waiting", "verification_expired", "verification_failed"].includes(knowledgeStatus.kind)
  );
}

function shouldShowReparseAction(entry, knowledgeStatus) {
  return isDiskStorage() && entry.kind === "link" && knowledgeStatus.kind === "failed";
}

function getVerifyButtonLabel(knowledgeStatus) {
  return "打开补全";
}

async function reparseEntry(entry) {
  try {
    const payload = await requestEntryReparse(entry);
    if (payload.entry) {
      state.entries = sortEntries(state.entries.map((item) => (item.id === entry.id ? payload.entry : item)));
    }
    showNotice("已重新解析。");
    render();
    scheduleDiskRefresh();
  } catch (error) {
    console.error(error);
    showNotice("重新解析失败。", true);
  }
}

async function verifyEntry(entry) {
  try {
    const payload = await requestEntryVerification(entry);
    if (payload.entry) {
      state.entries = sortEntries(state.entries.map((item) => (item.id === entry.id ? payload.entry : item)));
    }
    if (payload.verification?.status === "open_failed") {
      showNotice("没有打开验证窗口，请稍后再试。", true);
    } else {
      showNotice("已打开 Clipy 浏览器，请在里面完成验证。");
    }
    render();
    if (payload.verification?.status !== "open_failed") {
      startVerificationPolling(entry.id);
    }
  } catch (error) {
    console.error(error);
    showNotice("没有打开验证窗口，请稍后再试。", true);
  }
}

function startVerificationPolling(entryId, durationMs = 5 * 60 * 1000) {
  if (!isDiskStorage()) return;
  const existing = state.verificationPolls.get(entryId);
  if (existing) window.clearInterval(existing);

  const startedAt = Date.now();
  const timer = window.setInterval(async () => {
    await refreshDiskEntries();
    const entry = state.entries.find((item) => item.id === entryId);
    const contentStatus = entry?.content?.status || "";
    const verificationStatus = entry?.verification?.status || "";
    if (!entry || contentStatus === "ready" || verificationStatus === "complete" || Date.now() - startedAt > durationMs) {
      window.clearInterval(timer);
      state.verificationPolls.delete(entryId);
    }
  }, 2000);
  state.verificationPolls.set(entryId, timer);
}

function startProfileOpenPolling(entryId, durationMs = 5 * 60 * 1000) {
  if (!isDiskStorage()) return;
  const existing = state.profileOpenPolls.get(entryId);
  if (existing) window.clearInterval(existing);

  const startedAt = Date.now();
  const timer = window.setInterval(async () => {
    await refreshDiskEntries();
    const entry = state.entries.find((item) => item.id === entryId);
    const profileStatus = entry?.profileOpen?.status || "";
    if (!entry || ["complete", "open_failed", "waiting_for_login", "closed"].includes(profileStatus) || Date.now() - startedAt > durationMs) {
      window.clearInterval(timer);
      state.profileOpenPolls.delete(entryId);
    }
  }, 3000);
  state.profileOpenPolls.set(entryId, timer);
}

function renderEntryTitle(titleElement, entry, sourceKey) {
  titleElement.replaceChildren();

  if (entry.kind !== "file-batch") {
    titleElement.textContent = getEntryTitle(entry, sourceKey);
    return;
  }

  const titleText = document.createElement("span");
  titleText.className = "entry-title-main";
  titleText.textContent = getBatchTitleBase(entry);

  const titleHint = document.createElement("span");
  titleHint.className = "entry-title-hint";
  titleHint.textContent = `等 ${getBatchFiles(entry).length || entry.fileCount || 0} 个文件`;

  titleElement.append(titleText, titleHint);
}

function renderFileBatchDetails(container, entry) {
  const isExpanded = state.expandedEntries.has(entry.id);
  container.hidden = false;
  container.className = `entry-details ${isExpanded ? "batch-expanded-details" : "batch-preview-details"}`;
  container.replaceChildren();

  if (!isExpanded) {
    renderFileBatchPreview(container, entry);
    return;
  }

  const list = document.createElement("div");
  list.className = "batch-file-list";

  getBatchFiles(entry).forEach((file) => {
    const row = document.createElement("div");
    row.className = "batch-file-row";
    row.setAttribute("role", "button");
    row.tabIndex = 0;
    row.addEventListener("click", (event) => {
      event.stopPropagation();
      openBatchFile(entry, file);
    });
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        event.stopPropagation();
        openBatchFile(entry, file);
      }
    });

    const icon = document.createElement("span");
    renderFileIcon(icon, entry, file, { compact: true });

    const copy = document.createElement("span");
    copy.className = "batch-file-copy";

    const name = document.createElement("span");
    name.className = "batch-file-name";
    name.textContent = file.fileName || "未命名文件";

    const meta = document.createElement("span");
    meta.className = "batch-file-meta";
    meta.textContent = `${formatFileSize(file.fileSize)} · ${fileSource.classifyMime(file.fileType)}`;

    copy.append(name, meta);

    const deleteButton = document.createElement("button");
    deleteButton.className = "batch-file-delete";
    deleteButton.type = "button";
    deleteButton.title = "删除这个文件";
    deleteButton.setAttribute("aria-label", `删除 ${file.fileName || "这个文件"}`);
    deleteButton.innerHTML = `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M3 6h18" />
        <path d="M8 6V4h8v2" />
        <path d="M6 6l1 16h10l1-16" />
        <path d="M10 11v6M14 11v6" />
      </svg>
    `;
    deleteButton.addEventListener("click", async (event) => {
      event.stopPropagation();
      await handleDeleteBatchFile(entry, file);
    });

    row.append(icon, copy, deleteButton);
    list.append(row);
  });

  container.append(list);
}

async function handleDeleteBatchFile(entry, file) {
  try {
    const payload = await deleteBatchFile(entry, file);
    if (!payload.ok) {
      showNotice("没有找到这个文件。", true);
      return;
    }

    if (payload.entry) {
      state.entries = state.entries.map((item) => (item.id === entry.id ? payload.entry : item));
      if (payload.entry.kind !== "file-batch") state.expandedEntries.delete(entry.id);
    } else {
      state.entries = state.entries.filter((item) => item.id !== entry.id);
      removeEntryFromLocalRepositories(entry.id);
      state.expandedEntries.delete(entry.id);
    }

    showNotice("已删除这个文件。");
    render();
  } catch (error) {
    console.error(error);
    showNotice("删除文件失败，请确认本地服务还在运行。", true);
  }
}

function renderFileBatchPreview(container, entry) {
  const files = getBatchFiles(entry);
  const stack = document.createElement("div");
  stack.className = "batch-preview-stack";
  stack.setAttribute("aria-hidden", "true");

  const previewFile = files[1] || files[0];
  if (previewFile) {
    const row = document.createElement("div");
    row.className = "batch-preview-row";

    const icon = document.createElement("span");
    icon.className = "batch-preview-dot";

    const name = document.createElement("span");
    name.className = "batch-preview-name";
    name.textContent = previewFile.fileName || "未命名文件";

    const type = document.createElement("span");
    type.className = "batch-preview-type";
    type.textContent = fileSource.classifyMime(previewFile.fileType);

    row.append(icon, name, type);
    stack.append(row);
  }

  if (files.length > 1) {
    const more = document.createElement("div");
    more.className = "batch-preview-row batch-preview-more";
    more.textContent = `还有 ${files.length - 1} 个文件 · 点击展开`;
    stack.append(more);
  }

  container.append(stack);
}

function startInlineRename(entry, titleElement) {
  const currentTitle = getEditableEntryTitle(entry);
  const input = document.createElement("input");
  input.className = "entry-title-input";
  input.type = "text";
  input.value = currentTitle;
  input.setAttribute("aria-label", "重命名收藏");
  input.setAttribute("maxlength", "240");
  titleElement.replaceWith(input);
  input.focus();
  input.select();

  let finished = false;

  const finish = async (shouldSave) => {
    if (finished) return;
    finished = true;

    const nextTitle = input.value.trim();
    if (!shouldSave || nextTitle === currentTitle) {
      render();
      return;
    }

    if (!nextTitle) {
      showNotice("名称不能为空。", true);
      render();
      return;
    }

    input.disabled = true;

    try {
      const updatedEntry = await renameStoredEntry(entry, nextTitle);
      state.entries = state.entries.map((item) => (item.id === updatedEntry.id ? updatedEntry : item));
      updateLinkBackup();
      showNotice("已重命名。");
      render();
    } catch (error) {
      console.error(error);
      showNotice("重命名失败，请确认本地服务还在运行。", true);
      render();
    }
  };

  input.addEventListener("click", (event) => event.stopPropagation());
  input.addEventListener("keydown", (event) => {
    event.stopPropagation();
    if (event.key === "Enter") {
      event.preventDefault();
      finish(true);
    }
    if (event.key === "Escape") {
      event.preventDefault();
      finish(false);
    }
  });
  input.addEventListener("blur", () => finish(true));
}

function getEditableEntryTitle(entry) {
  if (entry.kind === "file-batch") return getBatchTitleBase(entry);
  return getEntryTitle(entry);
}

function renderSourceIcon(element, sourceKey, entry) {
  if (entry.kind === "file" || entry.kind === "file-batch") {
    renderFileIcon(element, entry);
    return;
  }

  const source = getSource(sourceKey);
  element.className = `source-icon ${source.className}`;
  element.replaceChildren();

  const logoUrls = getLogoCandidates(source, entry);
  if (logoUrls.length > 0) {
    renderLogoImage(element, source, logoUrls);
    return;
  }

  renderFallbackSourceIcon(element, source);
}

function renderLogoImage(element, source, logoUrls) {
  element.classList.add("source-logo");
  const image = document.createElement("img");
  image.alt = "";
  image.decoding = "async";
  image.loading = "lazy";
  let candidateIndex = 0;

  image.addEventListener("error", () => {
    candidateIndex += 1;
    if (logoUrls[candidateIndex]) {
      image.src = logoUrls[candidateIndex];
      return;
    }

    element.classList.remove("source-logo");
    renderFallbackSourceIcon(element, source);
  });

  image.src = logoUrls[candidateIndex];
  element.append(image);
}

function renderFallbackSourceIcon(element, source) {
  element.replaceChildren();

  if (source.iconSvg) {
    element.innerHTML = source.iconSvg;
  } else {
    element.textContent = source.label;
  }
}

function renderFileIcon(element, entry, file = getPrimaryFile(entry), options = {}) {
  const fileKind = getFileKind(file || entry);
  const compactClass = options.compact ? " file-type-icon-compact" : "";
  element.className = `source-icon file-type-icon file-kind-${fileKind}${compactClass}`;
  element.replaceChildren();

  if (fileKind === "image") {
    const previewUrl = getFilePreviewUrl(entry, file);
    if (previewUrl) {
      element.classList.add("source-logo", "file-preview-logo");
      const image = document.createElement("img");
      image.alt = "";
      image.decoding = "async";
      image.loading = "lazy";
      image.src = previewUrl;
      image.addEventListener("load", () => {
        if (previewUrl.startsWith("blob:")) URL.revokeObjectURL(previewUrl);
      }, { once: true });
      image.addEventListener("error", () => renderFileTypeBadge(element, fileKind, file));
      element.append(image);
      appendBatchCountBadge(element, entry, options);
      return;
    }
  }

  renderFileTypeBadge(element, fileKind, file);
  appendBatchCountBadge(element, entry, options);
}

function renderFileTypeBadge(element, fileKind, file) {
  element.classList.remove("source-logo", "file-preview-logo");
  element.replaceChildren();

  const fold = document.createElement("span");
  fold.className = "file-icon-fold";

  const label = document.createElement("span");
  label.className = "file-icon-label";
  label.textContent = getFileKindLabel(fileKind, file);

  element.append(fold, label);
}

function appendBatchCountBadge(element, entry, options = {}) {
  if (options.compact || entry.kind !== "file-batch") return;
  const count = getBatchFiles(entry).length || entry.fileCount || 0;
  if (count < 2) return;

  const badge = document.createElement("span");
  badge.className = "file-batch-count";
  badge.textContent = String(count);
  element.append(badge);
}

function getPrimaryFile(entry) {
  if (entry.kind === "file-batch") return getBatchFiles(entry)[0] || null;
  return entry;
}

function getBatchTitleBase(entry) {
  const fallback = getPrimaryFile(entry)?.fileName || "文件组";
  const title = (entry.title || fallback).trim();
  return title.replace(/\s等\s\d+\s个文件$/, "") || fallback;
}

function getBatchFiles(entry) {
  return [...(entry.files || [])].sort((a, b) => (a.batchOrder || 0) - (b.batchOrder || 0));
}

function getFileKind(file = {}) {
  const type = (file.fileType || "").toLowerCase();
  const name = (file.fileName || file.title || "").toLowerCase();

  if (type.startsWith("image/") || /\.(png|jpe?g|gif|webp|avif|heic|heif|bmp|tiff?)$/i.test(name)) return "image";
  if (type.includes("pdf") || /\.pdf$/i.test(name)) return "pdf";
  if (type.includes("spreadsheet") || type.includes("excel") || /\.(xlsx?|csv|numbers)$/i.test(name)) return "excel";
  if (type.includes("presentation") || type.includes("powerpoint") || /\.(pptx?|key)$/i.test(name)) return "ppt";
  if (type.includes("word") || type.includes("document") || /\.(docx?|pages)$/i.test(name)) return "word";
  if (type.startsWith("video/") || /\.(mp4|mov|m4v|webm|mkv|avi)$/i.test(name)) return "video";
  if (type.startsWith("audio/") || /\.(mp3|wav|m4a|aac|flac|ogg)$/i.test(name)) return "audio";
  if (type.includes("zip") || type.includes("compressed") || /\.(zip|rar|7z|tar|gz)$/i.test(name)) return "archive";
  return "file";
}

function getFileKindLabel(fileKind, file = {}) {
  const labels = {
    pdf: "PDF",
    excel: "XLS",
    word: "DOC",
    ppt: "PPT",
    video: "VID",
    audio: "AUD",
    archive: "ZIP",
    image: "IMG",
    file: getFileExtensionLabel(file),
  };
  return labels[fileKind] || "FILE";
}

function getFileExtensionLabel(file = {}) {
  const name = file.fileName || file.title || "";
  const match = name.match(/\.([a-z0-9]{1,5})$/i);
  return match ? match[1].toUpperCase() : "FILE";
}

function getFilePreviewUrl(entry, file = getPrimaryFile(entry)) {
  if (!file) return "";

  const blob = file.fileBlob || entry.fileBlob;
  if (blob) return URL.createObjectURL(blob);

  if (isDiskStorage()) {
    if (entry.kind === "file-batch") {
      return `${API_ROOT}/files/${encodeURIComponent(entry.id)}/${encodeURIComponent(file.id)}`;
    }
    return `${API_ROOT}/files/${encodeURIComponent(entry.id)}`;
  }

  return "";
}

function getLogoCandidates(source, entry) {
  if (entry.kind !== "link") return [];

  const candidates = [
    ...getCommonLogoCandidates(source.id, entry.url),
    ...(source.logoUrls || []),
  ];

  const dynamicLogoUrl = getDynamicLogoUrl(entry.url);
  if (dynamicLogoUrl) candidates.push(dynamicLogoUrl);

  return [...new Set(candidates.filter(Boolean))];
}

function getDynamicLogoUrl(url) {
  try {
    return `${API_ROOT}/logo?url=${encodeURIComponent(new URL(url).href)}`;
  } catch {
    return "";
  }
}

async function openEntry(entry) {
  if (entry.kind === "link") {
    if (isDiskStorage()) {
      try {
        const payload = await requestEntryProfileOpen(entry);
        if (payload.entry) {
          state.entries = sortEntries(state.entries.map((item) => (item.id === entry.id ? payload.entry : item)));
          render();
        }
        showNotice("已在 Clipy 浏览器打开，页面加载后会尝试更新这条收藏。");
        startProfileOpenPolling(entry.id);
        return;
      } catch (error) {
        console.error(error);
        showNotice("Clipy 浏览器没有打开，先用普通浏览器打开。", true);
      }
    }
    window.open(entry.url, "_blank", "noopener");
    return;
  }

  if (entry.kind === "file-batch") {
    toggleBatchEntry(entry);
    return;
  }

  if (isDiskStorage()) {
    openDiskFile(entry);
    return;
  }

  const blob = entry.fileBlob;
  if (!blob) {
    showNotice("这个文件没有找到。", true);
    return;
  }

  const objectUrl = URL.createObjectURL(blob);
  const opened = window.open(objectUrl, "_blank", "noopener");
  if (!opened) {
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = entry.fileName || entry.title || "clipy-file";
    anchor.click();
  }
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

function toggleBatchEntry(entry, article = null, entryDetails = null) {
  const entryId = typeof entry === "string" ? entry : entry.id;
  if (state.expandedEntries.has(entryId)) {
    state.expandedEntries.delete(entryId);
  } else {
    state.expandedEntries.add(entryId);
  }

  if (article && entryDetails && typeof entry !== "string") {
    renderFileBatchDetails(entryDetails, entry);
    article.setAttribute("aria-expanded", String(state.expandedEntries.has(entryId)));
    return;
  }

  render();
}

function openBatchFile(entry, file) {
  if (isDiskStorage()) {
    openDiskFile(entry, file);
    return;
  }

  const blob = file?.fileBlob;
  if (!blob) {
    showNotice("这个文件没有找到。", true);
    return;
  }

  const objectUrl = URL.createObjectURL(blob);
  const opened = window.open(objectUrl, "_blank", "noopener");
  if (!opened) {
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = file.fileName || "clipy-file";
    anchor.click();
  }
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

async function openDiskFile(entry, file = null) {
  const filePath = file
    ? `${encodeURIComponent(entry.id)}/${encodeURIComponent(file.id)}`
    : encodeURIComponent(entry.id);

  try {
    const response = await fetch(`${API_ROOT}/open/${filePath}`, { method: "POST" });
    if (response.ok) return;
  } catch {
    // Fall through to browser preview.
  }

  window.open(`${API_ROOT}/files/${filePath}`, "_blank", "noopener");
}

function getMetaText(entry) {
  if (entry.kind === "file-batch") {
    const count = getBatchFiles(entry).length || entry.fileCount || 0;
    return `${formatTime(entry.createdAt)} · ${count} 个文件 · ${formatFileSize(entry.fileSize)}`;
  }

  if (entry.kind === "file") {
    return `${formatTime(entry.createdAt)} · ${formatFileSize(entry.fileSize)} · ${fileSource.classifyMime(entry.fileType)}`;
  }

  try {
    const host = new URL(entry.url).hostname.replace(/^www\./, "");
    const sourceKey = getEntrySourceKey(entry);
    const sourceName = getSource(sourceKey).name || entry.sourceName || "网页";
    return `${sourceName} · ${formatTime(entry.createdAt)} · ${host}`;
  } catch {
    return `${entry.sourceName || "网页"} · ${formatTime(entry.createdAt)}`;
  }
}

function getKnowledgeStatus(entry, sourceKey = getEntrySourceKey(entry)) {
  const processingStatus = entry.processingStatus || "";
  const processingStep = entry.processingStep || "";
  const contentStatus = entry.content?.status || "";
  const itemStatus = entry.item?.status || "";
  const verificationStatus = entry.verification?.status || "";

  if (["queued", "processing", "fetching", "parsing", "archiving", "uploading"].includes(processingStatus)) {
    const labels = {
      queued: "排队中",
      processing: entry.kind === "link" ? "读取中" : "解析中",
      fetching: "抓取中",
      parsing: "解析中",
      archiving: "归档中",
      uploading: "上传中",
    };
    return { kind: "processing", label: labels[processingStatus] || labels[processingStep] || "处理中" };
  }

  if (entry.kind === "link" && contentStatus === "ready" && isFallbackTitle(entry, sourceKey)) {
    return { kind: "title_fallback", label: "已保存正文，标题未识别" };
  }

  if (contentStatus === "ready") {
    return { kind: "ready", label: "" };
  }

  if (verificationStatus === "waiting") {
    return { kind: "verification_waiting", label: "等待页面补全" };
  }

  if (verificationStatus === "expired") {
    return { kind: "verification_expired", label: "未获得正文内容，点击补全" };
  }

  if (verificationStatus === "open_failed") {
    return { kind: "verification_failed", label: "未获得正文内容，点击补全" };
  }

  if (processingStatus === "failed" || contentStatus === "failed" || itemStatus === "failed") {
    if (entry.kind === "link") return { kind: "failed", label: "未获得正文内容，点击补全" };
    const reason = entry.processingError || entry.content?.reason || "";
    return { kind: "failed", label: reason ? `保存失败：${shortStatus(reason)}` : "保存失败" };
  }

  if (contentStatus === "empty") {
    return { kind: "empty", label: "未获得正文内容，点击补全" };
  }

  if (contentStatus === "needs_login") {
    return { kind: "needs_login", label: "未获得正文内容，点击补全" };
  }

  if (contentStatus === "blocked") {
    return { kind: "blocked", label: "未获得正文内容，点击补全" };
  }

  if (contentStatus === "skipped_video") {
    return { kind: "skipped", label: "视频正文待接入" };
  }

  if (entry.kind === "link" && !contentStatus && !itemStatus) {
    return { kind: "empty", label: "尚未保存正文" };
  }

  return { kind: "ready", label: "" };
}

function isFallbackTitle(entry, sourceKey = getEntrySourceKey(entry)) {
  const title = (entry.title || "").trim();
  if (!title) return true;

  const genericTitles = new Set([
    "X 收藏",
    "YouTube 视频",
    "YouTube 收藏",
    "Bilibili 视频",
    "公众号文章",
    "大众点评收藏",
    "小红书收藏",
    "微博收藏",
    "网页",
  ]);
  if (genericTitles.has(title)) return true;
  if (sourceKey === "x" && /^@[\w.-]+\s+的帖子$/i.test(title)) return true;
  if (sourceKey === "weibo" && /^微博\s+\d+$/.test(title)) return true;
  if (title.startsWith("mp.weixin.qq.com /")) return true;
  if (title.startsWith("bilibili.com /")) return true;
  if (/^[a-z0-9.-]+\s\/\s/i.test(title)) return true;
  return false;
}

function shortStatus(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > 34 ? `${text.slice(0, 33)}...` : text;
}

function extractUrls(text) {
  const matches = text.match(/(?:https?:\/\/|www\.)[^\s"'<>]+/gi) || [];
  if (matches.length > 0) return matches.map(cleanUrlMatch);

  const trimmed = text.trim();
  const looksLikeDomain = /^(?:[a-z0-9-]+\.)+[a-z]{2,}(?:\/[^\s]*)?$/i.test(trimmed);
  return looksLikeDomain ? [trimmed] : [];
}

function cleanUrlMatch(value) {
  return value.replace(/[，。！？；、）)\]]+$/g, "");
}

function normalizeUrl(value) {
  const withScheme = /^https?:\/\//i.test(value) ? value : `https://${value}`;
  try {
    return new URL(withScheme);
  } catch {
    return null;
  }
}

function sortEntries(entries) {
  return [...entries].sort((a, b) => {
    if (b.createdAt !== a.createdAt) return b.createdAt - a.createdAt;
    return (a.batchOrder || 0) - (b.batchOrder || 0);
  });
}

function sortRepositories(repositories) {
  return [...repositories].sort((a, b) => {
    const left = Number(a.updatedAt || a.createdAt || 0);
    const right = Number(b.updatedAt || b.createdAt || 0);
    return right - left;
  });
}

function getActiveRepository() {
  if (state.activeFilter !== "repository" || !state.activeRepositoryId) return null;
  return state.repositories.find((repository) => repository.id === state.activeRepositoryId) || null;
}

function activeRepositoryEntryIds() {
  const repository = getActiveRepository();
  return new Set(repository?.entryIds || []);
}

function removeEntryFromLocalRepositories(entryId) {
  state.repositories = state.repositories.map((repository) => ({
    ...repository,
    entryIds: (repository.entryIds || []).filter((id) => id !== entryId),
  }));
}

function resizeTextInput() {
  elements.linkInput.style.height = "auto";
  elements.linkInput.style.height = `${elements.linkInput.scrollHeight}px`;
  updateComposerSpacer();
}

function setupFixedComposer() {
  if (!elements.composer || !elements.composerSpacer) return;

  refreshComposerPin();
  window.addEventListener("scroll", updateComposerPin, { passive: true });
  window.addEventListener("resize", () => {
    refreshComposerPin();
  });

  if ("ResizeObserver" in window) {
    const observer = new ResizeObserver(() => updateComposerSpacer());
    observer.observe(elements.composer);
  }
}

function refreshComposerPin() {
  if (!elements.composer || !elements.composerSpacer) return;
  state.composerAnchorTop = getComposerAnchorTop();
  updateComposerPin();
  updateComposerSpacer();
}

function getComposerAnchorTop() {
  const reference = elements.composer.classList.contains("composer-fixed")
    ? elements.composerSpacer
    : elements.composer;
  return reference.getBoundingClientRect().top + window.scrollY;
}

function updateComposerPin() {
  if (!elements.composer || !elements.composerSpacer) return;
  const shouldFix = window.scrollY > state.composerAnchorTop;
  elements.composer.classList.toggle("composer-fixed", shouldFix);
  elements.composerSpacer.hidden = !shouldFix;
  updateComposerSpacer();
}

function updateComposerSpacer() {
  if (!elements.composer || !elements.composerSpacer) return;
  if (!elements.composerSpacer.hidden) {
    elements.composerSpacer.style.height = `${elements.composer.offsetHeight}px`;
  }
}

function updateAddButton() {
  const hasText = elements.linkInput.value.trim().length > 0;
  const hasFiles = state.pendingFiles.length > 0;
  elements.addButton.disabled = state.isAdding || (!hasText && !hasFiles);
  elements.addButton.textContent = state.isAdding ? "添加中" : "添加";
}

function showNotice(message, isError = false) {
  elements.notice.textContent = message;
  elements.notice.classList.toggle("error", isError);
}

function clearNotice() {
  elements.notice.textContent = "";
  elements.notice.classList.remove("error");
}

function formatTime(timestamp) {
  const date = new Date(timestamp);
  const now = new Date();
  const dateOnly = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const nowOnly = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const dayGap = Math.round((nowOnly - dateOnly) / 86_400_000);
  const time = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);

  if (dayGap === 0) return `今天 ${time}`;
  if (dayGap === 1) return `昨天 ${time}`;

  const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    year: date.getFullYear() === now.getFullYear() ? undefined : "numeric",
  });
  return `${dateFormatter.format(date)} ${time}`;
}

function getTimelineTimeParts(timestamp) {
  const date = new Date(timestamp);
  const now = new Date();
  const dateOnly = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const nowOnly = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const dayGap = Math.round((nowOnly - dateOnly) / 86_400_000);
  const hour = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);

  if (dayGap === 0) return { day: "今天", hour };
  if (dayGap === 1) return { day: "昨天", hour };

  const day = new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
  }).format(date);
  return { day, hour };
}

function formatFileSize(size = 0) {
  if (size < 1024) return `${size} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = size / 1024;
  let unitIndex = 0;

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unitIndex]}`;
}

function createId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

setupEvents();
resizeTextInput();
updateAddButton();
setupFixedComposer();
boot();
