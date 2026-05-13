#!/usr/bin/env python3
from pathlib import Path
import json
import os
import subprocess
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
BROWSER_SCRIPT = ROOT / "browser_profile_fetch.js"
BROWSER_PORT = 9333


def fetch_with_browser_profile(
    url,
    data_dir,
    timeout=35,
    reuse_existing=True,
    prefer_existing_tab=False,
    require_existing_browser=False,
    require_existing_tab=False,
):
    profile_dir = Path(data_dir) / "browser_profile"
    if not BROWSER_SCRIPT.exists():
        return {
            "status": "failed",
            "method": "browser_profile",
            "reason": "browser_profile_fetch.js 不存在",
            "fetchedAt": int(time.time() * 1000),
        }

    if require_existing_browser and not devtools_alive(BROWSER_PORT):
        return {
            "status": "failed",
            "method": "browser_profile",
            "reason": "Clipy 浏览器已关闭，停止后台补全",
            "fetchedAt": int(time.time() * 1000),
            "browserClosed": True,
        }

    if not reuse_existing and devtools_alive(BROWSER_PORT) and has_visible_browser_profile(profile_dir):
        return {
            "status": "failed",
            "method": "browser_profile",
            "reason": "Clipy 可见浏览器正在打开，后台读取不会复用它新开标签",
            "fetchedAt": int(time.time() * 1000),
            "visibleBrowserOpen": True,
        }

    env = dict(os.environ)
    env["CLIPY_BROWSER_REUSE_EXISTING"] = "1" if reuse_existing else "0"
    env["CLIPY_BROWSER_PREFER_EXISTING_TAB"] = "1" if prefer_existing_tab else "0"
    env["CLIPY_BROWSER_REQUIRE_EXISTING_TAB"] = "1" if require_existing_tab else "0"
    env["CLIPY_BROWSER_REQUIRE_EXISTING_BROWSER"] = "1" if require_existing_browser else "0"

    try:
        result = subprocess.run(
            ["node", "--experimental-websocket", str(BROWSER_SCRIPT), url, str(profile_dir)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError:
        return {
            "status": "failed",
            "method": "browser_profile",
            "reason": "本机没有找到 Node.js，无法启动浏览器 profile 读取",
            "fetchedAt": int(time.time() * 1000),
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "failed",
            "method": "browser_profile",
            "reason": "浏览器读取超时",
            "fetchedAt": int(time.time() * 1000),
        }

    payload = (result.stdout or "").strip()
    if "\n" in payload:
        payload = payload.split("\n")[-1]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        data = {
            "status": "failed",
            "method": "browser_profile",
            "reason": (result.stderr or result.stdout or "浏览器读取没有返回 JSON").strip()[:300],
        }

    if result.returncode != 0 and data.get("status") != "failed":
        data["status"] = "failed"
        data["reason"] = (result.stderr or f"browser fetch exited with {result.returncode}").strip()[:300]

    data.setdefault("method", "browser_profile")
    data.setdefault("fetchedAt", int(time.time() * 1000))
    return data


def summarize_browser_result(result):
    result = dict(result or {})
    return {
        "status": result.get("status") or "failed",
        "method": result.get("method") or "browser_profile",
        "title": result.get("title") or result.get("documentTitle") or "",
        "documentTitle": result.get("documentTitle") or "",
        "finalUrl": result.get("finalUrl") or "",
        "textLength": result.get("textLength") or len(result.get("text") or ""),
        "imageCount": len(result.get("images") or []),
        "reason": result.get("reason") or "",
        "fetchedAt": result.get("fetchedAt") or int(time.time() * 1000),
        "headless": result.get("headless"),
        "launchMode": result.get("launchMode"),
        "reusedBrowser": result.get("reusedBrowser"),
        "reusedExistingTarget": result.get("reusedExistingTarget"),
        "browserClosed": result.get("browserClosed"),
        "existingTargetMissing": result.get("existingTargetMissing"),
        "visibleBrowserOpen": result.get("visibleBrowserOpen"),
    }


def open_visible_browser_profile(url, data_dir, port=BROWSER_PORT):
    profile_dir = (Path(data_dir) / "browser_profile").resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    stopped_headless = stop_headless_browser_profile(profile_dir, port)
    if devtools_alive(port):
        opened = open_devtools_target(url, port)
        if opened:
            return {
                "ok": True,
                "method": "browser_profile_visible",
                "profileDir": str(profile_dir),
                "port": port,
                "reusedBrowser": True,
                "stoppedHeadless": stopped_headless,
                "openedUrl": url,
            }

    remove_stale_singleton_files(profile_dir)
    chrome_path = find_chrome_path()
    if not chrome_path:
        return {
            "ok": False,
            "method": "browser_profile_visible",
            "reason": "没有找到 Chrome",
            "profileDir": str(profile_dir),
        }

    args = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        "--new-window",
        url,
    ]
    try:
        subprocess.Popen(args, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except Exception as error:
        return {
            "ok": False,
            "method": "browser_profile_visible",
            "reason": str(error)[:200],
            "profileDir": str(profile_dir),
        }

    start = time.time()
    while time.time() - start < 10:
        if devtools_alive(port):
            return {
                "ok": True,
                "method": "browser_profile_visible",
                "profileDir": str(profile_dir),
                "port": port,
                "reusedBrowser": False,
                "stoppedHeadless": stopped_headless,
                "openedUrl": url,
            }
        time.sleep(0.25)

    return {
        "ok": False,
        "method": "browser_profile_visible",
        "reason": "可见 Chrome 已启动，但 DevTools 端口没有响应",
        "profileDir": str(profile_dir),
    }


def devtools_alive(port=BROWSER_PORT):
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def open_devtools_target(url, port=BROWSER_PORT):
    endpoint = f"http://127.0.0.1:{port}/json/new?{quote(url, safe='')}"
    for method in ("PUT", "GET"):
        try:
            request = Request(endpoint, method=method)
            with urlopen(request, timeout=4) as response:
                response.read()
            if response.status == 200:
                return True
        except Exception:
            continue
    return False


def stop_headless_browser_profile(profile_dir, port=BROWSER_PORT):
    stopped = []
    for pid, command in browser_profile_processes(profile_dir):
        if "--headless" not in command and "--headless=new" not in command:
            continue
        try:
            os.kill(pid, 15)
            stopped.append(pid)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass

    if stopped:
        end = time.time() + 5
        while time.time() < end:
            if not any(pid_exists(pid) for pid in stopped):
                break
            time.sleep(0.2)
    return stopped


def browser_profile_processes(profile_dir):
    profile_text = str(profile_dir)
    relative_profile_text = str(Path("clipy_library") / "browser_profile")
    try:
        output = subprocess.run(["ps", "-ef"], text=True, capture_output=True, timeout=4).stdout
    except Exception:
        return []

    processes = []
    for line in output.splitlines():
        if "Google Chrome" not in line:
            continue
        if profile_text not in line and relative_profile_text not in line:
            continue
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        processes.append((pid, parts[7]))
    return processes


def has_visible_browser_profile(profile_dir):
    for _pid, command in browser_profile_processes(profile_dir):
        if "--headless" not in command and "--headless=new" not in command:
            return True
    return False


def pid_exists(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def remove_stale_singleton_files(profile_dir):
    if browser_profile_processes(profile_dir):
        return
    for name in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
        path = profile_dir / name
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
        except Exception:
            pass


def find_chrome_path():
    candidates = [
        os.environ.get("CHROME_PATH"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return ""
