(function () {
  "use strict";

  const STORAGE_KEY = "private-onyx:webui-reconnect:v1";
  const SCHEMA_VERSION = 1;
  const RECORD_TTL_MS = 4 * 60 * 60 * 1000;
  const MIN_RECOVERY_INTERVAL_MS = 1500;
  const POLL_DELAYS_MS = [2000, 5000, 15000, 30000, 60000];
  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const SEND_PATH = "/api/chat/send-chat-message";
  const SESSION_PREFIX = "/api/chat/get-chat-session/";
  const INCOGNITO_PREFIX = "/api/chat/end-incognito-session/";
  const allowedRecordKeys = new Set([
    "version",
    "token",
    "sessionId",
    "startedAt",
    "multiModel",
    "hiddenAt",
    "generation",
    "lastRecoveryAt",
    "pollPhase",
    "pollAttempt",
  ]);

  const originalFetch = window.fetch;
  const originalBeacon = navigator.sendBeacon
    ? navigator.sendBeacon.bind(navigator)
    : null;
  let recoveryTimer = null;
  let pollTimer = null;
  let statusInFlight = false;
  let storageUsable = true;
  let reloadingToken = null;

  function now() {
    return Date.now();
  }

  function isUuid(value) {
    return typeof value === "string" && UUID_RE.test(value);
  }

  function validRecord(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    if (Object.keys(value).some((key) => !allowedRecordKeys.has(key))) return false;
    if (value.version !== SCHEMA_VERSION) return false;
    if (typeof value.token !== "string" || value.token.length < 16 || value.token.length > 128) return false;
    if (!isUuid(value.sessionId)) return false;
    if (!Number.isFinite(value.startedAt) || value.startedAt <= 0 || now() - value.startedAt > RECORD_TTL_MS || value.startedAt - now() > 60000) return false;
    if (typeof value.multiModel !== "boolean") return false;
    if (value.hiddenAt !== null && (!Number.isFinite(value.hiddenAt) || value.hiddenAt < value.startedAt)) return false;
    if (!Number.isInteger(value.generation) || value.generation < 0) return false;
    if (value.lastRecoveryAt !== null && (!Number.isFinite(value.lastRecoveryAt) || value.lastRecoveryAt < value.startedAt)) return false;
    if (![null, "poll"].includes(value.pollPhase)) return false;
    if (!Number.isInteger(value.pollAttempt) || value.pollAttempt < 0 || value.pollAttempt > POLL_DELAYS_MS.length) return false;
    return true;
  }

  function removeStored() {
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch (_) {
      storageUsable = false;
    }
  }

  function loadRecord() {
    let raw;
    try {
      raw = sessionStorage.getItem(STORAGE_KEY);
    } catch (_) {
      storageUsable = false;
      return null;
    }
    if (raw === null) return null;
    try {
      const record = JSON.parse(raw);
      if (!validRecord(record)) {
        removeStored();
        return null;
      }
      return record;
    } catch (_) {
      removeStored();
      return null;
    }
  }

  function saveRecord(record) {
    if (!validRecord(record)) return false;
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(record));
      return true;
    } catch (_) {
      storageUsable = false;
      return false;
    }
  }

  function clearMatching(token) {
    const record = loadRecord();
    if (record && record.token === token) removeStored();
  }

  function requestToken() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }

  function requestUrl(input) {
    try {
      const raw = typeof input === "string" || input instanceof URL ? input : input.url;
      return new URL(raw, location.href);
    } catch (_) {
      return null;
    }
  }

  function requestMethod(input, init) {
    return String((init && init.method) || (input && input.method) || "GET").toUpperCase();
  }

  function exactSessionId(pathname, prefix) {
    if (!pathname.startsWith(prefix)) return null;
    const value = pathname.slice(prefix.length);
    return isUuid(value) ? value : null;
  }

  function matchingSameOrigin(url) {
    return url && url.origin === location.origin && !url.search && !url.hash;
  }

  function showManualNotice() {
    if (!document || document.getElementById("private-onyx-reconnect-notice")) return;
    const notice = document.createElement("div");
    notice.id = "private-onyx-reconnect-notice";
    notice.setAttribute("role", "status");
    notice.setAttribute("aria-live", "polite");
    notice.textContent = "Automatic chat reconnection is unavailable. Reload this chat manually if its response disconnects.";
    notice.style.cssText = "position:fixed;right:1rem;bottom:1rem;z-index:2147483647;max-width:24rem;padding:.75rem 1rem;border-radius:.5rem;background:#202124;color:#fff;font:14px/1.4 system-ui,sans-serif;box-shadow:0 2px 12px #0006";
    (document.body || document.documentElement).appendChild(notice);
  }

  function observeSession(sessionId, response) {
    if (!response.ok) {
      if (response.status === 404) {
        const record = loadRecord();
        if (record && record.sessionId === sessionId) removeStored();
      }
      return;
    }
    response.clone().json().then((payload) => {
      const record = loadRecord();
      if (!record || record.sessionId !== sessionId) return;
      if (payload && payload.incognito === true) {
        removeStored();
        cancelPolling();
        return;
      }
      if (!payload || !("current_run" in payload)) return;
      if (payload.current_run == null) {
        if (record.multiModel && record.pollPhase === "poll") {
          removeStored();
          cancelPolling();
          location.reload();
        } else {
          removeStored();
        }
      } else if (record.multiModel && record.pollPhase === "poll") {
        schedulePoll(record);
      }
    }).catch(function () {});
  }

  function instrumentSend(input, init, receiver, args) {
    let payload;
    try {
      if (!init || typeof init.body !== "string") return originalFetch.apply(receiver, args);
      payload = JSON.parse(init.body);
    } catch (_) {
      return originalFetch.apply(receiver, args);
    }
    const sessionId = payload && payload.chat_session_id;
    if (!isUuid(sessionId)) return originalFetch.apply(receiver, args);

    const token = requestToken();
    const record = {
      version: SCHEMA_VERSION,
      token,
      sessionId,
      startedAt: now(),
      multiModel: Array.isArray(payload.llm_overrides) && payload.llm_overrides.length > 0,
      hiddenAt: document.visibilityState === "hidden" ? now() : null,
      generation: 0,
      lastRecoveryAt: null,
      pollPhase: null,
      pollAttempt: 0,
    };
    if (!saveRecord(record)) showManualNotice();

    const signal = init.signal;
    let abortHandler = null;
    if (signal && typeof signal.addEventListener === "function") {
      abortHandler = () => clearMatching(token);
      signal.addEventListener("abort", abortHandler, { once: true });
    }

    let result;
    try {
      result = originalFetch.apply(receiver, args);
    } catch (error) {
      clearMatching(token);
      throw error;
    }
    return Promise.resolve(result).then((response) => {
      if (!response.ok) {
        clearMatching(token);
        return response;
      }
      if (!response.body || typeof TransformStream !== "function" || typeof response.body.pipeThrough !== "function") {
        showManualNotice();
        return response;
      }
      const transparent = new TransformStream({
        transform(chunk, controller) {
          controller.enqueue(chunk);
        },
        flush() {
          clearMatching(token);
          if (signal && abortHandler) signal.removeEventListener("abort", abortHandler);
        },
      });
      const body = response.body.pipeThrough(transparent);
      return new Response(body, {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      });
    }, (error) => {
      // A rejected fetch promise can occur after the server accepted the POST.
      // Retain the marker so wake-up recovery may reconcile without resending.
      throw error;
    });
  }

  window.fetch = function () {
    const args = arguments;
    const input = args[0];
    const init = args[1];
    const url = requestUrl(input);
    const method = requestMethod(input, init);
    if (!url || url.origin !== location.origin) return originalFetch.apply(this, args);

    const incognitoId = method === "POST" && matchingSameOrigin(url)
      ? exactSessionId(url.pathname, INCOGNITO_PREFIX)
      : null;
    if (incognitoId) {
      let result;
      try {
        result = originalFetch.apply(this, args);
      } catch (error) {
        const record = loadRecord();
        if (record && record.sessionId === incognitoId) removeStored();
        throw error;
      }
      const record = loadRecord();
      if (record && record.sessionId === incognitoId) removeStored();
      return result;
    }

    if (method === "POST" && matchingSameOrigin(url) && url.pathname === SEND_PATH) {
      return instrumentSend(input, init, this, args);
    }

    const sessionId = method === "GET" && matchingSameOrigin(url)
      ? exactSessionId(url.pathname, SESSION_PREFIX)
      : null;
    const result = originalFetch.apply(this, args);
    if (sessionId) {
      Promise.resolve(result).then((response) => observeSession(sessionId, response)).catch(function () {});
    }
    return result;
  };

  if (originalBeacon) {
    navigator.sendBeacon = function (input, data) {
      const url = requestUrl(input);
      const sessionId = matchingSameOrigin(url)
        ? exactSessionId(url.pathname, INCOGNITO_PREFIX)
        : null;
      let result;
      try {
        result = originalBeacon(input, data);
      } catch (error) {
        if (sessionId) {
          const record = loadRecord();
          if (record && record.sessionId === sessionId) removeStored();
        }
        throw error;
      }
      if (sessionId) {
        const record = loadRecord();
        if (record && record.sessionId === sessionId) removeStored();
      }
      return result;
    };
  }

  function currentChatId() {
    try {
      return new URL(location.href).searchParams.get("chatId");
    } catch (_) {
      return null;
    }
  }

  function cancelPolling() {
    if (pollTimer !== null) clearTimeout(pollTimer);
    pollTimer = null;
    statusInFlight = false;
  }

  function pollSession(token) {
    if (statusInFlight || document.visibilityState !== "visible" || navigator.onLine === false) return;
    const record = loadRecord();
    if (!record || record.token !== token || record.pollPhase !== "poll" || currentChatId() !== record.sessionId) return;
    statusInFlight = true;
    originalFetch.call(window, SESSION_PREFIX + record.sessionId, { method: "GET" }).then((response) => {
      statusInFlight = false;
      observeSession(record.sessionId, response);
    }, () => {
      statusInFlight = false;
      schedulePoll(record);
    });
  }

  function schedulePoll(record) {
    if (pollTimer !== null || document.visibilityState !== "visible" || navigator.onLine === false) return;
    const current = loadRecord();
    if (!current || current.token !== record.token || current.pollPhase !== "poll") return;
    const index = Math.min(current.pollAttempt, POLL_DELAYS_MS.length - 1);
    current.pollAttempt = Math.min(current.pollAttempt + 1, POLL_DELAYS_MS.length);
    if (!saveRecord(current)) return;
    pollTimer = setTimeout(() => {
      pollTimer = null;
      pollSession(current.token);
    }, POLL_DELAYS_MS[index]);
  }

  function recover() {
    recoveryTimer = null;
    const record = loadRecord();
    if (!record || document.visibilityState !== "visible" || navigator.onLine === false) return;
    if (currentChatId() !== record.sessionId) return;
    if (record.multiModel && record.pollPhase === "poll") {
      pollSession(record.token);
      return;
    }
    if (record.hiddenAt === null) return;
    const elapsed = record.lastRecoveryAt === null ? Infinity : now() - record.lastRecoveryAt;
    if (elapsed < MIN_RECOVERY_INTERVAL_MS) {
      recoveryTimer = setTimeout(recover, MIN_RECOVERY_INTERVAL_MS - elapsed);
      return;
    }
    record.hiddenAt = null;
    record.generation += 1;
    record.lastRecoveryAt = now();
    if (record.multiModel) {
      record.pollPhase = "poll";
      record.pollAttempt = 0;
    }
    if (!saveRecord(record)) {
      showManualNotice();
      return;
    }
    reloadingToken = record.token;
    location.reload();
  }

  function scheduleRecovery() {
    if (recoveryTimer === null) recoveryTimer = setTimeout(recover, 0);
  }

  document.addEventListener("visibilitychange", () => {
    const record = loadRecord();
    if (!record) return;
    if (document.visibilityState === "hidden") {
      record.hiddenAt = now();
      saveRecord(record);
      cancelPolling();
    } else {
      scheduleRecovery();
    }
  });
  window.addEventListener("pagehide", () => {
    const record = loadRecord();
    if (record && record.token !== reloadingToken) {
      record.hiddenAt = now();
      saveRecord(record);
    }
    cancelPolling();
  });
  window.addEventListener("pageshow", (event) => {
    const record = loadRecord();
    if (record && (event.persisted || record.hiddenAt !== null)) scheduleRecovery();
  });
  window.addEventListener("online", () => {
    const record = loadRecord();
    if (!record) return;
    if (record.hiddenAt === null) {
      record.hiddenAt = now();
      saveRecord(record);
    }
    scheduleRecovery();
  });

  if (!storageUsable) showManualNotice();
  if (window.__PRIVATE_ONYX_RECONNECT_TEST__ === true) {
    window.__privateOnyxReconnectTest = {
      STORAGE_KEY,
      loadRecord,
      saveRecord,
      recover,
      pollSession,
      constants: { RECORD_TTL_MS, MIN_RECOVERY_INTERVAL_MS, POLL_DELAYS_MS },
    };
  }
})();
