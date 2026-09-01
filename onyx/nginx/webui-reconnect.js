(function () {
  "use strict";

  const STORAGE_KEY = "private-onyx:webui-reconnect:v2";
  const SCHEMA_VERSION = 2;
  const RECORD_TTL_MS = 4 * 60 * 60 * 1000;
  const PENDING_RESERVATION_GRACE_MS = 10 * 60 * 1000;
  const MIN_RECOVERY_INTERVAL_MS = 1500;
  const POLL_DELAYS_MS = [2000, 5000, 15000, 30000, 60000];
  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const SEND_PATH = "/api/chat/send-chat-message";
  const SESSION_PREFIX = "/api/chat/reconnect-status/";
  const RESUME_PREFIX = "/api/chat/chat-session/";
  const RESUME_SUFFIX = "/resume-stream";
  const INCOGNITO_PREFIX = "/api/chat/end-incognito-session/";
  const STOP_PREFIX = "/api/chat/stop-chat-session/";
  const allowedRecordKeys = new Set([
    "version",
    "token",
    "sessionId",
    "startedAt",
    "multiModel",
    "hiddenAt",
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
  let statusRequest = null;
  let storageUsable = true;
  let reloadingToken = null;
  let resumeOwnerToken = null;

  function now() {
    return Date.now();
  }

  function isUuid(value) {
    return typeof value === "string" && UUID_RE.test(value);
  }

  function validTimestamp(value, minimum) {
    return Number.isFinite(value) && value >= minimum && value - now() <= 60000;
  }

  function validRecord(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    if (Object.keys(value).some((key) => !allowedRecordKeys.has(key))) return false;
    if (value.version !== SCHEMA_VERSION) return false;
    if (typeof value.token !== "string" || value.token.length < 16 || value.token.length > 128) return false;
    if (!isUuid(value.sessionId)) return false;
    if (!Number.isFinite(value.startedAt) || value.startedAt <= 0 || now() - value.startedAt > RECORD_TTL_MS || value.startedAt - now() > 60000) return false;
    if (typeof value.multiModel !== "boolean") return false;
    if (value.hiddenAt !== null && !validTimestamp(value.hiddenAt, value.startedAt)) return false;
    if (value.lastRecoveryAt !== null && !validTimestamp(value.lastRecoveryAt, value.startedAt)) return false;
    if (![null, "single", "multi"].includes(value.pollPhase)) return false;
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

  function cancelRecoveryWork(token) {
    if (recoveryTimer !== null) clearTimeout(recoveryTimer);
    recoveryTimer = null;
    if (pollTimer !== null) clearTimeout(pollTimer);
    pollTimer = null;
    if (statusRequest && (!token || statusRequest.token === token)) {
      statusRequest.controller.abort();
      statusRequest = null;
    }
  }

  function clearMatching(token) {
    // Once this document has committed to a companion-owned reload, browser
    // teardown events from the outgoing page no longer describe user intent
    // or a fresh transport failure. Preserve the marker for the new document
    // regardless of whether abort, stream completion, visibilitychange, or
    // pagehide happens first.
    if (reloadingToken === token) return false;
    const record = loadRecord();
    if (!record || record.token !== token) return false;
    removeStored();
    hideRecoveryNotice();
    cancelRecoveryWork(token);
    if (resumeOwnerToken === token) resumeOwnerToken = null;
    return true;
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

  function resumeSessionId(url) {
    if (!url || url.origin !== location.origin || url.hash) return null;
    if (!url.pathname.startsWith(RESUME_PREFIX) || !url.pathname.endsWith(RESUME_SUFFIX)) return null;
    const sessionId = url.pathname.slice(RESUME_PREFIX.length, -RESUME_SUFFIX.length);
    const keys = Array.from(url.searchParams.keys());
    if (!isUuid(sessionId) || keys.length !== 1 || keys[0] !== "cursor") return null;
    return /^\d+$/.test(url.searchParams.get("cursor") || "") ? sessionId : null;
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

  function showRecoveryNotice() {
    if (!document || document.getElementById("private-onyx-recovery-notice")) return;
    const notice = document.createElement("div");
    notice.id = "private-onyx-recovery-notice";
    notice.setAttribute("role", "status");
    notice.setAttribute("aria-live", "polite");
    notice.textContent = "Reconnecting to this response…";
    notice.style.cssText = "position:fixed;right:1rem;bottom:1rem;z-index:2147483647;max-width:24rem;padding:.75rem 1rem;border-radius:.5rem;background:#202124;color:#fff;font:14px/1.4 system-ui,sans-serif;box-shadow:0 2px 12px #0006";
    (document.body || document.documentElement).appendChild(notice);
  }

  function hideRecoveryNotice() {
    const notice = document && document.getElementById("private-onyx-recovery-notice");
    if (notice) notice.remove();
  }

  function currentChatId() {
    try {
      return new URL(location.href).searchParams.get("chatId");
    } catch (_) {
      return null;
    }
  }

  function recoveryEligible(record) {
    return document.visibilityState === "visible" &&
      navigator.onLine !== false &&
      currentChatId() === record.sessionId;
  }

  function markForRecovery(token) {
    if (reloadingToken === token) return;
    const record = loadRecord();
    if (!record || record.token !== token) return;
    record.hiddenAt = now();
    record.pollAttempt = 0;
    if (!saveRecord(record)) {
      showManualNotice();
      return;
    }
    scheduleRecovery();
  }

  function finishResumeCleanly(token) {
    if (reloadingToken === token) return;
    if (resumeOwnerToken === token) resumeOwnerToken = null;
    const record = loadRecord();
    if (!record || record.token !== token) return;
    record.pollAttempt = 0;
    if (!saveRecord(record)) {
      showManualNotice();
      return;
    }
    // The resume endpoint can end cleanly on a replay gap as well as actual
    // completion. Keep the marker until the authoritative status route says
    // the recorded run is no longer active.
    scheduleStatus(record, true, "resume-eof");
  }

  function canWrapStreamResponse(response) {
    return Boolean(response.body) && typeof TransformStream === "function" && typeof response.body.pipeTo === "function";
  }

  function wrapStreamResponse(response, token, streamKind) {
    if (!canWrapStreamResponse(response)) {
      showManualNotice();
      return response;
    }
    let failureObserved = false;
    const transparent = new TransformStream({
      transform(chunk, controller) {
        controller.enqueue(chunk);
      },
      flush() {
        if (streamKind === "resume") finishResumeCleanly(token);
        else clearMatching(token);
      },
    });
    response.body.pipeTo(transparent.writable).catch(() => {
      if (failureObserved) return;
      failureObserved = true;
      if (streamKind === "resume" && resumeOwnerToken === token) {
        resumeOwnerToken = null;
      }
      markForRecovery(token);
    });
    return new Response(transparent.readable, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  }

  function instrumentSend(init, receiver, args) {
    let payload;
    try {
      if (!init || typeof init.body !== "string") return originalFetch.apply(receiver, args);
      payload = JSON.parse(init.body);
    } catch (_) {
      return originalFetch.apply(receiver, args);
    }
    const sessionId = payload && payload.chat_session_id;
    if (!isUuid(sessionId)) return originalFetch.apply(receiver, args);

    let token;
    try {
      token = requestToken();
    } catch (_) {
      showManualNotice();
      return originalFetch.apply(receiver, args);
    }
    const record = {
      version: SCHEMA_VERSION,
      token,
      sessionId,
      startedAt: now(),
      multiModel: Array.isArray(payload.llm_overrides) && payload.llm_overrides.length >= 2,
      hiddenAt: document.visibilityState === "hidden" ? now() : null,
      lastRecoveryAt: null,
      pollPhase: null,
      pollAttempt: 0,
    };
    cancelRecoveryWork();
    reloadingToken = null;
    resumeOwnerToken = null;
    if (!saveRecord(record)) showManualNotice();

    const signal = init.signal;
    const abortedBeforeSend = Boolean(signal && signal.aborted);
    if (abortedBeforeSend) clearMatching(token);

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
      return wrapStreamResponse(response, token, "send");
    }, (error) => {
      if (abortedBeforeSend) clearMatching(token);
      else markForRecovery(token);
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

    const terminalSessionId = method === "POST" && matchingSameOrigin(url)
      ? exactSessionId(url.pathname, INCOGNITO_PREFIX) ||
        exactSessionId(url.pathname, STOP_PREFIX)
      : null;
    if (terminalSessionId) {
      let result;
      try {
        result = originalFetch.apply(this, args);
      } catch (error) {
        const record = loadRecord();
        if (record && record.sessionId === terminalSessionId) clearMatching(record.token);
        throw error;
      }
      const record = loadRecord();
      if (record && record.sessionId === terminalSessionId) clearMatching(record.token);
      return result;
    }

    if (method === "POST" && matchingSameOrigin(url) && url.pathname === SEND_PATH) {
      return instrumentSend(init, this, args);
    }

    const resumedSession = method === "GET" ? resumeSessionId(url) : null;
    if (resumedSession) {
      const result = originalFetch.apply(this, args);
      const record = loadRecord();
      if (!record || record.sessionId !== resumedSession) return result;
      return Promise.resolve(result).then((response) => {
        if (!response.ok) return response;
        const current = loadRecord();
        if (!current || current.token !== record.token) return response;
        if (canWrapStreamResponse(response)) {
          resumeOwnerToken = record.token;
          cancelRecoveryWork(record.token);
        }
        return wrapStreamResponse(response, record.token, "resume");
      }, (error) => {
        const current = loadRecord();
        if (current && current.token === record.token) markForRecovery(record.token);
        throw error;
      });
    }

    return originalFetch.apply(this, args);
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
          if (record && record.sessionId === sessionId) clearMatching(record.token);
        }
        throw error;
      }
      if (sessionId) {
        const record = loadRecord();
        if (record && record.sessionId === sessionId) clearMatching(record.token);
      }
      return result;
    };
  }

  function scheduleStatus(record, immediate, purpose) {
    if (pollTimer !== null || statusRequest !== null) return;
    if (!recoveryEligible(record)) return;
    const current = loadRecord();
    if (!current || current.token !== record.token) return;
    const index = Math.min(current.pollAttempt, POLL_DELAYS_MS.length - 1);
    const delay = immediate ? 0 : POLL_DELAYS_MS[index];
    if (!immediate) current.pollAttempt = Math.min(current.pollAttempt + 1, POLL_DELAYS_MS.length);
    if (!saveRecord(current)) {
      showManualNotice();
      return;
    }
    pollTimer = setTimeout(() => {
      pollTimer = null;
      checkStatus(current.token, purpose);
    }, delay);
  }

  async function checkStatus(token, purpose) {
    if (statusRequest !== null) return;
    const record = loadRecord();
    if (!record || record.token !== token || !recoveryEligible(record)) return;
    const controller = new AbortController();
    const owner = { token, controller };
    statusRequest = owner;
    let outcome = "retry";
    let payload = null;
    try {
      const response = await originalFetch.call(window, SESSION_PREFIX + record.sessionId, {
        method: "GET",
        signal: controller.signal,
      });
      if (response.status === 404) {
        outcome = "missing";
      } else if (response.ok) {
        payload = await response.json();
        outcome = payload && typeof payload.incognito === "boolean" &&
          typeof payload.pending_reservation === "boolean" &&
          Object.hasOwn(payload, "current_run")
          ? "status"
          : "retry";
      }
    } catch (_) {
      if (controller.signal.aborted) outcome = "aborted";
    } finally {
      if (statusRequest === owner) statusRequest = null;
    }

    const current = loadRecord();
    if (!current || current.token !== token) return;
    if (outcome === "aborted") return;
    // The selected chat, visibility, or connectivity can change while the
    // status body is in flight. Never let that stale result mutate recovery
    // state or reload a page that is no longer eligible for this token.
    if (!recoveryEligible(current)) return;
    if (outcome === "missing" || (outcome === "status" && payload.incognito)) {
      clearMatching(token);
      return;
    }
    if (outcome !== "status") {
      scheduleStatus(current, false, purpose);
      return;
    }

    // The assistant reservation is committed before the processing fence is
    // published. During that pre-stream window, current_run=null is not
    // completion: accepting it would strand the stock error placeholder.
    if (
      payload.current_run == null &&
      payload.pending_reservation &&
      now() - current.startedAt < PENDING_RESERVATION_GRACE_MS
    ) {
      showRecoveryNotice();
      scheduleStatus(current, false, purpose);
      return;
    }
    hideRecoveryNotice();

    if (purpose === "recover") {
      current.hiddenAt = null;
      current.lastRecoveryAt = now();
      current.pollAttempt = 0;
      if (payload.current_run == null) {
        removeStored();
      } else {
        current.pollPhase = current.multiModel ? "multi" : "single";
        if (!saveRecord(current)) {
          showManualNotice();
          return;
        }
      }
      reloadForRecovery(token);
      return;
    }

    if (purpose === "resume-eof") {
      if (payload.current_run == null) {
        clearMatching(token);
        return;
      }
      current.hiddenAt = now();
      current.pollAttempt = 0;
      if (!saveRecord(current)) {
        showManualNotice();
        return;
      }
      scheduleRecovery();
      return;
    }

    if (purpose === "single-settle") {
      if (payload.current_run == null) {
        if (resumeOwnerToken === token) return;
        clearMatching(token);
        reloadForRecovery(token);
        return;
      }
      if (resumeOwnerToken !== token) {
        scheduleStatus(current, false, purpose);
      }
      return;
    }
    if (payload.current_run == null) {
      clearMatching(token);
      reloadForRecovery(token);
      return;
    }
    scheduleStatus(current, false, purpose);
  }

  function recover() {
    recoveryTimer = null;
    const record = loadRecord();
    if (!record || !recoveryEligible(record)) return;
    if (record.pollPhase === "multi" && record.hiddenAt === null) {
      scheduleStatus(record, true, "settle");
      return;
    }
    if (record.pollPhase === "single" && record.hiddenAt === null) {
      scheduleStatus(record, true, "single-settle");
      return;
    }
    if (record.pollPhase === "multi") {
      record.hiddenAt = null;
      record.pollAttempt = 0;
      if (saveRecord(record)) scheduleStatus(record, true, "settle");
      else showManualNotice();
      return;
    }
    if (record.hiddenAt === null) return;
    const elapsed = record.lastRecoveryAt === null ? Infinity : now() - record.lastRecoveryAt;
    if (elapsed < MIN_RECOVERY_INTERVAL_MS) {
      recoveryTimer = setTimeout(recover, MIN_RECOVERY_INTERVAL_MS - elapsed);
      return;
    }
    record.pollAttempt = 0;
    if (!saveRecord(record)) {
      showManualNotice();
      return;
    }
    scheduleStatus(record, true, "recover");
  }

  function scheduleRecovery() {
    const record = loadRecord();
    if (!record || record.token === reloadingToken) return;
    if (recoveryTimer === null) recoveryTimer = setTimeout(recover, 0);
  }

  function reloadForRecovery(token) {
    reloadingToken = token;
    cancelRecoveryWork(token);
    try {
      location.reload();
    } catch (_) {
      reloadingToken = null;
      showManualNotice();
    }
  }

  function scheduleRouteRecovery() {
    const record = loadRecord();
    if (record && currentChatId() === record.sessionId) scheduleRecovery();
    else hideRecoveryNotice();
  }

  function wrapHistoryMethod(name) {
    if (!window.history || typeof window.history[name] !== "function") return;
    const original = window.history[name];
    window.history[name] = function () {
      const result = original.apply(this, arguments);
      scheduleRouteRecovery();
      return result;
    };
  }

  wrapHistoryMethod("pushState");
  wrapHistoryMethod("replaceState");
  window.addEventListener("popstate", scheduleRouteRecovery);

  document.addEventListener("visibilitychange", () => {
    const record = loadRecord();
    if (!record) return;
    if (document.visibilityState === "hidden") {
      hideRecoveryNotice();
      if (record.token !== reloadingToken) {
        record.hiddenAt = now();
        record.pollAttempt = 0;
        saveRecord(record);
      }
      cancelRecoveryWork(record.token);
    } else {
      scheduleRecovery();
    }
  });
  window.addEventListener("pagehide", () => {
    const record = loadRecord();
    if (record && record.token !== reloadingToken) {
      record.hiddenAt = now();
      record.pollAttempt = 0;
      saveRecord(record);
    }
    if (record) cancelRecoveryWork(record.token);
  });
  window.addEventListener("pageshow", (event) => {
    // A pageshow in this same JavaScript realm means a requested navigation
    // did not replace the document (for example, it was cancelled or restored
    // from the back-forward cache). It is safe to resume the persisted phase.
    reloadingToken = null;
    const record = loadRecord();
    if (record && (event.persisted || record.hiddenAt !== null || record.pollPhase !== null)) scheduleRecovery();
  });
  window.addEventListener("online", () => {
    const record = loadRecord();
    if (!record || record.token === reloadingToken) return;
    // A new send has no persisted recovery phase, so restored connectivity is
    // itself enough to reconcile an ambiguously accepted request. Persisted
    // phases already describe their pending work. In particular, do not turn
    // an online event into another suspension while the stock resume body is
    // the active single-model completion owner.
    if (record.pollPhase === null) record.hiddenAt = now();
    record.pollAttempt = 0;
    if (!saveRecord(record)) {
      showManualNotice();
      return;
    }
    if (
      record.pollPhase === "single" &&
      record.hiddenAt === null &&
      resumeOwnerToken === record.token
    ) return;
    scheduleRecovery();
  });

  const initialRecord = loadRecord();
  if (initialRecord && (initialRecord.hiddenAt !== null || initialRecord.pollPhase !== null)) {
    scheduleRecovery();
  }
  if (!storageUsable) showManualNotice();
  if (window.__PRIVATE_ONYX_RECONNECT_TEST__ === true) {
    window.__privateOnyxReconnectTest = {
      STORAGE_KEY,
      loadRecord,
      saveRecord,
      recover,
      checkStatus,
      constants: { RECORD_TTL_MS, PENDING_RESERVATION_GRACE_MS, MIN_RECOVERY_INTERVAL_MS, POLL_DELAYS_MS },
    };
  }
})();
