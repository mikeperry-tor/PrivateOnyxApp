"use strict";

// Execute the shipped settings hook and fetcher, with only React/SWR/navigation
// substituted. This proves the image's CE behavior, not a copy of source logic.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const factories = new Map();

// Next's runtime registers an optional leading shared group before the
// remaining module IDs/factories. Preserve its first-registered identity.
function registerModules(items) {
  let start = 0;
  if (Array.isArray(items[0])) {
    registerModules(items[0]);
    start = 1;
  }
  let ids = [];
  for (const item of items.slice(start)) {
    if (Number.isSafeInteger(item)) ids.push(item);
    else if (typeof item === "function") {
      assert.ok(ids.length, "Module factory has no IDs");
      const factory = ids.map((id) => factories.get(id)).find(Boolean) || item;
      for (const id of ids) if (!factories.has(id)) factories.set(id, factory);
      ids = [];
    } else throw new Error("Unexpected shipped module registration");
  }
  assert.equal(ids.length, 0, "Module IDs have no factory");
}

const context = vm.createContext({
  TURBOPACK: {
    push(chunk) {
      registerModules(chunk.slice(1));
    },
  },
});

function visit(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) visit(file);
    else if (file.endsWith(".js") && !entry.name.startsWith("turbopack-")) {
      const source = fs.readFileSync(file, "utf8");
      // Runtime/bootstrap chunks execute rather than register module factories.
      if (source.includes("globalThis.TURBOPACK") && source.includes(".push([")) {
        vm.runInContext(source, context, { filename: file, timeout: 5000 });
      }
    }
  }
}
visit("/app/.next/static/chunks");

function findFactory(name, markers) {
  const matches = [...new Set(factories.values())].filter((factory) =>
    markers.every((marker) => factory.toString().includes(marker))
  );
  assert.equal(matches.length, 1, `Expected one ${name} module`);
  return matches[0];
}

function execute(factory, imports = new Map()) {
  const exports = {};
  factory({
    i(id) {
      assert.ok(imports.has(id), `Unaudited settings dependency ${id}`);
      return imports.get(id);
    },
    s(entries) {
      for (let i = 0; i < entries.length;) {
        const name = entries[i++];
        const kind = entries[i++];
        exports[name] = kind === 0 ? entries[i++] : kind();
      }
    },
  });
  return exports;
}

// Export names are mangled in the selected image. Locate unique factories by
// their audited semantics, then require their exact current export/import keys.
const fetcher = execute(findFactory("FetchError", ["extends Error", "this.status=", "this.info=", "errorRetryCount"]));
const hookFactory = findFactory("useSettings", ["enterpriseSettings", "shouldRetryOnError", "application_name"]);
const dependencies = [...hookFactory.toString().matchAll(/\w+\.i\((\d+)\)/g)]
  .map((match) => Number(match[1]));
assert.equal(dependencies.length, 9, "Settings dependency shape drifted");
let pathname, core, enterprise, calls;
const settingsKey = "/api/settings";
const enterpriseKey = "/api/enterprise-settings";
const importedValues = [
  { U(key, _fetcher, options) {
    calls.push({ key, options });
    return key === settingsKey ? core : key === enterpriseKey ? enterprise : {};
  } },
  { useMemo: (fn) => fn() },
  { usePathname: () => pathname },
  { f() { throw new Error("CE login must not load connectors"); } },
  fetcher,
  { f: { settings: settingsKey, enterpriseSettings: enterpriseKey } },
  { V: (value) => value.startsWith("/auth/") },
  { E: { ACTIVE: "active" }, a: { NORMAL: "normal" } },
  { C: false },
];
const { s: useSettings } = execute(hookFactory,
  new Map(dependencies.map((id, index) => [id, importedValues[index]])));

for (pathname of ["/auth/login", "/app"]) {
  for (const status of [404, 401, 403, 429, 500]) {
    calls = [];
    core = {};
    const error = new fetcher.t("fixture", status, {});
    enterprise = { error };
    const result = useSettings();
    assert.equal(result.error, status === 404 ? undefined : error);
    assert.equal(result.enterprise, null);
    assert.equal(result.appName, "Onyx");
    assert.equal(result.logoUrl, null);
    const policy = calls.find((call) => call.key === enterpriseKey).options;
    assert.equal(policy.shouldRetryOnError(error), status !== 404);
    assert.equal(policy.shouldRetryOnError({ status: 404 }), true);
    if (pathname === "/auth/login") assert.equal(calls[0].key, null);
  }
}
pathname = "/app";
for (const status of [404, 500]) {
  calls = [];
  core = { error: new fetcher.t("core failure", status, {}) };
  enterprise = { error: new fetcher.t("optional missing", 404, {}) };
  assert.equal(useSettings().error, core.error);
}
console.log("PINNED_WEBUI_CE_SETTINGS_CONTRACT_OK");
