"use strict";

// Execute the shipped settings hook and fetcher, with only React/SWR/navigation
// substituted. This proves the image's CE behavior, not a copy of source logic.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const factories = new Map();
const context = vm.createContext({
  TURBOPACK: {
    push(chunk) {
      let ids = [];
      for (const item of chunk.slice(1)) {
        if (typeof item === "number") ids.push(item);
        else if (typeof item === "function") {
          for (const id of ids) factories.set(id, item);
          ids = [];
        } else throw new Error("Unexpected shipped module registration");
      }
      assert.equal(ids.length, 0);
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

function findFactory(exportName) {
  const matches = [...new Set(factories.values())].filter((factory) =>
    factory.toString().includes(`"${exportName}",0`)
  );
  assert.equal(matches.length, 1, `Expected one ${exportName} module`);
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

const fetcher = execute(findFactory("FetchError"));
const hookFactory = findFactory("useSettings");
const dependencies = [...hookFactory.toString().matchAll(/\w+\.i\((\d+)\)/g)]
  .map((match) => Number(match[1]));
assert.equal(dependencies.length, 9, "Settings dependency shape drifted");
let pathname, core, enterprise, calls;
const settingsKey = "/api/settings";
const enterpriseKey = "/api/enterprise-settings";
const importedValues = [
  { default(key, _fetcher, options) {
    calls.push({ key, options });
    return key === settingsKey ? core : key === enterpriseKey ? enterprise : {};
  } },
  { useMemo: (fn) => fn() },
  { usePathname: () => pathname },
  { default() { throw new Error("CE login must not load connectors"); } },
  fetcher,
  { SWR_KEYS: { settings: settingsKey, enterpriseSettings: enterpriseKey } },
  { isAuthPath: (value) => value.startsWith("/auth/") },
  { ApplicationStatus: { ACTIVE: "active" }, QueryHistoryType: { NORMAL: "normal" } },
  { EE_ENABLED: false },
];
const { useSettings } = execute(hookFactory,
  new Map(dependencies.map((id, index) => [id, importedValues[index]])));

for (pathname of ["/auth/login", "/app"]) {
  for (const status of [404, 401, 403, 429, 500]) {
    calls = [];
    core = {};
    const error = new fetcher.FetchError("fixture", status, {});
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
  core = { error: new fetcher.FetchError("core failure", status, {}) };
  enterprise = { error: new fetcher.FetchError("optional missing", 404, {}) };
  assert.equal(useSettings().error, core.error);
}
console.log("PINNED_WEBUI_CE_SETTINGS_CONTRACT_OK");
