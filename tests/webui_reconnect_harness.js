"use strict";

const fs = require("fs");
const vm = require("vm");

const scriptPath = process.argv[2];
if (!scriptPath) throw new Error("usage: node webui_reconnect_harness.js SCRIPT");
const script = fs.readFileSync(scriptPath, "utf8");
const SESSION = "11111111-1111-4111-8111-111111111111";
const OTHER = "22222222-2222-4222-8222-222222222222";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function settle() {
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
}

function createBrowser(fetchImpl, options = {}) {
  const events = { window: new Map(), document: new Map() };
  const storage = new Map();
  const timers = new Map();
  const notices = [];
  let nextTimer = 1;
  let clock = 1700000000000;
  let reloads = 0;
  let beaconCalls = 0;
  const location = {
    href: `https://onyx.example/chat?chatId=${SESSION}`,
    origin: "https://onyx.example",
    reload() { reloads += 1; },
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
    crypto: {
      randomUUID: (() => {
        let counter = 0;
        return () => `aaaaaaaa-aaaa-4aaa-8aaa-${String(++counter).padStart(12, "0")}`;
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
    pendingTimers: () => [...timers.values()].map((timer) => timer.delay),
    key: window.__privateOnyxReconnectTest.STORAGE_KEY,
  };
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
    const browser = createBrowser(() => { calls += 1; return sentinel; });
    assert(browser.window.fetch("https://other.example/api", { method: "POST" }) === sentinel, "cross-origin fetch identity changed");
    assert(browser.window.fetch("/api/other") === sentinel, "unrelated fetch identity changed");
    assert(calls === 2, "unrelated fetch was duplicated");
    assert(browser.storage.size === 0, "unrelated fetch created state");
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
  }

  {
    const controller = new AbortController();
    const browser = createBrowser(() => new Promise(() => {}));
    browser.window.fetch("/api/chat/send-chat-message", { ...sendInit(), signal: controller.signal });
    controller.abort();
    await Promise.resolve();
    assert(!browser.storage.has(browser.key), "explicit abort retained marker");
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
    const browser = createBrowser(() => Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 })));
    await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    browser.document.visibilityState = "hidden";
    await browser.dispatch("document", "visibilitychange");
    browser.document.visibilityState = "visible";
    await browser.dispatch("document", "visibilitychange");
    await browser.dispatch("window", "pageshow", { persisted: true });
    await browser.dispatch("window", "online");
    await browser.runTimers(3);
    assert(browser.reloads() === 1, "wake signals did not coalesce to one reload");
    await browser.dispatch("window", "pagehide");
    await browser.dispatch("window", "pageshow", { persisted: false });
    await browser.runTimers(1);
    assert(browser.reloads() === 1, "initial pageshow caused reload loop");
    browser.advance(2000);
    browser.document.visibilityState = "hidden";
    await browser.dispatch("document", "visibilitychange");
    browser.document.visibilityState = "visible";
    await browser.dispatch("document", "visibilitychange");
    await browser.runTimers(1);
    assert(browser.reloads() === 2, "second suspension did not permit recovery");
    browser.location.href = `https://onyx.example/chat?chatId=${OTHER}`;
    browser.advance(2000);
    browser.document.visibilityState = "hidden";
    await browser.dispatch("document", "visibilitychange");
    browser.document.visibilityState = "visible";
    await browser.dispatch("document", "visibilitychange");
    await browser.runTimers(1);
    assert(browser.reloads() === 2 && browser.storage.has(browser.key), "other chat was reloaded or marker discarded");
  }

  {
    const browser = createBrowser(() => Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 })));
    await browser.window.fetch("/api/chat/send-chat-message", sendInit());
    await browser.dispatch("window", "online");
    await browser.runTimers(1);
    assert(browser.reloads() === 1, "online transition did not recover an active visible send");
  }

  {
    const browser = createBrowser(() => Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 })));
    browser.storage.set(browser.key, "not json private prompt");
    assert(browser.window.__privateOnyxReconnectTest.loadRecord() === null, "malformed storage survived");
    assert(!browser.storage.has(browser.key), "malformed storage was not discarded");
    browser.storage.set(browser.key, JSON.stringify({
      version: 1,
      token: "aaaaaaaa-aaaa-4aaa-8aaa-000000000001",
      sessionId: SESSION,
      startedAt: 1700000000000 - (4 * 60 * 60 * 1000) - 1,
      multiModel: false,
      hiddenAt: null,
      generation: 0,
      lastRecoveryAt: null,
      pollPhase: null,
      pollAttempt: 0,
    }));
    assert(browser.window.__privateOnyxReconnectTest.loadRecord() === null, "expired storage survived");
  }

  {
    let currentRun = true;
    const browser = createBrowser((url) => {
      if (String(url).startsWith("/api/chat/get-chat-session/")) {
        return Promise.resolve(Response.json({ current_run: currentRun ? { run_id: 9 } : null, incognito: false }));
      }
      return Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 }));
    });
    await browser.window.fetch("/api/chat/send-chat-message", sendInit({ llm_overrides: [{ model: "redacted" }] }));
    browser.document.visibilityState = "hidden";
    await browser.dispatch("document", "visibilitychange");
    browser.document.visibilityState = "visible";
    await browser.dispatch("document", "visibilitychange");
    await browser.runTimers(1);
    assert(browser.reloads() === 1, "multi-model first reconciliation reload missing");
    await browser.window.fetch(`/api/chat/get-chat-session/${SESSION}`);
    await settle();
    assert(browser.pendingTimers().length === 1 && browser.pendingTimers()[0] === 2000, "multi-model poll did not start with backoff");
    await browser.runTimers(1);
    await settle();
    assert(browser.pendingTimers().length === 1 && browser.pendingTimers()[0] === 5000, "multi-model poll did not advance backoff");
    browser.document.visibilityState = "hidden";
    await browser.dispatch("document", "visibilitychange");
    assert(browser.pendingTimers().length === 0, "multi-model poll did not pause while hidden");
    currentRun = false;
    browser.document.visibilityState = "visible";
    await browser.dispatch("document", "visibilitychange");
    await browser.runTimers(1);
    await settle();
    assert(browser.reloads() === 2 && !browser.storage.has(browser.key), "multi-model completion did not settle with one final reload");
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
    browser.window.__privateOnyxReconnectTest.saveRecord({
      version: 1,
      token: "aaaaaaaa-aaaa-4aaa-8aaa-000000000001",
      sessionId: SESSION,
      startedAt: 1700000000000,
      multiModel: false,
      hiddenAt: null,
      generation: 0,
      lastRecoveryAt: null,
      pollPhase: null,
      pollAttempt: 0,
    });
    const result = browser.window.fetch(`/api/chat/end-incognito-session/${SESSION}`, { method: "POST" });
    assert(result === sentinel && calls === 1, "incognito fetch identity/call count changed");
    assert(!browser.storage.has(browser.key), "incognito fetch retained marker");
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
