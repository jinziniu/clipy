#!/usr/bin/env node
const siteKey = process.argv[2] || "";
const port = Number(process.env.CLIPY_BROWSER_PORT || 9333);

if (!siteKey) {
  writeJson({ ok: false, reason: "missing siteKey" }, 2);
}

main().catch((error) => writeJson({ ok: false, reason: String(error && error.message ? error.message : error) }, 1));

async function main() {
  if (!(await devtoolsAlive())) {
    writeJson({ ok: false, reason: "DevTools is not running" });
    return;
  }

  const target = await createTarget("about:blank");
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.connect();

  const origins = buildOrigins(siteKey);
  const results = [];
  try {
    await client.send("Storage.enable").catch(() => {});
    for (const origin of origins) {
      try {
        await client.send("Storage.clearDataForOrigin", {
          origin,
          storageTypes: "cookies,local_storage,session_storage,indexeddb,cache_storage,service_workers,websql",
        });
        results.push({ origin, ok: true });
      } catch (error) {
        results.push({ origin, ok: false, reason: error.message });
      }
    }
  } finally {
    client.close();
    closeTarget(target.id).catch(() => {});
  }

  writeJson({ ok: results.some((item) => item.ok), siteKey, results });
}

function buildOrigins(siteKey) {
  const clean = siteKey.replace(/^\.+/, "").toLowerCase();
  const hosts = new Set([clean]);
  if (!clean.startsWith("www.")) hosts.add(`www.${clean}`);
  return [...hosts].flatMap((host) => [`https://${host}`, `http://${host}`]);
}

async function devtoolsAlive() {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(700) });
    return response.ok;
  } catch {
    return false;
  }
}

async function createTarget(initialUrl) {
  const endpoint = `http://127.0.0.1:${port}/json/new?${encodeURIComponent(initialUrl)}`;
  let response = await fetch(endpoint, { method: "PUT" });
  if (!response.ok) response = await fetch(endpoint);
  if (!response.ok) throw new Error(`cannot create browser target: ${response.status}`);
  return response.json();
}

async function closeTarget(targetId) {
  await fetch(`http://127.0.0.1:${port}/json/close/${encodeURIComponent(targetId)}`);
}

class CdpClient {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.ws = null;
    this.nextId = 1;
    this.pending = new Map();
  }

  connect() {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.wsUrl);
      this.ws.onopen = () => resolve();
      this.ws.onerror = () => reject(new Error("WebSocket connection failed"));
      this.ws.onmessage = (event) => this.handleMessage(event.data);
      this.ws.onclose = () => {
        for (const { reject: rejectPending } of this.pending.values()) {
          rejectPending(new Error("WebSocket closed"));
        }
        this.pending.clear();
      };
    });
  }

  handleMessage(message) {
    const data = JSON.parse(message);
    if (!data.id || !this.pending.has(data.id)) return;
    const { resolve, reject, timer } = this.pending.get(data.id);
    clearTimeout(timer);
    this.pending.delete(data.id);
    if (data.error) reject(new Error(data.error.message || JSON.stringify(data.error)));
    else resolve(data.result || {});
  }

  send(method, params = {}, timeoutMs = 12000) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} timed out`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  close() {
    if (this.ws) this.ws.close();
  }
}

function writeJson(value, exitCode = 0) {
  process.stdout.write(`${JSON.stringify(value)}\n`, () => process.exit(exitCode));
}
