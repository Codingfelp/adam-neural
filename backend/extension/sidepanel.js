/* global ADAM_SERVER, chrome */
const $ = (id) => document.getElementById(id);
let session = null;
let stopped = false;
let allowAll = false;

const LABELS = {
  clicar: (a) => `Clicar no elemento [${a.indice}]`,
  digitar: (a) => `Digitar "${a.texto}" no campo [${a.indice}]${a.enviar ? " e enviar" : ""}`,
  navegar: (a) => `Abrir ${a.url}`,
  rolar: (a) => `Rolar para ${a.direcao}`,
  voltar: () => "Voltar à página anterior",
  ler_pagina: () => "Ler a página",
};

function log(text, cls = "adam") {
  const el = document.createElement("div");
  el.className = `msg ${cls}`;
  el.textContent = text;
  $("log").appendChild(el);
  $("log").scrollTop = $("log").scrollHeight;
}

async function cfg() {
  return chrome.storage.local.get({ apiKey: "", askEach: true });
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

function snapshotInPage() {
  const sel = 'a[href],button,input:not([type=hidden]),textarea,select,[role=button],[role=link],[role=tab],[role=menuitem],[onclick],[contenteditable=true]';
  document.querySelectorAll("[data-adam-idx]").forEach((e) => e.removeAttribute("data-adam-idx"));
  const els = [];
  let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === "hidden" || st.display === "none") continue;
    if (r.bottom < -200 || r.top > innerHeight * 2.5) continue;
    el.setAttribute("data-adam-idx", String(i));
    const label = (el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.getAttribute("title") || el.getAttribute("name") || "").trim().replace(/\s+/g, " ");
    els.push({ i, tag: el.tagName.toLowerCase(), type: el.getAttribute("type") || "", label: label.slice(0, 100), href: el.getAttribute("href") || "" });
    if (++i >= 150) break;
  }
  return { url: location.href, title: document.title, text: (document.body?.innerText || "").slice(0, 6000), elements: els };
}

function actInPage(name, args) {
  const el = args.indice !== undefined ? document.querySelector(`[data-adam-idx="${args.indice}"]`) : null;
  if ((name === "clicar" || name === "digitar") && !el) return { error: `Elemento [${args.indice}] não encontrado` };
  if (el) {
    el.scrollIntoView({ block: "center" });
    el.style.outline = "3px solid #F4A261";
    setTimeout(() => (el.style.outline = ""), 1200);
  }
  if (name === "clicar") el.click();
  if (name === "digitar") {
    el.focus();
    if (el.isContentEditable) el.textContent = args.texto;
    else {
      const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value").set.call(el, args.texto);
    }
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    if (args.enviar) {
      el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true }));
      if (el.form) el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
    }
  }
  if (name === "rolar") window.scrollBy({ top: (args.direcao === "cima" ? -1 : 1) * innerHeight * 0.8, behavior: "smooth" });
  return { ok: true };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitLoad(tabId) {
  for (let i = 0; i < 40; i++) {
    const t = await chrome.tabs.get(tabId);
    if (t.status === "complete") return;
    await sleep(250);
  }
}

async function snapshot() {
  const tab = await activeTab();
  try {
    const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: snapshotInPage });
    return res.result;
  } catch (e) {
    return { url: tab.url, title: tab.title, text: "(Não é possível ler esta página — páginas internas do Chrome são protegidas.)", elements: [] };
  }
}

async function execute(action) {
  const tab = await activeTab();
  const { name, args } = action;
  try {
    if (name === "navegar") {
      const url = /^https?:\/\//.test(args.url) ? args.url : `https://${args.url}`;
      await chrome.tabs.update(tab.id, { url });
      await sleep(500);
      await waitLoad(tab.id);
    } else if (name === "voltar") {
      await chrome.tabs.goBack(tab.id);
      await sleep(500);
      await waitLoad(tab.id);
    } else if (name !== "ler_pagina") {
      const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: actInPage, args: [name, args] });
      if (res.result?.error) return { error: res.result.error, page: await snapshot() };
      await sleep(1200);
      await waitLoad(tab.id);
    }
    return { page: await snapshot() };
  } catch (e) {
    return { error: String(e.message || e), page: await snapshot() };
  }
}

function askPermission(text) {
  return new Promise((resolve) => {
    $("approvalText").textContent = `O Adam quer: ${text}`;
    $("approval").classList.remove("hidden");
    const done = (v) => { $("approval").classList.add("hidden"); resolve(v); };
    $("allow").onclick = () => done(true);
    $("allowAll").onclick = () => { allowAll = true; done(true); };
    $("deny").onclick = () => done(false);
  });
}

async function api(path, body) {
  const { apiKey } = await cfg();
  const r = await fetch(`${ADAM_SERVER}/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || `Erro ${r.status}`);
  return d;
}

async function run() {
  const task = $("task").value.trim();
  const { apiKey, askEach } = await cfg();
  if (!task) return;
  if (!apiKey) { $("cfg").classList.remove("hidden"); log("Configure sua chave de API primeiro.", "adam err"); return; }
  $("task").value = "";
  log(task, "user");
  stopped = false;
  allowAll = !askEach;
  $("run").disabled = true;
  $("stop").classList.remove("hidden");
  try {
    let res = await api("/v1/agent/start", { task, page: await snapshot() });
    session = res.session_id;
    while (!stopped) {
      if (res.message) log(res.message);
      if (res.done || !res.action) break;
      const desc = (LABELS[res.action.name] || (() => res.action.name))(res.action.args || {});
      const ok = allowAll || res.action.name === "ler_pagina" || (await askPermission(desc));
      if (stopped) break;
      log(`${ok ? "▶" : "✕"} ${desc}`, "step");
      const result = ok ? await execute(res.action) : { denied: true, page: await snapshot() };
      res = await api(`/v1/agent/${session}/step`, { tool_call_id: res.action.id, result });
    }
  } catch (e) {
    log(`Erro: ${e.message}`, "adam err");
  } finally {
    $("run").disabled = false;
    $("stop").classList.add("hidden");
    $("approval").classList.add("hidden");
  }
}

$("run").onclick = run;
$("task").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); run(); } });
$("stop").onclick = async () => {
  stopped = true;
  if (session) api(`/v1/agent/${session}/stop`, {}).catch(() => {});
  log("Tarefa interrompida.", "adam err");
};
$("cfgBtn").onclick = () => $("cfg").classList.toggle("hidden");
$("saveCfg").onclick = async () => {
  await chrome.storage.local.set({ apiKey: $("apiKey").value.trim(), askEach: $("askEach").checked });
  $("cfg").classList.add("hidden");
  log("Configurações salvas.");
};
cfg().then((c) => { $("apiKey").value = c.apiKey; $("askEach").checked = c.askEach; if (!c.apiKey) $("cfg").classList.remove("hidden"); });
