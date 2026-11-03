// kube-noob in a Web Worker. Pyodide (served from this site, no CDN) runs the same Toolkit the MCP server serves,
// with the offline lock on: nothing typed into the page leaves the browser. Protocol: {id, op, name?, args?} in,
// {id, ok, json | error} out, where op is "info", "tools" or "call".
import { loadPyodide } from "../pyodide/pyodide.mjs";

const PACKAGES = ["pydantic", "pyyaml", "jsonschema", "httpx"];
let bridge = null;

async function boot() {
  const base = new URL("../", import.meta.url);
  const py = await loadPyodide({ indexURL: new URL("pyodide/", base).href });
  await py.loadPackage(PACKAGES, { messageCallback: () => {} });
  const manifest = await (await fetch(new URL("py/manifest.json", base))).json();
  const wheel = await fetch(new URL(`py/${manifest.wheel}`, base));
  if (!wheel.ok) throw new Error(`kube-noob wheel not found (${wheel.status})`);
  py.unpackArchive(new Uint8Array(await wheel.arrayBuffer()), "wheel");
  const source = await (await fetch(new URL("py/bridge.py", base))).text();
  py.FS.mkdirTree("/home/pyodide");
  py.FS.writeFile("/home/pyodide/kn_bridge.py", source);
  py.runPython("import sys; sys.path.insert(0, '/home/pyodide')");
  return py.pyimport("kn_bridge");
}

self.onmessage = async (event) => {
  const { id, op, name, args } = event.data || {};
  try {
    bridge ??= boot();
    const b = await bridge;
    let json;
    if (op === "info") json = b.info();
    else if (op === "tools") json = b.tools();
    else if (op === "call") json = b.call(String(name), JSON.stringify(args ?? {}));
    else throw new Error(`unknown op ${op}`);
    self.postMessage({ id, ok: true, json });
  } catch (error) {
    if (op === "info") bridge = null; // a failed start can be retried
    self.postMessage({ id, ok: false, error: String(error?.message ?? error) });
  }
};
