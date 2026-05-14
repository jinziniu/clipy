#!/usr/bin/env node
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const url = process.argv[2] || "";
const profileDir = process.argv[3] || "";
const port = Number(process.env.CLIPY_BROWSER_PORT || 9333);
const headless = process.env.CLIPY_BROWSER_HEADLESS !== "0";
const preferExistingTab = process.env.CLIPY_BROWSER_PREFER_EXISTING_TAB === "1";
const requireExistingTab = process.env.CLIPY_BROWSER_REQUIRE_EXISTING_TAB === "1";
const requireExistingBrowser = process.env.CLIPY_BROWSER_REQUIRE_EXISTING_BROWSER === "1";
const navigationTimeoutMs = Number(process.env.CLIPY_BROWSER_NAV_TIMEOUT_MS || 18000);
const settleMs = Number(process.env.CLIPY_BROWSER_SETTLE_MS || 1800);

if (!url || !profileDir) {
  writeJson({ status: "failed", reason: "missing url or profileDir" }, 2);
}

main().catch((error) => {
  writeJson({ status: "failed", reason: String(error && error.message ? error.message : error) }, 1);
});

async function main() {
  fs.mkdirSync(profileDir, { recursive: true });
  if ((requireExistingBrowser || requireExistingTab) && !(await devtoolsAlive())) {
    return writeJson({
      status: "failed",
      method: "browser_profile",
      reason: "Clipy 浏览器已关闭，停止后台补全",
      fetchedAt: Date.now(),
      browserClosed: true,
    });
  }

  const chromeState = await ensureChrome();

  const existingTarget = preferExistingTab ? await findExistingTarget(url) : null;
  if (preferExistingTab && requireExistingTab && !existingTarget) {
    return writeJson({
      status: "failed",
      method: "browser_profile",
      reason: "当前链接的 Clipy 浏览器标签页已关闭，停止后台补全",
      fetchedAt: Date.now(),
      existingTargetMissing: true,
    });
  }

  const target = existingTarget || await createTarget("about:blank");
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.connect();

  try {
    await client.send("Page.enable");
    await client.send("Runtime.enable");

    if (!target.reusedExistingTarget) {
      const loadPromise = client.waitFor("Page.loadEventFired", navigationTimeoutMs).catch(() => null);
      await client.send("Page.navigate", { url });
      await Promise.race([loadPromise, delay(navigationTimeoutMs)]);
      await delay(settleMs);
    }

    const extracted = await evaluatePage(client);
    const status = classifyPage(extracted);
    writeJson({
      status,
      method: "browser_profile",
      headless: chromeState.headless,
      launchMode: chromeState.launchMode,
      reusedBrowser: chromeState.reused,
      reusedExistingTarget: Boolean(target.reusedExistingTarget),
      profileDir,
      fetchedAt: Date.now(),
      ...extracted,
    });
  } finally {
    client.close();
    if (!target.reusedExistingTarget) closeTarget(target.id).catch(() => {});
  }
}

async function ensureChrome() {
  if (await devtoolsAlive()) return { reused: true, launchMode: "existing", headless: null };

  const chromePath = findChromePath();
  if (!chromePath) {
    throw new Error("Chrome not found. Set CHROME_PATH to the Chrome executable.");
  }

  removeStaleSingletonFiles(profileDir);

  const args = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profileDir}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-popup-blocking",
    "--window-size=1280,900",
  ];

  if (headless) {
    args.push("--headless=new", "--disable-gpu");
  }

  args.push("about:blank");
  const child = spawn(chromePath, args, { detached: true, stdio: "ignore" });
  child.unref();

  const start = Date.now();
  while (Date.now() - start < 9000) {
    if (await devtoolsAlive()) return { reused: false, launchMode: headless ? "headless" : "visible", headless };
    await delay(250);
  }
  throw new Error("Chrome DevTools did not start");
}

function removeStaleSingletonFiles(profilePath) {
  for (const name of ["SingletonLock", "SingletonSocket", "SingletonCookie"]) {
    try {
      const target = path.join(profilePath, name);
      fs.lstatSync(target);
      fs.rmSync(target, { force: true });
    } catch {
      // Best effort only. Chrome can still report a clearer startup failure.
    }
  }
}

function findChromePath() {
  const candidates = [
    process.env.CHROME_PATH,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
  ].filter(Boolean);

  return candidates.find((candidate) => fs.existsSync(candidate)) || "";
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
  if (!response.ok) {
    response = await fetch(endpoint);
  }
  if (!response.ok) {
    throw new Error(`cannot create browser target: ${response.status}`);
  }
  return response.json();
}

async function findExistingTarget(rawUrl) {
  let targets = [];
  try {
    const response = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(1000) });
    if (response.ok) targets = await response.json();
  } catch {
    targets = [];
  }

  const pages = targets.filter((item) => item.type === "page" && item.webSocketDebuggerUrl && item.url && item.url !== "about:blank");
  const target = pages.find((item) => urlsMatch(item.url || "", rawUrl));
  return target ? { ...target, reusedExistingTarget: true } : null;
}

function urlsMatch(left, right) {
  const a = normalizeComparableUrl(left);
  const b = normalizeComparableUrl(right);
  if (!a || !b) return false;
  return a.host === b.host && a.pathname === b.pathname;
}

function normalizeComparableUrl(value) {
  try {
    const parsed = new URL(value);
    let host = parsed.hostname.toLowerCase();
    if (host.startsWith("www.")) host = host.slice(4);
    let pathname = decodeURIComponent(parsed.pathname || "/").replace(/\/+$/, "");
    if (!pathname) pathname = "/";
    return { host, pathname };
  } catch {
    return null;
  }
}

async function closeTarget(targetId) {
  await fetch(`http://127.0.0.1:${port}/json/close/${encodeURIComponent(targetId)}`);
}

async function evaluatePage(client) {
  const expression = `(async () => {
    const clean = (value) => String(value || "")
      .replace(/\\u00a0/g, " ")
      .replace(/[\\t ]+/g, " ")
      .replace(/\\n[\\t ]+/g, "\\n")
      .replace(/\\n{3,}/g, "\\n\\n")
      .trim();
    const meta = (selector) => document.querySelector(selector)?.getAttribute("content")?.trim() || "";
    const title = clean(
      meta('meta[property="og:title"]') ||
      meta('meta[name="twitter:title"]') ||
      meta('meta[itemprop="name"]') ||
      document.title
    );
    const author = clean(
      meta('meta[name="author"]') ||
      meta('meta[property="article:author"]') ||
      meta('meta[name="weixin:author"]')
    );
    const publishedAt = clean(
      meta('meta[property="article:published_time"]') ||
      meta('meta[name="publishdate"]') ||
      meta('meta[name="pubdate"]') ||
      meta('meta[name="date"]')
    );
    const answerMatch = location.pathname.match(/\\/answer\\/(\\d+)/);
    const zhihuAnswer = answerMatch
      ? document.querySelector('.ContentItem.AnswerItem[name="' + answerMatch[1] + '"]')
      : null;
    const zhihuContent = zhihuAnswer?.querySelector('.RichContent-inner, .RichText, [itemprop="text"]') || null;
    const selectors = [
      "#js_content",
      "#readme .markdown-body",
      "article.markdown-body",
      ".markdown-body",
      ".QuestionHeader-title",
      ".QuestionRichText",
      ".QuestionAnswer-content",
      ".AnswerItem .RichContent-inner",
      ".AnswerItem .RichText",
      ".Post-RichText",
      ".ContentItem",
      ".RichContent-inner",
      ".RichText",
      "article",
      "main",
      "[role='main']",
      ".article",
      ".post",
      ".entry-content",
      ".rich_media_content",
      ".article-content",
      ".body-content"
    ];
    const blocks = [zhihuContent]
      .filter(Boolean)
      .map((element) => clean(element.innerText || ""))
      .filter((text) => text.length > 40)
      .concat(selectors
      .map((selector) => document.querySelector(selector))
      .filter(Boolean)
      .map((element) => clean(element.innerText || ""))
      .filter((text) => text.length > 40)
      .sort((a, b) => b.length - a.length));
    const tweetText = Array.from(document.querySelectorAll('[data-testid="tweetText"]'))
      .map((element) => clean(element.innerText || ""))
      .filter(Boolean)
      .join("\\n\\n");
    const bodyText = clean(document.body?.innerText || "");
    const text = clean(tweetText || blocks[0] || bodyText).slice(0, 450000);
    const html = String(document.documentElement?.outerHTML || "").slice(0, 1500000);
    const imageRoot = zhihuContent || document;
    const images = Array.from(imageRoot.images || imageRoot.querySelectorAll?.("img") || [])
      .map((image) => ({
        url: image.currentSrc || image.src || image.getAttribute("data-src") || image.getAttribute("data-original") || "",
        alt: clean(image.alt || image.getAttribute("aria-label") || "图片"),
        width: image.naturalWidth || image.width || 0,
        height: image.naturalHeight || image.height || 0
      }))
      .filter((image) => image.url && !image.url.startsWith("data:"))
      .filter((image, index, array) => array.findIndex((item) => item.url === image.url) === index)
      .slice(0, 24);
    const shouldInlineImage = (url) => {
      try {
        const parsed = new URL(url, location.href);
        return /(^|\\.)xhscdn\\.com$/i.test(parsed.hostname) || /(^|\\.)xiaohongshu\\.com$/i.test(parsed.hostname);
      } catch {
        return false;
      }
    };
    const arrayBufferToBase64 = (buffer) => {
      const bytes = new Uint8Array(buffer);
      const chunkSize = 0x8000;
      let binary = "";
      for (let index = 0; index < bytes.length; index += chunkSize) {
        binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
      }
      return btoa(binary);
    };
    const inlineImages = await Promise.all(images.slice(0, 12).map(async (image) => {
      if (!shouldInlineImage(image.url)) return image;
      try {
        const response = await fetch(image.url, {
          credentials: "include",
          referrer: location.href,
          signal: AbortSignal.timeout(7000)
        });
        if (!response.ok) return image;
        const contentType = (response.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
        if (!contentType.startsWith("image/")) return image;
        const blob = await response.blob();
        if (blob.size > 10000000) return image;
        return {
          ...image,
          contentType,
          dataUrl: \`data:\${contentType};base64,\${arrayBufferToBase64(await blob.arrayBuffer())}\`
        };
      } catch {
        return image;
      }
    }));
    for (const inlineImage of inlineImages) {
      const index = images.findIndex((image) => image.url === inlineImage.url);
      if (index >= 0) images[index] = inlineImage;
    }
    return {
      title,
      documentTitle: clean(document.title),
      finalUrl: location.href,
      text,
      html,
      images,
      author,
      publishedAt,
      description: clean(meta('meta[name="description"]') || meta('meta[property="og:description"]')),
      textLength: text.length,
      htmlLength: html.length,
      bodySample: bodyText.slice(0, 1200)
    };
  })()`;

  const response = await client.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
    timeout: 7000,
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.text || "Runtime.evaluate failed");
  }
  return response.result?.value || {};
}

function classifyPage(page) {
  const combined = `${page.documentTitle || ""}\n${page.title || ""}\n${page.bodySample || ""}`.toLowerCase();
  const title = `${page.title || ""} ${page.documentTitle || ""}`.trim();
  const text = page.text || "";
  let finalUrl = null;
  try {
    finalUrl = new URL(page.finalUrl || "");
  } catch {
    finalUrl = null;
  }
  const isAuthPage = finalUrl
    ? /\/(login|signin|sign-in|signup|register|auth|account)\b/i.test(finalUrl.pathname)
    : false;
  const hasSubstantialReadableContent =
    title.length > 20 &&
    !isAuthPage &&
    ((page.textLength || 0) >= 1200 || ((page.images || []).length > 0 && (page.textLength || 0) >= 450));
  const hasArticleLikeContent =
    title.length > 20 &&
    (page.textLength || 0) >= 500 &&
    /\\bby\\b|作者|photographer|published|\\d{4}|gmt|正文|article/i.test(text);
  const loginSignals = [
    "sign in",
    "log in",
    "login",
    "登录",
    "请先登录",
    "注册/登录",
    "需要登录",
    "verify your identity",
    "authentication",
  ];
  const paywallSignals = [
    "subscribe to continue",
    "sign in to continue",
    "sign in to read",
    "already a subscriber",
    "continue reading with a subscription",
    "to read the full story",
    "subscribe to unlock this article",
    "try unlimited access",
    "only $1 for 4 weeks",
    "keep reading for $1",
    "subscribe now",
  ];
  const blockedSignals = [
    "are you a robot",
    "access denied",
    "forbidden",
    "unusual traffic",
    "captcha",
    "please wait for verification",
    "请完成安全验证",
    "访问过于频繁",
    "安全限制",
    "ip存在风险",
    "ip 存在风险",
  ];

  if (blockedSignals.some((signal) => combined.includes(signal))) return "blocked";
  if (paywallSignals.some((signal) => combined.includes(signal))) return "login_required";
  if (hasSubstantialReadableContent) return "ready";
  if (loginSignals.some((signal) => combined.includes(signal)) && !hasArticleLikeContent) return "login_required";
  if (page.title || page.documentTitle || page.textLength || (page.images || []).length) return "ready";
  return "empty";
}

class CdpClient {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.ws = null;
    this.nextId = 1;
    this.pending = new Map();
    this.waiters = new Map();
  }

  connect() {
    return new Promise((resolve, reject) => {
      if (!globalThis.WebSocket) {
        reject(new Error("WebSocket is not available in this Node.js runtime"));
        return;
      }
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
    if (data.id && this.pending.has(data.id)) {
      const { resolve, reject, timer } = this.pending.get(data.id);
      clearTimeout(timer);
      this.pending.delete(data.id);
      if (data.error) reject(new Error(data.error.message || JSON.stringify(data.error)));
      else resolve(data.result || {});
      return;
    }

    if (data.method && this.waiters.has(data.method)) {
      const waiters = this.waiters.get(data.method);
      this.waiters.delete(data.method);
      for (const waiter of waiters) {
        clearTimeout(waiter.timer);
        waiter.resolve(data.params || {});
      }
    }
  }

  send(method, params = {}, timeoutMs = 12000) {
    const id = this.nextId++;
    const payload = JSON.stringify({ id, method, params });
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} timed out`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.ws.send(payload);
    });
  }

  waitFor(method, timeoutMs) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const waiters = (this.waiters.get(method) || []).filter((waiter) => waiter.timer !== timer);
        if (waiters.length) this.waiters.set(method, waiters);
        else this.waiters.delete(method);
        reject(new Error(`${method} timed out`));
      }, timeoutMs);
      const waiters = this.waiters.get(method) || [];
      waiters.push({ resolve, timer });
      this.waiters.set(method, waiters);
    });
  }

  close() {
    if (this.ws) this.ws.close();
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function writeJson(value, exitCode = 0) {
  process.stdout.write(`${JSON.stringify(value)}\n`, () => process.exit(exitCode));
}
