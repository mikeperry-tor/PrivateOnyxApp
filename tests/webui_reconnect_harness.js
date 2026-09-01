"use strict";

const fs = require("fs");
const vm = require("vm");

const scriptPath = process.argv[2];
if (!scriptPath) throw new Error("usage: node webui_reconnect_harness.js SCRIPT");
const script = fs.readFileSync(scriptPath, "utf8");
const SESSION = "11111111-1111-4111-8111-111111111111";
const OTHER = "22222222-2222-4222-8222-222222222222";

if (script.includes("private-onyx:webui-reconnect:v1")) {
  throw new Error("unreleased v1 storage compatibility must not be retained");
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function settle() {
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
}

function createBrowser(fetchImpl, options = {}) {
  const events = { window: new Map(), document: new Map() };
  const storage = new Map();
  if (options.initialRecord) {
    storage.set(
      "private-onyx:webui-reconnect:v2",
      JSON.stringify(options.initialRecord)
    );
  }
  const timers = new Map();
  const notices = [];
  let nextTimer = 1;
  let clock = 1700000000000;
  let reloads = 0;
  let beaconCalls = 0;
  const location = {
    href: options.initialHref || `https://onyx.example/chat?chatId=${SESSION}`,
    origin: "https://onyx.example",
    reload() { reloads += 1; },
  };
  const historyCalls = [];
  const history = {
    pushState(state, unused, url) {
      historyCalls.push(["pushState", state, unused, url]);
      if (options.historyError) throw options.historyError;
      if (url !== undefined && url !== null) location.href = new URL(url, location.href).href;
      return options.historyResult;
    },
    replaceState(state, unused, url) {
      historyCalls.push(["replaceState", state, unused, url]);
      if (options.historyError) throw options.historyError;
      if (url !== undefined && url !== null) location.href = new URL(url, location.href).href;
      return options.historyResult;
    },
  };
  function add(owner, type, callback) {
    const callbacks = events[owner].get(type) || [];
    callbacks.push(callback);
    events[owner].set(type, callbacks);
  }
  const document = {
    visibilityState: "visible",
    addEventListener(type, callback) { add("document", type, callback); },
    getElementById(id) { return notices.find((item) => item.id === id) || null; },
    createElement() {
      return {
        id: "",
        style: {},
        setAttribute() {},
        textContent: "",
      };
    },
    body: { appendChild(node) { notices.push(node); } },
    documentElement: { appendChild(node) { notices.push(node); } },
  };
  const navigator = {
    onLine: true,
    sendBeacon(url, data) {
      beaconCalls += 1;
      if (options.beaconError) throw options.beaconError;
      return options.beaconResult === undefined ? true : options.beaconResult;
    },
  };
  const sessionStorage = options.storageFailure ? {
    getItem() { throw new Error("storage disabled"); },
    setItem() { throw new Error("storage disabled"); },
    removeItem() { throw new Error("storage disabled"); },
  } : {
    getItem(key) { return storage.has(key) ? storage.get(key) : null; },
    setItem(key, value) { storage.set(key, String(value)); },
    removeItem(key) { storage.delete(key); },
  };
  const window = {
    fetch: fetchImpl,
    history,
    crypto: {
      randomUUID: (() => {
        let counter = 0;
        return () => {
          if (options.cryptoFailure) throw new Error("crypto unavailable");
          return `aaaaaaaa-aaaa-4aaa-8aaa-${String(++counter).padStart(12, "0")}`;
        };
      })(),
      getRandomValues(array) { array.fill(7); return array; },
    },
    addEventListener(type, callback) { add("window", type, callback); },
    __PRIVATE_ONYX_RECONNECT_TEST__: true,
  };
  const context = {
    window,
    document,
    navigator,
    sessionStorage,
    location,
    URL,
    Uint8Array,
    Response,
    Headers,
    ReadableStream,
    TransformStream: options.noTransform ? undefined : TransformStream,
    AbortController,
    Promise,
    Date: { now: () => clock },
    setTimeout(callback, delay) {
      const id = nextTimer++;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
  };
  vm.runInNewContext(script, context, { filename: scriptPath });
  async function dispatch(owner, type, event = {}) {
    for (const callback of events[owner].get(type) || []) callback(event);
    await Promise.resolve();
    await Promise.resolve();
  }
  async function runTimers(limit = Infinity) {
    let count = 0;
    while (timers.size && count < limit) {
      const [id, timer] = timers.entries().next().value;
      timers.delete(id);
      timer.callback();
      count += 1;
      await Promise.resolve();
      await Promise.resolve();
    }
  }
  return {
    window,
    document,
    navigator,
    location,
    storage,
    notices,
    dispatch,
    runTimers,
    advance(ms) { clock += ms; },
    reloads: () => reloads,
    beaconCalls: () => beaconCalls,
    historyCalls,
    pendingTimers: () => [...timers.values()].map((timer) => timer.delay),
    key: window.__privateOnyxReconnectTest.STORAGE_KEY,
  };
}

function recoveryRecord(overrides = {}) {
  return {
    version: 2,
    token: "aaaaaaaa-aaaa-4aaa-8aaa-000000000001",
    sessionId: SESSION,
    startedAt: 1700000000000,
    multiModel: false,
    hiddenAt: null,
    lastRecoveryAt: null,
    pollPhase: null,
    pollAttempt: 0,
    ...overrides,
  };
}

function sessionResponse(currentRun = true, incognito = false) {
  return Response.json({
    current_run: currentRun ? { run_id: 9 } : null,
    incognito,
  });
}

function sendInit(overrides = {}) {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "secret" },
    body: JSON.stringify({
      chat_session_id: SESSION,
      message: "private prompt",
      llm_overrides: null,
      ...overrides,
    }),
  };
}

function streamResponse(chunks, failure = null) {
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
      if (failure) controller.error(failure);
      else controller.close();
    },
  }), { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

async function main() {
  {
    const sentinel = Promise.resolve(new Response("unrelated"));
    let calls = 0;
    const browser = createBrowser(() => { calls += 1; return sentinel; }, { historyResult: "history-result" });
    assert(browser.window.fetch("https://other.example/api", { method: "POST" }) === sentinel, "cross-origin fetch identity changed");
    assert(browser.window.fetch("/api/other") === sentinel, "unrelated fetch identity changed");
    assert(browser.window.history.pushState({ safe: true }, "", "/chat?chatId=" + OTHER) === "history-result", "history return value changed");
    assert(browser.window.history.replaceState({ safe: true }, "", "/chat?chatId=" + SESSION) === "history-result", "replaceState return value changed");
    assert(browser.historyCalls.length === 2 && browser.pendingTimers().length === 0, "marker-free history navigation scheduled recovery");
    assert(calls === 2, "unrelated fetch was duplicated");
    assert(browser.storage.size === 0, "unrelated fetch created state");
  }

  {
    const failure = new Error("native history failure");
    const browser = createBrowser(() => { throw new Error("unexpected fetch"); }, { historyError: failure });
    let observed = null;
    try { browser.window.history.pushState({}, "", "/chat"); } catch (error) { observed = error; }
    assert(observed === failure && browser.pendingTimers().length === 0, "history exception identity or scheduling changed");
  }

  {
    const browser = createBrowser(() => Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 })));
    await browser.window.fetch("/api/chat/send-chat-message", sendInit({ llm_overrides: [{ model: "one" }] }));
    assert(JSON.parse(browser.storage.get(browser.key)).multiModel === false, "one LLM override was misclassified as multi-model");
    await browser.window.fetch("/api/chat/send-chat-message", sendInit({ llm_overrides: [{ model: "one" }, { model: "two" }] }));
    assert(JSON.parse(browser.storage.get(browser.key)).multiModel === true, "two LLM overrides were not classified as multi-model");
  }

  {
    let calls = 0;
    const browser = createBrowser(() => {
      calls += 1;
      return Promise.resolve(streamResponse(["one", "two"]));
    });
    const response = await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    const stored = browser.storage.get(browser.key);
    assert(calls === 1 && stored, "exact send did not create one marker");
    for (const forbidden of ["private prompt", "Authorization", "secret", "one", "two"]) {
      assert(!stored.includes(forbidden), `sensitive value entered storage: ${forbidden}`);
    }
    assert(response.status === 200 && response.headers.get("Content-Type").includes("text/event-stream"), "response metadata changed");
    assert(await response.text() === "onetwo", "stream chunks changed");
    assert(!browser.storage.has(browser.key), "clean EOF did not clear marker");
  }

  {
    const failure = new Error("private stream failure");
    const browser = createBrowser(() => Promise.resolve(streamResponse(["first"], failure)));
    const response = await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    let observed = null;
    try { await response.text(); } catch (error) { observed = error; }
    assert(observed === failure, "stream failure identity changed");
    const stored = browser.storage.get(browser.key);
    assert(stored && !stored.includes(failure.message), "stream failure did not retain sanitized marker");
  }

  {
    const browser = createBrowser(() => Promise.resolve(new Response("no", { status: 503 })));
    const response = await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    assert(response.status === 503 && !browser.storage.has(browser.key), "HTTP failure retained marker");
  }

  {
    const rejection = new Error("network disconnected after acceptance");
    const browser = createBrowser(() => Promise.reject(rejection));
    let observed = null;
    try { await browser.window.fetch("/api/chat/send-chat-message", sendInit()); } catch (error) { observed = error; }
    assert(observed === rejection && browser.storage.has(browser.key), "async network rejection was not retained exactly");
    assert(browser.pendingTimers().length === 1, "visible network rejection did not schedule recovery");
  }

  {
    const controller = new AbortController();
    const browser = createBrowser(() => new Promise(() => {}));
    browser.window.fetch("/api/chat/send-chat-message", { ...sendInit(), signal: controller.signal });
    controller.abort();
    await Promise.resolve();
    assert(browser.storage.has(browser.key), "ambiguous in-flight abort discarded an accepted send marker");
  }

  {
    const responses = [];
    const browser = createBrowser(() => new Promise((resolve) => responses.push(resolve)));
    const first = browser.window.fetch("/api/chat/send-chat-message", sendInit());
    const firstToken = JSON.parse(browser.storage.get(browser.key)).token;
    const second = browser.window.fetch("/api/chat/send-chat-message", sendInit());
    const secondToken = JSON.parse(browser.storage.get(browser.key)).token;
    assert(firstToken !== secondToken, "later send reused token");
    responses[0](streamResponse(["old"]));
    await (await first).text();
    assert(JSON.parse(browser.storage.get(browser.key)).token === secondToken, "old completion cleared later send");
    responses[1](streamResponse(["new"]));
    await (await second).text();
    assert(!browser.storage.has(browser.key), "later completion did not clear itself");
  }

  {
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) {
        return Promise.resolve(sessionResponse(true, false));
      }
      return Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 }));
    });
    await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    browser.document.visibilityState = "hidden";
    await browser.dispatch("document", "visibilitychange");
    browser.document.visibilityState = "visible";
    await browser.dispatch("document", "visibilitychange");
    await browser.dispatch("window", "pageshow", { persisted: true });
    await browser.dispatch("window", "online");
    await browser.runTimers(2);
    await settle();
    assert(browser.reloads() === 1, "wake signals did not coalesce to one reload");
    await browser.dispatch("window", "pagehide");
    await browser.dispatch("window", "pageshow", { persisted: false });
    await browser.runTimers(2);
    await settle();
    assert(browser.reloads() === 1, "initial pageshow caused reload loop");
    browser.advance(2000);
    browser.document.visibilityState = "hidden";
    await browser.dispatch("document", "visibilitychange");
    browser.document.visibilityState = "visible";
    await browser.dispatch("document", "visibilitychange");
    await browser.runTimers(2);
    await settle();
    assert(browser.reloads() === 2, "second suspension did not permit recovery");
    browser.location.href = `https://onyx.example/chat?chatId=${OTHER}`;
    browser.advance(2000);
    browser.document.visibilityState = "hidden";
    await browser.dispatch("document", "visibilitychange");
    browser.document.visibilityState = "visible";
    await browser.dispatch("document", "visibilitychange");
    await browser.runTimers(2);
    assert(browser.reloads() === 2 && browser.storage.has(browser.key), "other chat was reloaded or marker discarded");
  }

  for (const [name, order] of [
    ["visibility then pagehide", ["visibilitychange", "pagehide"]],
    ["pagehide then visibility", ["pagehide", "visibilitychange"]],
    ["visibility only", ["visibilitychange"]],
    ["pagehide only", ["pagehide"]],
  ]) {
    let statusCalls = 0;
    const browser = createBrowser((url) => {
      if (!String(url).startsWith("/api/chat/reconnect-status/")) throw new Error("unexpected fetch");
      statusCalls += 1;
      return Promise.resolve(sessionResponse(true, false));
    }, { initialRecord: recoveryRecord({ hiddenAt: 1700000000000 }) });
    await browser.runTimers(2);
    await settle();
    assert(browser.reloads() === 1, `${name}: initial recovery reload missing`);
    for (const event of order) {
      if (event === "visibilitychange") {
        browser.document.visibilityState = "hidden";
        await browser.dispatch("document", event);
      } else {
        await browser.dispatch("window", event);
      }
    }
    let stored = JSON.parse(browser.storage.get(browser.key));
    assert(stored.pollPhase === "single" && stored.hiddenAt === null, `${name}: outgoing lifecycle event created a new interruption`);
    browser.document.visibilityState = "visible";
    await browser.dispatch("window", "pageshow", { persisted: true });
    await browser.runTimers(2);
    await settle();
    stored = JSON.parse(browser.storage.get(browser.key));
    assert(browser.reloads() === 1 && stored.hiddenAt === null, `${name}: restored outgoing document entered a reload loop`);
    assert(statusCalls >= 2, `${name}: restored document did not resume settling`);
  }

  {
    let streamController;
    const abortController = new AbortController();
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) {
        return Promise.resolve(sessionResponse(true, false));
      }
      return Promise.resolve(new Response(new ReadableStream({
        start(controller) { streamController = controller; },
      }), { status: 200 }));
    });
    const response = await browser.window.fetch("/api/chat/send-chat-message", {
      ...sendInit(),
      signal: abortController.signal,
    });
    const consumed = response.text().catch(() => null);
    browser.document.visibilityState = "hidden";
    await browser.dispatch("document", "visibilitychange");
    browser.document.visibilityState = "visible";
    await browser.dispatch("document", "visibilitychange");
    await browser.runTimers(2);
    await settle();
    assert(browser.reloads() === 1, "active stream did not enter recovery reload");
    abortController.abort();
    streamController.close();
    await consumed;
    await settle();
    const stored = JSON.parse(browser.storage.get(browser.key));
    assert(stored.pollPhase === "single" && stored.hiddenAt === null, "outgoing stream teardown cleared or re-interrupted the recovery marker");
  }

  {
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) {
        return Promise.resolve(sessionResponse(true, false));
      }
      return Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 }));
    });
    await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    await browser.dispatch("window", "online");
    await browser.runTimers(2);
    await settle();
    assert(browser.reloads() === 1, "online transition did not recover an active visible send");
  }

  {
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) return Promise.resolve(sessionResponse(false, false));
      return Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 }));
    });
    await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    browser.document.visibilityState = "hidden";
    await browser.dispatch("document", "visibilitychange");
    browser.document.visibilityState = "visible";
    await browser.dispatch("document", "visibilitychange");
    await browser.runTimers(2);
    await settle();
    assert(browser.reloads() === 1 && !browser.storage.has(browser.key), "completed-while-hidden single model retained a stale phase");
  }

  {
    const browser = createBrowser(() => Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 })));
    browser.storage.set(browser.key, "not json private prompt");
    assert(browser.window.__privateOnyxReconnectTest.loadRecord() === null, "malformed storage survived");
    assert(!browser.storage.has(browser.key), "malformed storage was not discarded");
    browser.storage.set(browser.key, JSON.stringify(recoveryRecord({
      startedAt: 1700000000000 - (4 * 60 * 60 * 1000) - 1,
    })));
    assert(browser.window.__privateOnyxReconnectTest.loadRecord() === null, "expired storage survived");
  }

  {
    let currentRun = true;
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) {
        return Promise.resolve(sessionResponse(currentRun, false));
      }
      return Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 }));
    });
    await browser.window.fetch("/api/chat/send-chat-message", sendInit({ llm_overrides: [{ model: "one" }, { model: "two" }] }));
    browser.document.visibilityState = "hidden";
    await browser.dispatch("document", "visibilitychange");
    browser.document.visibilityState = "visible";
    await browser.dispatch("document", "visibilitychange");
    await browser.runTimers(2);
    await settle();
    assert(browser.reloads() === 1, "multi-model first reconciliation reload missing");
    assert(JSON.parse(browser.storage.get(browser.key)).pollPhase === "multi", "multi-model phase was not persisted before reload");
    await browser.dispatch("window", "pageshow", { persisted: false });
    await browser.runTimers(2);
    await settle();
    assert(browser.pendingTimers().length === 1 && browser.pendingTimers()[0] === 2000, "persisted multi-model poll did not bootstrap with backoff");
    await browser.runTimers(1);
    await settle();
    assert(browser.pendingTimers().length === 1 && browser.pendingTimers()[0] === 5000, "multi-model poll did not advance backoff");
    browser.document.visibilityState = "hidden";
    await browser.dispatch("document", "visibilitychange");
    assert(browser.pendingTimers().length === 0, "multi-model poll did not pause while hidden");
    currentRun = false;
    browser.document.visibilityState = "visible";
    await browser.dispatch("document", "visibilitychange");
    await browser.runTimers(2);
    await settle();
    assert(browser.reloads() === 2 && !browser.storage.has(browser.key), "multi-model completion did not settle with one final reload");
  }

  {
    let statusCalls = 0;
    const browser = createBrowser((url) => {
      if (!String(url).startsWith("/api/chat/reconnect-status/")) throw new Error("unexpected fetch");
      statusCalls += 1;
      return Promise.resolve(sessionResponse(true, false));
    }, {
      initialHref: `https://onyx.example/chat?chatId=${OTHER}`,
      initialRecord: recoveryRecord({ multiModel: true, pollPhase: "multi" }),
      historyResult: "route-result",
    });
    await browser.runTimers(2);
    assert(statusCalls === 0 && browser.pendingTimers().length === 0, "different-chat startup polled the marked session");
    assert(browser.window.history.pushState({}, "", `/chat?chatId=${SESSION}`) === "route-result", "wrapped pushState changed its result");
    await browser.runTimers(2);
    await settle();
    assert(statusCalls === 1 && browser.pendingTimers()[0] === 2000, "SPA return did not restart multi-model recovery");
    browser.window.history.replaceState({}, "", `/chat?chatId=${OTHER}`);
    await browser.runTimers(2);
    assert(statusCalls === 1, "replaceState to another chat polled the marked session");
    browser.location.href = `https://onyx.example/chat?chatId=${SESSION}`;
    await browser.dispatch("window", "popstate");
    await browser.runTimers(2);
    await settle();
    assert(statusCalls === 2, "popstate return did not restart multi-model recovery");
  }

  {
    let statusCalls = 0;
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) {
        statusCalls += 1;
        return Promise.resolve(sessionResponse(true, false));
      }
      throw new Error("unexpected fetch");
    }, {
      initialRecord: recoveryRecord({ multiModel: true, pollPhase: "multi", lastRecoveryAt: 1700000000000 }),
    });
    await browser.runTimers(2);
    await settle();
    assert(statusCalls === 1, "persisted recovery phase depended on a stock session request");
    assert(browser.pendingTimers().length === 1 && browser.pendingTimers()[0] === 2000, "startup recovery did not schedule its own poll");
  }

  {
    let statusCalls = 0;
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) {
        statusCalls += 1;
        return Promise.resolve(statusCalls === 1
          ? new Response("temporary", { status: 503 })
          : sessionResponse(true, false));
      }
      throw new Error("unexpected fetch");
    }, { initialRecord: recoveryRecord({ pollPhase: "single" }) });
    await browser.runTimers(2);
    await settle();
    assert(browser.storage.has(browser.key) && browser.pendingTimers()[0] === 2000, "transient status failure stopped recovery");
    await browser.runTimers(1);
    await settle();
    assert(statusCalls === 2 && browser.pendingTimers()[0] === 5000 && browser.storage.has(browser.key), "single-model settling stopped without a stock resume owner");
  }

  {
    let resolveStatus;
    let resumeController;
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) {
        return new Promise((resolve) => { resolveStatus = resolve; });
      }
      if (String(url).includes("/resume-stream")) {
        return Promise.resolve(new Response(new ReadableStream({
          start(controller) { resumeController = controller; },
        }), { status: 200 }));
      }
      throw new Error("unexpected fetch");
    }, { initialRecord: recoveryRecord({ pollPhase: "single" }) });
    await browser.runTimers(2);
    const resumed = await browser.window.fetch(`/api/chat/chat-session/${SESSION}/resume-stream?cursor=0`);
    const consumed = resumed.text().catch(() => null);
    resolveStatus(sessionResponse(false, false));
    await settle();
    assert(browser.reloads() === 0 && browser.storage.has(browser.key), "completion status displaced the stock resume owner");
    resumeController.close();
    await consumed;
    await settle();
    assert(!browser.storage.has(browser.key) && browser.pendingTimers().length === 0, "clean resume EOF did not own single-model completion");
  }

  {
    let resumeController;
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) {
        return Promise.resolve(sessionResponse(true, false));
      }
      if (String(url).includes("/resume-stream")) {
        return Promise.resolve(new Response(new ReadableStream({
          start(controller) { resumeController = controller; },
        }), { status: 200 }));
      }
      throw new Error("unexpected fetch");
    }, { initialRecord: recoveryRecord({ pollPhase: "single" }) });
    const resumed = await browser.window.fetch(`/api/chat/chat-session/${SESSION}/resume-stream?cursor=0`);
    const consumed = resumed.text();
    await browser.runTimers(2);
    await settle();
    assert(browser.storage.has(browser.key) && browser.pendingTimers().length === 0, "single-model polling continued after stock resume ownership");
    resumeController.close();
    await consumed;
    await settle();
    assert(!browser.storage.has(browser.key), "owned active resume EOF retained its marker");
  }

  {
    let resumeController;
    let statusCalls = 0;
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) {
        statusCalls += 1;
        return Promise.resolve(sessionResponse(true, false));
      }
      if (String(url).includes("/resume-stream")) {
        return Promise.resolve(new Response(new ReadableStream({
          start(controller) { resumeController = controller; },
        }), { status: 200 }));
      }
      throw new Error("unexpected fetch");
    }, { initialRecord: recoveryRecord({ pollPhase: "single" }) });
    await browser.runTimers(2);
    await settle();
    const resumed = await browser.window.fetch(`/api/chat/chat-session/${SESSION}/resume-stream?cursor=0`);
    const consumed = resumed.text();
    await browser.runTimers(1);
    await settle();
    const settledStatusCalls = statusCalls;
    assert(browser.pendingTimers().length === 0, "single-model owner left a settling poll active");
    await browser.dispatch("window", "online");
    await browser.runTimers(2);
    await settle();
    assert(statusCalls === settledStatusCalls && browser.reloads() === 0 && browser.pendingTimers().length === 0, "online event displaced an active stock resume owner");
    resumeController.close();
    await consumed;
    await settle();
    assert(!browser.storage.has(browser.key), "online-preserved resume owner did not clear on EOF");
  }

  {
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) {
        return Promise.resolve(sessionResponse(true, false));
      }
      if (String(url).includes("/resume-stream")) {
        return Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 }));
      }
      throw new Error("unexpected fetch");
    }, {
      initialRecord: recoveryRecord({ pollPhase: "single" }),
      noTransform: true,
    });
    await browser.window.fetch(`/api/chat/chat-session/${SESSION}/resume-stream?cursor=0`);
    await browser.runTimers(2);
    await settle();
    assert(browser.pendingTimers()[0] === 2000 && browser.notices.length === 1, "unwrappable resume incorrectly claimed completion ownership");
  }

  {
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) {
        return Promise.resolve(sessionResponse(false, false));
      }
      throw new Error("unexpected fetch");
    }, { initialRecord: recoveryRecord({ pollPhase: "single" }) });
    await browser.runTimers(2);
    await settle();
    assert(browser.reloads() === 1 && !browser.storage.has(browser.key), "single-model completion without a resume owner skipped final hydration");
  }

  {
    let resolveOldStatus;
    let oldStatusSignal;
    const browser = createBrowser((url, init) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) {
        oldStatusSignal = init.signal;
        return new Promise((resolve) => { resolveOldStatus = resolve; });
      }
      return Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 }));
    }, { initialRecord: recoveryRecord({ token: "bbbbbbbb-bbbb-4bbb-8bbb-000000000001", pollPhase: "single" }) });
    await browser.runTimers(2);
    const send = browser.window.fetch("/api/chat/send-chat-message", sendInit());
    const newToken = JSON.parse(browser.storage.get(browser.key)).token;
    assert(oldStatusSignal.aborted, "new send did not cancel the older token's status request");
    resolveOldStatus(sessionResponse(false, false));
    await settle();
    assert(JSON.parse(browser.storage.get(browser.key)).token === newToken, "stale status response cleared a newer send");
    void send;
  }

  {
    let resolveFirstStatus;
    let statusCalls = 0;
    const browser = createBrowser((url) => {
      if (!String(url).startsWith("/api/chat/reconnect-status/")) throw new Error("unexpected fetch");
      statusCalls += 1;
      if (statusCalls === 1) {
        return new Promise((resolve) => { resolveFirstStatus = resolve; });
      }
      return Promise.resolve(sessionResponse(true, false));
    }, {
      initialRecord: recoveryRecord({ hiddenAt: 1700000000000 }),
      historyResult: "route-result",
    });
    await browser.runTimers(2);
    assert(statusCalls === 1, "initial recovery status request did not start");
    assert(browser.window.history.pushState({}, "", `/chat?chatId=${OTHER}`) === "route-result", "route change result changed during status request");
    resolveFirstStatus(sessionResponse(true, false));
    await settle();
    assert(browser.reloads() === 0 && browser.storage.has(browser.key), "in-flight status response reloaded a newly selected chat");
    browser.window.history.pushState({}, "", `/chat?chatId=${SESSION}`);
    await browser.runTimers(2);
    await settle();
    assert(statusCalls === 2 && browser.reloads() === 1, "returning to the marked chat did not resume retained recovery");
  }

  {
    let active = 0;
    let maximumActive = 0;
    let statusCalls = 0;
    const browser = createBrowser((url, init) => {
      if (!String(url).startsWith("/api/chat/reconnect-status/")) throw new Error("unexpected fetch");
      statusCalls += 1;
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      if (statusCalls === 1) {
        return new Promise((resolve, reject) => {
          init.signal.addEventListener("abort", () => {
            active -= 1;
            reject(new DOMException("aborted", "AbortError"));
          }, { once: true });
        });
      }
      active -= 1;
      return Promise.resolve(sessionResponse(true, false));
    }, { initialRecord: recoveryRecord({ pollPhase: "multi" }) });
    await browser.runTimers(2);
    browser.document.visibilityState = "hidden";
    await browser.dispatch("document", "visibilitychange");
    browser.document.visibilityState = "visible";
    await browser.dispatch("document", "visibilitychange");
    await browser.runTimers(2);
    await settle();
    assert(statusCalls === 2 && maximumActive === 1, "hidden/visible transition overlapped status probes");
  }

  {
    const failure = new Error("visible stream disconnected");
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) return Promise.resolve(sessionResponse(true, false));
      return Promise.resolve(streamResponse(["partial"], failure));
    });
    const response = await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    try { await response.text(); } catch (_) {}
    await settle();
    await browser.runTimers(2);
    await settle();
    assert(browser.reloads() === 1, "visible stream failure did not initiate recovery");
  }

  {
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/reconnect-status/")) return Promise.resolve(sessionResponse(true, true));
      return Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 }));
    });
    await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    browser.document.visibilityState = "hidden";
    await browser.dispatch("document", "visibilitychange");
    browser.document.visibilityState = "visible";
    await browser.dispatch("document", "visibilitychange");
    await browser.runTimers(2);
    await settle();
    assert(browser.reloads() === 0, "incognito session was reloaded before classification");
    assert(!browser.storage.has(browser.key), "incognito classification retained reconnect state");
    assert(browser.beaconCalls() === 0, "recovery synthesized an incognito teardown beacon");
  }

  {
    const browser = createBrowser(() => Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 })), { beaconResult: false });
    await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    const result = browser.window.navigator ? null : browser.navigator.sendBeacon(`/api/chat/end-incognito-session/${SESSION}`);
    assert(result === false && browser.beaconCalls() === 1, "beacon result/call count changed");
    assert(!browser.storage.has(browser.key), "incognito beacon retained marker");
  }

  {
    let calls = 0;
    const sentinel = Promise.resolve(new Response(null, { status: 204 }));
    const browser = createBrowser(() => { calls += 1; return sentinel; });
    browser.window.__privateOnyxReconnectTest.saveRecord(recoveryRecord());
    const result = browser.window.fetch(`/api/chat/end-incognito-session/${SESSION}`, { method: "POST" });
    assert(result === sentinel && calls === 1, "incognito fetch identity/call count changed");
    assert(!browser.storage.has(browser.key), "incognito fetch retained marker");
  }

  {
    let calls = 0;
    const sentinel = Promise.resolve(new Response(null, { status: 200 }));
    const browser = createBrowser(() => { calls += 1; return sentinel; });
    browser.window.__privateOnyxReconnectTest.saveRecord(recoveryRecord());
    const result = browser.window.fetch(`/api/chat/stop-chat-session/${SESSION}`, { method: "POST" });
    assert(result === sentinel && calls === 1, "stop fetch identity/call count changed");
    assert(!browser.storage.has(browser.key), "explicit stop retained reconnect state");
  }

  {
    let calls = 0;
    const controller = new AbortController();
    controller.abort();
    const rejection = new Error("already aborted");
    const browser = createBrowser(() => { calls += 1; return Promise.reject(rejection); });
    let observed = null;
    try {
      await browser.window.fetch("/api/chat/send-chat-message", { ...sendInit(), signal: controller.signal });
    } catch (error) {
      observed = error;
    }
    assert(calls === 1 && observed === rejection, "pre-aborted send changed or duplicated the stock request");
    assert(!browser.storage.has(browser.key), "pre-aborted send retained reconnect state");
  }

  {
    let calls = 0;
    const browser = createBrowser(() => {
      calls += 1;
      return Promise.resolve(streamResponse(["stock"]));
    }, { cryptoFailure: true });
    const response = await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    assert(await response.text() === "stock" && calls === 1, "token-generation failure changed or duplicated the stock send");
    assert(browser.notices.length === 1 && !browser.storage.has(browser.key), "token-generation failure did not degrade to manual recovery");
  }

  {
    const browser = createBrowser((url) => {
      if (String(url).includes("/resume-stream")) return Promise.resolve(streamResponse(["resumed"]));
      return Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 }));
    });
    await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    const response = await browser.window.fetch(`/api/chat/chat-session/${SESSION}/resume-stream?cursor=8`);
    assert(await response.text() === "resumed", "exact resume stream changed data");
    assert(!browser.storage.has(browser.key), "clean resume EOF did not clear reconnect state");
  }

  {
    const failure = new Error("resume disconnected");
    const browser = createBrowser((url) => {
      if (String(url).includes("/resume-stream")) return Promise.reject(failure);
      return Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 }));
    });
    await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    let observed = null;
    try {
      await browser.window.fetch(`/api/chat/chat-session/${SESSION}/resume-stream?cursor=8`);
    } catch (error) {
      observed = error;
    }
    assert(observed === failure && browser.pendingTimers().length === 1, "resume rejection did not retain identity and schedule recovery");
  }

  {
    const failure = new Error("resumed body disconnected");
    const controller = new AbortController();
    const browser = createBrowser((url) => {
      if (String(url).includes("/resume-stream")) {
        return Promise.resolve(streamResponse(["partial"], failure));
      }
      return Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 }));
    });
    await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    const response = await browser.window.fetch(
      `/api/chat/chat-session/${SESSION}/resume-stream?cursor=8`,
      { signal: controller.signal }
    );
    let observed = null;
    try { await response.text(); } catch (error) { observed = error; }
    await settle();
    controller.abort();
    await settle();
    assert(observed === failure, "resumed body failure identity changed");
    assert(browser.storage.has(browser.key) && browser.pendingTimers().length === 1, "stock cleanup abort erased resumed-body recovery");
  }

  {
    const sentinel = Promise.resolve(sessionResponse(false, false));
    const browser = createBrowser((url) => String(url).startsWith("/api/chat/get-chat-session/")
      ? sentinel
      : Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 })));
    await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    assert(browser.window.fetch(`/api/chat/get-chat-session/${SESSION}`) === sentinel, "generic session request identity changed");
    await sentinel;
    assert(browser.storage.has(browser.key), "generic session observation cleared reconnect state");
  }

  {
    let calls = 0;
    const browser = createBrowser(() => { calls += 1; return Promise.resolve(streamResponse(["stock"])); }, { noTransform: true });
    const response = await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    assert(await response.text() === "stock" && calls === 1, "missing TransformStream broke or duplicated stock send");
    assert(browser.notices.length === 1 && browser.storage.has(browser.key), "missing TransformStream did not expose manual recovery");
  }

  {
    let calls = 0;
    const browser = createBrowser(() => { calls += 1; return Promise.resolve(streamResponse(["stock"])); }, { storageFailure: true });
    const response = await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    assert(await response.text() === "stock" && calls === 1, "storage failure broke or duplicated stock send");
    assert(browser.notices.length === 1, "storage failure did not expose manual recovery");
  }

  console.log("WEBUI_RECONNECT_COMPANION_CONTRACT_OK");
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
