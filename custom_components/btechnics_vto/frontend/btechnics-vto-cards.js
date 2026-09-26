/*
 * Btechnics VTO dashboardkaarten
 *   custom:btechnics-vto-overzicht   status van alle deuren (nieuwe VTO's verschijnen automatisch)
 *   custom:btechnics-vto-toegang     toegangshistoriek: zoeken, filteren, per persoon, per maand, CSV
 *   custom:btechnics-vto-codes       codes en badges per persoon over alle deuren (enkel beheerders)
 * Gegevens komen via de WebSocket-commando's van de integratie; tijden in de tijdzone van Home Assistant.
 */
const C_OPEN = "#0288d1";
const C_REFUSED = "#db4437";
const MONTHS = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
// Label voor een toegang zonder naam (door de integratie gezet volgens de methode), geen persoon.
const LABEL_RE = /^(Binnenpost( \w+)?|Op afstand|Exitknop|Ongeldige invoer|Onbekende badge|Foute code|Onbekende code)$/;
const isLabel = (n) => LABEL_RE.test(String(n || ""));

function errText(e) {
  if (e && typeof e === "object" && e.message) return e.message;
  if (typeof e === "number" || !e) return "geen verbinding met Home Assistant";
  return String(e);
}

const DAYS = ["Zo", "Ma", "Di", "Wo", "Do", "Vr", "Za"];

// Datums als "Za 13 sep 2026" en uren als "23u14", altijd in de tijdzone van Home Assistant.
function formatters(tz) {
  const nf = new Intl.DateTimeFormat("en-US", { timeZone: tz || undefined, year: "numeric", month: "numeric", day: "numeric",
    hour: "numeric", minute: "numeric", second: "numeric", hourCycle: "h23" });
  const parts = (d) => {
    const p = {};
    for (const x of nf.formatToParts(d)) if (x.type !== "literal") p[x.type] = Number(x.value);
    if (p.hour === 24) p.hour = 0;
    p.wd = new Date(Date.UTC(p.year, p.month - 1, p.day)).getUTCDay();
    return p;
  };
  const two = (n) => String(n).padStart(2, "0");
  const date = (p) => `${DAYS[p.wd]} ${p.day} ${MONTHS[p.month - 1]} ${p.year}`;
  const time = (p) => `${two(p.hour)}u${two(p.minute)}`;
  return {
    dateTime: { format: (d) => { const p = parts(d); return `${date(p)}, ${time(p)}`; } },
    date: { format: (d) => date(parts(d)) },
    time: { format: (d) => time(parts(d)) },
    short: { format: (d) => { const p = parts(d); return `${date(p)}, ${time(p)}`; } },
    // CSV: gewone notatie die Excel herkent
    csvDate: { format: (d) => { const p = parts(d); return `${two(p.day)}/${two(p.month)}/${p.year}`; } },
    csvTime: { format: (d) => { const p = parts(d); return `${two(p.hour)}:${two(p.minute)}:${two(p.second)}`; } },
  };
}

/* Foto's bij een toegang: enkel voor beheerders, via een ondertekend pad (een <img> kan geen token meesturen). */
const PHOTO_SRC = new Map();
async function photoSrc(hass, id) {
  const hit = PHOTO_SRC.get(id);
  if (hit && hit.until > Date.now()) return hit.path;
  const r = await hass.callWS({ type: "auth/sign_path", path: `/api/btechnics_vto/foto/${id}`, expires: 3600 });
  PHOTO_SRC.set(id, { path: r.path, until: Date.now() + 3000 * 1000 });
  return r.path;
}
function isAdmin(hass) {
  return !!(hass && hass.user && hass.user.is_admin);
}
function photoBtn(hass, x, cap) {
  if (!x.photo || !isAdmin(hass)) return "";
  return `<button class="photo" data-photo="${esc(x.photo)}" data-cap="${esc(cap)}" title="Foto bekijken" aria-label="Foto bekijken"><ha-icon icon="mdi:camera"></ha-icon></button>`;
}
async function openPhoto(root, hass, id, cap) {
  closePhoto(root);
  const lb = document.createElement("div");
  lb.className = "lb";
  lb.innerHTML = `<figure><div class="lbimg muted">Foto laden...</div><figcaption>${esc(cap || "")}</figcaption>
    <button class="btn" id="lbclose">Sluiten</button></figure>`;
  lb.addEventListener("click", (e) => { if (e.target === lb || e.target.id === "lbclose") closePhoto(root); });
  root.appendChild(lb);
  root._lbKey = (e) => { if (e.key === "Escape") closePhoto(root); };
  document.addEventListener("keydown", root._lbKey);
  try {
    const src = await photoSrc(hass, id);
    const img = new Image();
    img.alt = cap || "Foto";
    img.onload = () => { const box = lb.querySelector(".lbimg"); if (box) box.replaceWith(img); };
    img.onerror = () => { const box = lb.querySelector(".lbimg"); if (box) box.textContent = "Foto niet (meer) beschikbaar."; };
    img.src = src;
  } catch (e) {
    const box = lb.querySelector(".lbimg");
    if (box) box.textContent = `Foto laden mislukt: ${errText(e)}`;
  }
}
function closePhoto(root) {
  const lb = root.querySelector(".lb");
  if (lb) lb.remove();
  if (root._lbKey) { document.removeEventListener("keydown", root._lbKey); root._lbKey = null; }
}

const BASE_CSS = `
  :host { display: block; min-width: 0; }
  ha-card { padding: 16px; }
  .muted { color: var(--secondary-text-color); }
  .title { font-size: 1.25rem; font-weight: 500; margin: 0 0 12px; color: var(--primary-text-color); }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; flex: none; }
  .dot.open { background: ${C_OPEN}; }
  .dot.refused { background: ${C_REFUSED}; }
  .status { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
  table { width: 100%; border-collapse: collapse; font-size: 0.95rem; }
  th { text-align: left; font-weight: 500; color: var(--secondary-text-color); padding: 8px; border-bottom: 1px solid var(--divider-color); white-space: nowrap; }
  td { padding: 8px; border-bottom: 1px solid var(--divider-color); vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .mono { font-family: var(--code-font-family, ui-monospace, Menlo, Consolas, monospace); }
  .link { color: var(--primary-color); cursor: pointer; background: none; border: none; padding: 0; font: inherit; text-align: left; }
  .link:hover { text-decoration: underline; }
  .error { color: var(--error-color, ${C_REFUSED}); padding: 8px 0; }
  .empty { color: var(--secondary-text-color); padding: 16px 0; text-align: center; }
  input, select, button.btn {
    font: inherit; color: var(--primary-text-color); background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 10px; min-height: 40px; box-sizing: border-box;
  }
  button.btn { cursor: pointer; background: var(--secondary-background-color); }
  button.btn:hover { border-color: var(--primary-color); }
  button.btn.active { border-color: var(--primary-color); color: var(--primary-color); }
  .scroll { overflow-x: auto; max-width: 100%; }
  button.photo { background: none; border: 0; padding: 0 2px; cursor: pointer; color: var(--secondary-text-color); --mdc-icon-size: 18px; vertical-align: middle; }
  button.photo:hover, button.photo:focus-visible { color: var(--primary-color); }
  .lb { position: fixed; inset: 0; z-index: 10; background: rgba(0, 0, 0, 0.75); display: flex; align-items: center; justify-content: center; padding: 16px; }
  .lb figure { margin: 0; background: var(--card-background-color, #fff); border-radius: 12px; padding: 12px; max-width: min(920px, 100%);
    display: flex; flex-direction: column; gap: 8px; align-items: center; }
  .lb img { max-width: 100%; max-height: 75vh; border-radius: 8px; display: block; }
  .lb .lbimg { min-width: 240px; min-height: 120px; display: flex; align-items: center; justify-content: center; }
  .lb figcaption { color: var(--primary-text-color); font-size: 0.95rem; text-align: center; }
  button.thumb { border: 0; padding: 0; background: none; cursor: pointer; flex: none; }
  button.thumb img { width: 88px; height: 50px; object-fit: cover; border-radius: 8px; display: block; }
`;

class VtoBase extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this.shadowRoot.addEventListener("click", (e) => {
      const p = e.target.closest("[data-photo]");
      if (!p) return;
      e.stopPropagation();
      openPhoto(this.shadowRoot, this._hass, p.dataset.photo, p.dataset.cap);
    });
  }
  setConfig(config) {
    this._config = config || {};
  }
  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    this._fmt = this._fmt || formatters(hass.config && hass.config.time_zone);
    if (first) this._init();
    else this._hassChanged();
  }
  get hass() {
    return this._hass;
  }
  _init() {}
  _hassChanged() {}
  getCardSize() {
    return 6;
  }
  getGridOptions() {
    return { columns: "full", min_columns: 6 };
  }
  _ws(msg) {
    return this._hass.callWS(msg);
  }
}

/* ------------------------------------------------------------------------ overzicht */

class VtoOverzicht extends VtoBase {
  _init() {
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}
      .doors { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
      .door { border: 1px solid var(--divider-color); border-radius: var(--ha-card-border-radius, 12px); padding: 16px; display: flex; flex-direction: column; gap: 12px; }
      .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
      .name { font-size: 1.15rem; font-weight: 500; }
      .chip { font-size: 0.8rem; padding: 2px 10px; border-radius: 12px; background: var(--secondary-background-color); color: var(--secondary-text-color); display: inline-flex; align-items: center; gap: 6px; }
      .chip.off { color: var(--error-color, ${C_REFUSED}); }
      .last { display: flex; gap: 12px; align-items: center; height: 88px; }
      .last .txt { flex: 1; min-width: 0; }
      .last .line { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.35; }
      .pic { width: 104px; height: 64px; flex: none; border-radius: 8px; background: var(--secondary-background-color);
        display: flex; align-items: center; justify-content: center; color: var(--secondary-text-color); overflow: hidden; }
      .pic button.thumb, .pic button.thumb img { width: 104px; height: 64px; }
      .stat .l { min-height: 2.7em; line-height: 1.35; }
      table.recent { table-layout: fixed; width: 100%; }
      table.recent td { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; height: 22px; }
      table.recent col.c1 { width: 33%; } table.recent col.c2 { width: 26%; } table.recent col.c3 { width: 12%; } table.recent col.c4 { width: 29%; }
      table.recent button.photo { padding: 0 2px; min-height: 0; height: 20px; }
      @media (max-width: 480px) {
        .pic, .pic button.thumb, .pic button.thumb img { width: 72px; height: 48px; }
        .last { height: 80px; } .who.line { font-size: 1.05rem; }
        table.recent col.c1 { width: 38%; } table.recent col.c2 { width: 30%; } table.recent col.c3 { width: 0; } table.recent col.c4 { width: 32%; }
        table.recent td:nth-child(3) { padding: 0; font-size: 0; }
      }
      table.recent button.photo ha-icon { --mdc-icon-size: 16px; }
      .icon { width: 44px; height: 44px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex: none; }
      .icon.open { background: rgba(2, 136, 209, 0.15); color: ${C_OPEN}; }
      .icon.refused { background: rgba(219, 68, 55, 0.15); color: ${C_REFUSED}; }
      .icon.none { background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .who { font-size: 1.2rem; font-weight: 500; }
      .who.line { font-size: 1.2rem; }
      .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
      .stat { background: var(--secondary-background-color); border-radius: 8px; padding: 8px; }
      .stat .v { font-size: 1.2rem; font-weight: 500; font-variant-numeric: tabular-nums; }
      .stat .l { font-size: 0.8rem; color: var(--secondary-text-color); }
      .recent td { padding: 6px 4px; font-size: 0.9rem; }
      .foot { font-size: 0.8rem; color: var(--secondary-text-color); margin-top: 12px; }
      .opener { display: flex; align-items: center; gap: 10px; flex-wrap: nowrap; min-height: 44px; }
      .opener .omsg { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
      .opener button.btn { display: inline-flex; align-items: center; gap: 6px; }
      .opener button.btn.sure { background: var(--error-color, ${C_REFUSED}); color: #fff; border-color: var(--error-color, ${C_REFUSED}); }
      .opener .omsg { font-size: 0.85rem; }
      .opener .omsg.ok { color: var(--success-color, #0b8043); }
      .opener .omsg.error { color: var(--error-color, ${C_REFUSED}); }
    </style><ha-card><div class="title">${esc(this._config.title || "Deuren")}</div><div id="body" class="muted">Laden...</div><div id="foot" class="foot"></div></ha-card>`;
    this._load();
    this._timer = setInterval(() => this._load(), 30000);
  }
  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
  }
  connectedCallback() {
    if (this._hass && !this._timer) {
      this._load();
      this._timer = setInterval(() => this._load(), 30000);
    }
  }
  _hassChanged() {
    // opnieuw laden zodra een "laatste unlock" sensor verandert (nieuwe toegang of verbinding)
    if (!this._data) return;
    const sig = this._data.doors.map((d) => { const s = this._hass.states[d.entities.last_unlock]; return s ? s.last_updated : ""; }).join("|");
    if (sig !== this._sig) {
      this._sig = sig;
      this._load();
    }
  }
  async _load() {
    try {
      const data = await this._ws({ type: "btechnics_vto/doors" });
      this._data = data;
      this._sig = this._data.doors.map((d) => { const s = this._hass.states[d.entities.last_unlock]; return s ? s.last_updated : ""; }).join("|");
      if (this._busyOpen || this.shadowRoot.querySelector("[data-sure='1']")) return;
      this._render();
    } catch (e) {
      this.shadowRoot.getElementById("body").innerHTML = `<div class="error">Kon de deuren niet laden: ${esc(errText(e))}</div>`;
    }
  }
  _render() {
    const f = this._fmt;
    const doors = this._data.doors;
    const body = this.shadowRoot.getElementById("body");
    body.className = "";
    if (!doors.length) {
      body.innerHTML = `<div class="empty">Nog geen VTO's toegevoegd. Voeg ze toe via Instellingen, Apparaten en diensten, Integratie toevoegen, Btechnics VTO.</div>`;
      return;
    }
    body.innerHTML = `<div class="doors">${doors.map((d) => this._door(d, f)).join("")}</div>`;
    body.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => this._open(b)));
    body.querySelectorAll("img[data-thumb]").forEach(async (img) => {
      try { img.src = await photoSrc(this._hass, img.dataset.thumb); } catch (e) { const bt = img.closest("button"); bt.outerHTML = '<ha-icon icon="mdi:camera-off-outline"></ha-icon>'; }
    });
    const a = this._data.archive || {};
    const first = Object.values(a).map((x) => x.first).filter(Boolean).sort()[0];
    this.shadowRoot.getElementById("foot").textContent = first
      ? `Archief sinds ${f.dateTime.format(new Date(first * 1000))}, ${Object.values(a).reduce((n, x) => n + x.count, 0).toLocaleString("nl-BE")} toegangen bewaard.`
      : "";
  }
  _door(d, f) {
    const u = d.last_unlock;
    const cls = !d.available ? "none" : !u ? "none" : u.opened ? "open" : "refused";
    const icon = !d.available ? "mdi:lan-disconnect" : !u ? "mdi:door" : u.opened ? "mdi:door-open" : "mdi:door-closed-lock";
    const who = !d.available ? "Niet bereikbaar" : !u ? "Nog geen toegang" : u.name === "?" ? "Onbekende code" : u.name;
    // twee vaste regels: hoe, en wanneer
    const how = !u ? "" : String(u.method).startsWith("op afstand") ? `${u.opened ? "Geopend" : "Geweigerd"} op afstand`
      : `${u.opened ? "Geopend" : "Geweigerd"} via ${u.method}`;
    const line1 = !d.available ? (u ? `Laatst gekend: ${how}` : "Geen verbinding met het toestel") : how;
    const line2 = u ? f.dateTime.format(new Date(u.ts * 1000)) : "";
    const recent = (d.recent || []).slice(0, 6).map((r) => `<tr>
        <td class="muted">${f.dateTime.format(new Date(r.ts * 1000)).replace(/ \d{4},/, ",")}</td>
        <td>${r.name === "?" ? '<span class="muted">onbekende code</span>' : isLabel(r.name) ? `<span class="muted">${esc(r.name)}</span>` : esc(r.name)}</td>
        <td class="muted">${String(r.method).startsWith("op afstand") ? "afstand" : esc(r.method)}</td>
        <td><span class="status"><span class="dot ${r.opened ? "open" : "refused"}"></span>${r.opened ? "Geopend" : "Geweigerd"}${photoBtn(this._hass, r, `${r.name === "?" ? "Onbekende code" : r.name}, ${d.name}, ${f.dateTime.format(new Date(r.ts * 1000))}`)}</span></td></tr>`).join("")
      + '<tr><td colspan="4">&nbsp;</td></tr>'.repeat(Math.max(0, 6 - Math.min(6, (d.recent || []).length)));
    const thumb = `<div class="pic">${u && u.photo && isAdmin(this._hass)
      ? `<button class="thumb" data-photo="${esc(u.photo)}" data-cap="${esc(`${who}, ${d.name}, ${f.dateTime.format(new Date(u.ts * 1000))}`)}" title="Foto bekijken"><img data-thumb="${esc(u.photo)}" alt="Foto van de laatste toegang"></button>`
      : `<ha-icon icon="mdi:camera-off-outline" title="Geen foto"></ha-icon>`}</div>`;
    return `<div class="door">
      <div class="head"><span class="name">${esc(d.name)}</span>
        <span class="chip ${d.available ? "" : "off"}">${d.available ? "Online" : "Offline"}</span></div>
      <div class="last"><div class="icon ${cls}"><ha-icon icon="${icon}"></ha-icon></div>
        <div class="txt"><div class="who line">${esc(who)}</div><div class="muted line">${esc(line1)}</div><div class="muted line">${esc(line2) || "&nbsp;"}</div></div>${thumb}</div>
      <div class="stats">
        <div class="stat"><div class="v">${d.today.opened}</div><div class="l">Vandaag geopend</div></div>
        <div class="stat"><div class="v">${d.today.refused}</div><div class="l">Vandaag geweigerd</div></div>
        <div class="stat"><div class="v">${d.codes}</div><div class="l">Codes</div></div>
        <div class="stat"><div class="v">${d.cards}</div><div class="l">Badges</div></div>
      </div>
      ${isAdmin(this._hass) ? `<div class="opener"><button class="btn" data-open="${esc(d.id)}" data-name="${esc(d.name)}" ${d.available ? "" : "disabled"}>
        <ha-icon icon="mdi:door-open" style="--mdc-icon-size:18px"></ha-icon> Deur openen</button><span class="omsg" data-omsg="${esc(d.id)}"></span></div>` : ""}
      <table class="recent"><colgroup><col class="c1"><col class="c2"><col class="c3"><col class="c4"></colgroup>${recent}</table>
    </div>`;
  }
  async _open(b) {
    // twee klikken: eerst bevestigen, zodat een deur nooit per ongeluk opengaat
    const msg = this.shadowRoot.querySelector(`[data-omsg="${CSS.escape(b.dataset.open)}"]`);
    if (b.dataset.sure !== "1") {
      b.dataset.sure = "1";
      b.classList.add("sure");
      b.innerHTML = `<ha-icon icon="mdi:alert" style="--mdc-icon-size:18px"></ha-icon> Zeker ${esc(b.dataset.name)} openen?`;
      msg.className = "omsg"; msg.textContent = "Klik nog eens om te openen.";
      clearTimeout(this._sureT);
      this._sureT = setTimeout(() => this._render(), 6000);
      return;
    }
    clearTimeout(this._sureT);
    b.disabled = true;
    msg.className = "omsg"; msg.textContent = "Bezig...";
    this._busyOpen = true;
    try {
      await this._hass.callService("btechnics_vto", "open_door", { door: b.dataset.open });
      msg.className = "omsg ok"; msg.textContent = `${b.dataset.name} is geopend.`;
    } catch (e) {
      msg.className = "omsg error"; msg.textContent = `Niet gelukt: ${errText(e)}`;
    } finally {
      this._busyOpen = false;
      b.disabled = false; b.dataset.sure = ""; b.classList.remove("sure");
      b.innerHTML = `<ha-icon icon="mdi:door-open" style="--mdc-icon-size:18px"></ha-icon> Deur openen`;
      setTimeout(() => { if (!this._busyOpen) this._render(); }, 8000);
    }
  }
  static getStubConfig() {
    return {};
  }
}

/* ------------------------------------------------------------------------ toegang */

const PERIODS = [
  ["1", "Vandaag"], ["7", "7 dagen"], ["30", "30 dagen"], ["90", "90 dagen"], ["365", "1 jaar"], ["custom", "Eigen periode"],
];
const PAGE = 50;

class VtoToegang extends VtoBase {
  disconnectedCallback() {
    clearTimeout(this._deb);
    clearTimeout(this._retry);
  }
  _init() {
    const c = this._config;
    this._state = {
      search: "", person: null, door: "", status: "all",
      period: String(c.period || "365"), from: "", to: "", view: "list", offset: 0,
    };
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}
      .filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
      .filters input[type=search] { flex: 1 1 220px; min-width: 180px; }
      .dates { display: none; gap: 8px; align-items: center; }
      .dates.on { display: inline-flex; }
      .personchip { display: none; align-items: center; gap: 6px; padding: 4px 6px 4px 12px; border-radius: 16px; background: var(--secondary-background-color); }
      .personchip.on { display: inline-flex; }
      .personchip button { border: none; background: none; cursor: pointer; color: var(--secondary-text-color); padding: 2px; display: flex; }
      .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin-bottom: 12px; }
      .kpi { background: var(--secondary-background-color); border-radius: 8px; padding: 10px 12px; }
      .kpi .v { font-size: 1.4rem; font-weight: 500; font-variant-numeric: tabular-nums; }
      .kpi .l { font-size: 0.85rem; color: var(--secondary-text-color); display: flex; align-items: center; gap: 6px; }
      .chartwrap { position: relative; margin: 4px 0 16px; }
      .legend { display: flex; gap: 16px; font-size: 0.85rem; color: var(--secondary-text-color); margin-bottom: 4px; }
      .legend span { display: inline-flex; align-items: center; gap: 6px; }
      svg { display: block; overflow: visible; max-width: 100%; }
      svg text { fill: var(--secondary-text-color); font-size: 11px; }
      svg .grid { stroke: var(--divider-color); stroke-width: 1; }
      .tip { position: absolute; pointer-events: none; background: var(--card-background-color, #fff); color: var(--primary-text-color);
        border: 1px solid var(--divider-color); border-radius: 8px; padding: 6px 10px; font-size: 0.85rem; box-shadow: 0 2px 8px rgba(0,0,0,.15);
        display: none; white-space: nowrap; z-index: 2; }
      .tabs { display: flex; gap: 8px; margin-bottom: 8px; }
      .more { display: flex; justify-content: center; padding: 12px 0 0; }
      .info { font-size: 0.8rem; color: var(--secondary-text-color); margin-top: 8px; }
      td.when { white-space: nowrap; font-variant-numeric: tabular-nums; }
    </style>
    <ha-card>
      <div class="title">${esc(c.title || "Toegangshistoriek")}</div>
      <div class="filters">
        <input id="q" type="search" list="names" placeholder="Zoek persoon of badgenummer" autocomplete="off">
        <datalist id="names"></datalist>
        <span id="pchip" class="personchip"><span id="pname"></span><button id="pclear" title="Persoonfilter wissen"><ha-icon icon="mdi:close" style="--mdc-icon-size:18px"></ha-icon></button></span>
        <select id="door"><option value="">Alle deuren</option></select>
        <select id="status"><option value="all">Alle</option><option value="opened">Geopend</option><option value="refused">Geweigerd</option></select>
        <select id="period">${PERIODS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
        <span id="dates" class="dates"><input id="from" type="date"> tot <input id="to" type="date"></span>
        <button id="csv" class="btn" title="Exporteer de gefilterde toegangen als CSV (Excel)"><ha-icon icon="mdi:download" style="--mdc-icon-size:18px"></ha-icon> CSV</button>
      </div>
      <div id="kpis" class="kpis"></div>
      <div id="chart" class="chartwrap"></div>
      <div class="tabs">
        <button id="tlist" class="btn active">Toegangen</button>
        <button id="tpeople" class="btn">Per persoon</button>
      </div>
      <div id="out" class="scroll"><div class="muted">Laden...</div></div>
      <div id="more" class="more"></div>
      <div id="info" class="info"></div>
    </ha-card>`;
    const $ = (id) => this.shadowRoot.getElementById(id);
    $("period").value = this._state.period;
    $("q").addEventListener("input", (e) => {
      // waarde meteen uitlezen: na het event wijst e.target (shadow DOM) naar de kaart zelf
      const v = e.target.value.trim();
      clearTimeout(this._deb);
      this._deb = setTimeout(() => { this._state.search = v; this._reload(); }, 300);
    });
    $("q").addEventListener("change", (e) => {
      // keuze uit de lijst met namen: meteen exact op die persoon filteren
      const v = e.target.value.trim();
      if (this._names && this._names.includes(v)) this._setPerson(v);
    });
    $("pclear").addEventListener("click", () => this._setPerson(null));
    $("door").addEventListener("change", (e) => { this._state.door = e.target.value; this._reload(); });
    $("status").addEventListener("change", (e) => { this._state.status = e.target.value; this._reload(); });
    $("period").addEventListener("change", (e) => {
      this._state.period = e.target.value;
      $("dates").classList.toggle("on", this._state.period === "custom");
      if (this._state.period !== "custom") this._reload();
    });
    $("from").addEventListener("change", (e) => { this._state.from = e.target.value; this._reload(); });
    $("to").addEventListener("change", (e) => { this._state.to = e.target.value; this._reload(); });
    $("tlist").addEventListener("click", () => this._setView("list"));
    $("tpeople").addEventListener("click", () => this._setView("people"));
    $("csv").addEventListener("click", () => this._csv());
    this.shadowRoot.addEventListener("click", (e) => {
      const b = e.target.closest("[data-person]");
      if (b) this._setPerson(b.dataset.person);
    });
    this._loadDoors();
    this._reload();
    this._ro = new ResizeObserver(() => {
      const w = Math.round(this.shadowRoot.getElementById("chart").clientWidth);
      if (this._res && w && Math.abs(w - (this._chartW || 0)) > 4) this._chart();
    });
    this._ro.observe(this.shadowRoot.getElementById("chart"));
  }
  async _loadDoors() {
    try {
      const d = await this._ws({ type: "btechnics_vto/doors" });
      clearTimeout(this._doorsRetry);
      const sel = this.shadowRoot.getElementById("door");
      sel.innerHTML = `<option value="">Alle deuren</option>` + d.doors.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("");
      sel.value = this._state.door;
      this._archive = d.archive || {};
    } catch (e) {
      // bv. tijdens het opstarten van Home Assistant: later opnieuw proberen
      clearTimeout(this._doorsRetry);
      this._doorsRetry = setTimeout(() => this.isConnected && this._loadDoors(), 10000);
    }
  }
  _setPerson(p) {
    clearTimeout(this._deb); // een nog lopende zoekopdracht mag de persoonkeuze niet overschrijven
    this._state.person = p;
    const $ = (id) => this.shadowRoot.getElementById(id);
    $("pchip").classList.toggle("on", !!p);
    $("pname").textContent = p ? `Persoon: ${p === "?" ? "onbekende code" : p}` : "";
    if (p) { $("q").value = ""; this._state.search = ""; this._state.view = "list"; this._syncTabs(); }
    this._reload();
  }
  _setView(v) {
    this._state.view = v;
    this._syncTabs();
    this._renderOut();
  }
  _syncTabs() {
    this.shadowRoot.getElementById("tlist").classList.toggle("active", this._state.view === "list");
    this.shadowRoot.getElementById("tpeople").classList.toggle("active", this._state.view === "people");
  }
  _filters() {
    const s = this._state;
    const m = {};
    if (s.door) m.door_ids = [s.door];
    if (s.search) m.search = s.search;
    if (s.person !== null) m.person = s.person;
    if (s.status !== "all") m.status = s.status;
    if (s.period === "custom") {
      if (s.from) m.date_from = s.from;
      if (s.to) m.date_to = s.to;
    } else {
      m.days = parseInt(s.period, 10);
    }
    return m;
  }
  async _reload() {
    this._state.offset = 0;
    const seq = (this._seq = (this._seq || 0) + 1);
    clearTimeout(this._retry);
    const mb = this.shadowRoot.getElementById("morebtn");
    if (mb) mb.disabled = true;
    try {
      const res = await this._ws({ type: "btechnics_vto/history", ...this._filters(), limit: PAGE, offset: 0 });
      if (seq !== this._seq) return; // er kwam intussen een nieuwere zoekopdracht
      this._res = res;
      if (!this._state.search && this._state.person === null) {
        this._names = res.people.map((p) => p.name).filter((n) => n !== "?" && !isLabel(n));
        this.shadowRoot.getElementById("names").innerHTML = this._names.map((n) => `<option value="${esc(n)}">`).join("");
      }
      this._render();
    } catch (e) {
      if (seq !== this._seq) return;
      if (e && e.code === "unauthorized") {
        this.shadowRoot.getElementById("out").innerHTML = `<div class="error">Enkel beheerders kunnen de toegangshistoriek bekijken.</div>`;
        return;
      }
      const permanent = e && typeof e === "object" && e.code && !["not_ready", "unknown_error", "timeout"].includes(e.code);
      this.shadowRoot.getElementById("out").innerHTML = `<div class="error">Kon de historiek niet laden: ${esc(errText(e))}.${permanent ? "" : " Nieuwe poging binnen 10 seconden."}</div>`;
      if (!permanent) this._retry = setTimeout(() => this.isConnected && this._reload(), 10000);
    }
  }
  async _loadMore() {
    if (this._busy || !this._res) return;
    this._busy = true;
    const seq = this._seq, res0 = this._res;
    const btn = this.shadowRoot.getElementById("morebtn");
    if (btn) btn.disabled = true;
    try {
      // zelfde momentopname (max_id): geen dubbele of verschoven rijen als er intussen toegangen bijkomen
      const res = await this._ws({ type: "btechnics_vto/history", ...this._filters(), limit: PAGE, offset: res0.rows.length, max_id: res0.max_id });
      if (seq !== this._seq || res0 !== this._res) return; // filters gewijzigd terwijl we laadden
      this._res.rows = this._res.rows.concat(res.rows);
      this._renderOut();
    } catch (e) {
      if (btn) btn.disabled = false;
      this.shadowRoot.getElementById("more").insertAdjacentHTML("beforeend", `<div class="error">Laden mislukt: ${esc(errText(e))}</div>`);
    } finally {
      this._busy = false;
    }
  }
  _render() {
    const r = this._res;
    const $ = (id) => this.shadowRoot.getElementById(id);
    $("kpis").innerHTML = `
      <div class="kpi"><div class="v">${r.total.toLocaleString("nl-BE")}</div><div class="l">Toegangen</div></div>
      <div class="kpi"><div class="v">${r.opened.toLocaleString("nl-BE")}</div><div class="l"><span class="dot open"></span>Geopend</div></div>
      <div class="kpi"><div class="v">${r.refused.toLocaleString("nl-BE")}</div><div class="l"><span class="dot refused"></span>Geweigerd</div></div>
      <div class="kpi"><div class="v">${r.people.filter((p) => p.name !== "?" && !isLabel(p.name)).length.toLocaleString("nl-BE")}</div><div class="l">Personen</div></div>`;
    this._chart();
    this._renderOut();
    const a = this._archive || {};
    const first = Object.values(a).map((x) => x.first).filter(Boolean).sort()[0];
    $("info").textContent = first ? `Het archief bevat toegangen vanaf ${this._fmt.dateTime.format(new Date(first * 1000))}. Oudere gegevens bestaan niet meer op de toestellen.` : "";
  }
  _chart() {
    const wrap = this.shadowRoot.getElementById("chart");
    const months = this._res.months;
    const days = this._state.period === "custom" ? 999 : parseInt(this._state.period, 10);
    if (days < 30 || !months.length) { wrap.innerHTML = ""; return; }
    // aaneensluitende maanden, ook zonder toegangen
    const all = [];
    let [y, m] = months[0].month.split("-").map(Number);
    const [ly, lm] = months[months.length - 1].month.split("-").map(Number);
    const by = Object.fromEntries(months.map((x) => [x.month, x]));
    while (y < ly || (y === ly && m <= lm)) {
      const k = `${y}-${String(m).padStart(2, "0")}`;
      all.push({ key: k, label: `${MONTHS[m - 1]} ${String(y).slice(2)}`, full: `${MONTHS[m - 1]} ${y}`, ...(by[k] || { count: 0, opened: 0 }) });
      m += 1; if (m > 12) { m = 1; y += 1; }
    }
    const W = Math.max(280, Math.round(wrap.clientWidth || 800)), H = 180, top = 18, bottom = 22, left = 36, right = 8;
    this._chartW = W;
    const plotH = H - top - bottom, plotW = W - left - right;
    const max = Math.max(1, ...all.map((x) => x.count));
    // kleinste "mooie" bovengrens boven het maximum (bv. 548 -> 600), zodat de balken de hoogte benutten
    const step = Math.pow(10, Math.floor(Math.log10(max)));
    const nice = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map((f) => f * step).find((v) => v >= max) || max;
    const yv = (v) => top + plotH - (v / nice) * plotH;
    const band = plotW / all.length;
    const bw = Math.min(24, band * 0.6);
    const grid = (nice < 2 ? [0, nice] : [0, nice / 2, nice]).map((v) => `<line class="grid" x1="${left}" x2="${W - right}" y1="${yv(v)}" y2="${yv(v)}"></line>
      <text x="${left - 6}" y="${yv(v) + 4}" text-anchor="end">${Math.round(v).toLocaleString("nl-BE")}</text>`).join("");
    const bars = all.map((x, i) => {
      const cx = left + band * i + band / 2, x0 = cx - bw / 2;
      const ref = x.count - x.opened;
      const hO = (x.opened / nice) * plotH, hR = (ref / nice) * plotH;
      const base = top + plotH;
      const seg = (y1, h, color, rounded) => {
        if (h <= 0) return "";
        const r = rounded ? Math.min(4, h, bw / 2) : 0;
        const yt = y1 - h;
        return `<path fill="${color}" d="M${x0},${y1} V${yt + r} Q${x0},${yt} ${x0 + r},${yt} H${x0 + bw - r} Q${x0 + bw},${yt} ${x0 + bw},${yt + r} V${y1} Z"></path>`;
      };
      const gap = hO > 0 && hR > 0 ? 2 : 0;
      const label = all.length <= 14 && band >= 28 && x.count ? `<text x="${cx}" y="${base - hO - hR - gap - 5}" text-anchor="middle">${x.count}</text>` : "";
      const every = Math.max(1, Math.ceil(40 / band));
      const showX = i % every === 0;
      return `${seg(base, hO, C_OPEN, hR <= 0)}${seg(base - hO - gap, hR, C_REFUSED, true)}${label}
        ${showX ? `<text x="${cx}" y="${H - 6}" text-anchor="middle">${x.label}</text>` : ""}
        <rect class="hit" data-i="${i}" x="${left + band * i}" y="${top}" width="${band}" height="${plotH}" fill="transparent"></rect>`;
    }).join("");
    wrap.innerHTML = `<div class="legend"><span><span class="dot open"></span>Geopend</span><span><span class="dot refused"></span>Geweigerd</span><span>per maand</span></div>
      <svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Toegangen per maand">${grid}${bars}</svg><div class="tip"></div>`;
    const tip = wrap.querySelector(".tip");
    wrap.querySelectorAll(".hit").forEach((el) => {
      el.addEventListener("mouseenter", () => {
        const x = all[+el.dataset.i];
        tip.innerHTML = `<b>${x.full}</b><br>${x.count} toegangen, ${x.opened} geopend, ${x.count - x.opened} geweigerd`;
        tip.style.display = "block";
      });
      el.addEventListener("mousemove", (e) => {
        const b = wrap.getBoundingClientRect();
        const px = e.clientX - b.left;
        tip.style.left = `${Math.min(px + 12, b.width - tip.offsetWidth)}px`;
        tip.style.top = `${e.clientY - b.top - 40}px`;
      });
      el.addEventListener("mouseleave", () => { tip.style.display = "none"; });
    });
  }
  _renderOut() {
    const r = this._res;
    if (!r) return;
    const f = this._fmt;
    const out = this.shadowRoot.getElementById("out");
    const more = this.shadowRoot.getElementById("more");
    more.innerHTML = "";
    if (this._state.view === "people") {
      if (!r.people.length) { out.innerHTML = `<div class="empty">Geen toegangen gevonden</div>`; return; }
      out.innerHTML = `<table><thead><tr><th>Persoon</th><th class="num">Toegangen</th><th class="num">Geopend</th><th class="num">Geweigerd</th><th>Laatst</th><th>Deuren</th></tr></thead><tbody>
        ${r.people.map((p) => `<tr>
          <td><button class="link${isLabel(p.name) ? " muted" : ""}" data-person="${esc(p.name)}">${p.name === "?" ? "onbekende code" : esc(p.name)}</button></td>
          <td class="num">${p.count}</td><td class="num">${p.opened}</td><td class="num">${p.refused}</td>
          <td class="when">${f.dateTime.format(new Date(p.last * 1000))}</td><td class="muted">${p.doors.map(esc).join(", ")}</td></tr>`).join("")}
        </tbody></table>`;
      return;
    }
    if (!r.rows.length) { out.innerHTML = `<div class="empty">Geen toegangen gevonden</div>`; return; }
    out.innerHTML = `<table><thead><tr><th>Datum</th><th>Tijd</th><th>Deur</th><th>Persoon</th><th>Methode</th><th>Status</th></tr></thead><tbody>
      ${r.rows.map((x) => { const d = new Date(x.ts * 1000); return `<tr>
        <td class="when">${f.date.format(d)}</td><td class="when">${f.time.format(d)}</td><td>${esc(x.door)}</td>
        <td>${x.name === "?" ? '<button class="link muted" data-person="?">onbekende code</button>' : `<button class="link${isLabel(x.name) ? " muted" : ""}" data-person="${esc(x.name)}">${esc(x.name)}</button>`}</td>
        <td class="muted">${esc(x.method)}${x.card ? ` <span class="mono">${esc(x.card)}</span>` : ""}</td>
        <td><span class="status"><span class="dot ${x.opened ? "open" : "refused"}"></span>${x.opened ? "Geopend" : "Geweigerd"}${photoBtn(this._hass, x, `${x.name === "?" ? "Onbekende code" : x.name}, ${x.door}, ${f.dateTime.format(d)}`)}</span></td></tr>`; }).join("")}
      </tbody></table>`;
    if (r.rows.length < r.total) {
      more.innerHTML = `<button class="btn" id="morebtn">Meer tonen (${r.rows.length} van ${r.total.toLocaleString("nl-BE")})</button>`;
      more.querySelector("#morebtn").addEventListener("click", () => this._loadMore());
    }
  }
  async _csv() {
    const btn = this.shadowRoot.getElementById("csv");
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      await this._csvExport();
    } catch (e) {
      this.shadowRoot.getElementById("info").innerHTML = `<span class="error">CSV export mislukt: ${esc(e.message || e)}</span>`;
    } finally {
      if (btn) btn.disabled = false;
      this._busy = false;
    }
  }
  async _csvExport() {
    // in blokken ophalen (geen bovengrens), allemaal uit dezelfde momentopname en dezelfde filters
    const CHUNK = 20000;
    const filt = this._filters();
    let rows = [], maxId;
    for (;;) {
      const res = await this._ws({ type: "btechnics_vto/history", ...filt, limit: CHUNK, offset: rows.length, ...(maxId ? { max_id: maxId } : {}) });
      maxId = res.max_id;
      rows = rows.concat(res.rows);
      if (res.rows.length < CHUNK || rows.length >= res.total) break;
    }
    const f = this._fmt;
    // tekst die Excel als formule zou uitvoeren (=, +, -, @) onschadelijk maken
    const q = (v) => { let t = String(v ?? ""); if (/^[=+\-@\t\r]/.test(t)) t = "'" + t; return `"${t.replace(/"/g, '""')}"`; };
    const lines = [["Datum", "Tijd", "Deur", "Persoon", "Methode", "Badge", "Status"].map(q).join(";")];
    for (const x of rows) {
      const d = new Date(x.ts * 1000);
      lines.push([f.csvDate.format(d), f.csvTime.format(d), x.door, x.name === "?" ? "onbekende code" : x.name, x.method, x.card, x.opened ? "Geopend" : "Geweigerd"].map(q).join(";"));
    }
    const blob = new Blob([String.fromCharCode(0xfeff) + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    const dn = new Date(); const pad = (n) => String(n).padStart(2, "0");
    a.download = `toegangen-${dn.getFullYear()}-${pad(dn.getMonth() + 1)}-${pad(dn.getDate())}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }
  getCardSize() {
    return 12;
  }
  static getStubConfig() {
    return { period: 365 };
  }
}

/* ------------------------------------------------------------------------ codes */

class VtoCodes extends VtoBase {
  _init() {
    this._show = false;
    this._q = "";
    this._status = "active";
    this._kind = "all";
    this._form = null;      // { type, entry }
    this._auditAll = false;
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}
      .bar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; align-items: center; }
      .bar input[type=search] { flex: 1 1 220px; min-width: 180px; }
      .tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
      .hidden { letter-spacing: 2px; color: var(--secondary-text-color); }
      .chip { display: inline-flex; align-items: center; gap: 4px; font-size: 0.8rem; padding: 2px 8px; border-radius: 12px;
        background: var(--secondary-background-color); margin: 0 4px 4px 0; white-space: nowrap; }
      .chip.stored { color: var(--secondary-text-color); border: 1px dashed var(--divider-color); background: none; }
      .acts { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
      .acts button.btn { min-height: 32px; padding: 4px 10px; font-size: 0.85rem; }
      button.btn.danger { color: var(--error-color, ${C_REFUSED}); }
      button.btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
      .dot.blocked { background: var(--warning-color, #f4b400); }
      .dot.retired { background: var(--disabled-text-color, #9e9e9e); }
      .warn { color: var(--warning-color, #b06000); font-size: 0.85rem; }
      .panel { border: 1px solid var(--divider-color); border-radius: 8px; padding: 12px; margin-bottom: 12px; display: none; }
      .panel.on { display: block; }
      .panel h3 { margin: 0 0 8px; font-size: 1rem; font-weight: 500; }
      .row { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 10px; }
      .row label { display: inline-flex; align-items: center; gap: 6px; }
      .row input[type=text] { min-width: 220px; }
      .msg { margin: 0 0 12px; font-size: 0.9rem; }
      .msg.ok { color: var(--success-color, #0b8043); }
      .audit td { font-size: 0.85rem; }
      td.when { white-space: nowrap; font-variant-numeric: tabular-nums; }
      td .pname { font-weight: 500; }
      button.linkbtn { background: none; border: 0; padding: 0; font: inherit; color: var(--primary-text-color); cursor: pointer; text-align: left; }
      button.linkbtn:hover, button.linkbtn:focus-visible { color: var(--primary-color); text-decoration: underline; }
      .hist .kpis { display: flex; flex-wrap: wrap; gap: 16px; margin: 4px 0 10px; font-size: 0.9rem; }
      .hist .kpis b { font-size: 1.1rem; font-weight: 500; font-variant-numeric: tabular-nums; }
      .hist .head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
      .hist td { font-size: 0.9rem; }
      .more { display: flex; justify-content: center; padding: 12px 0 0; }
      .valid { font-size: 0.8rem; color: var(--secondary-text-color); margin-top: 2px; }
      .share textarea { width: 100%; box-sizing: border-box; min-height: 150px; font: inherit; padding: 8px; border-radius: 8px;
        border: 1px solid var(--divider-color); background: var(--card-background-color, #fff); color: var(--primary-text-color); }
      .share .sendbar { display: flex; flex-wrap: wrap; gap: 8px; }
      .share a.btn { text-decoration: none; display: inline-flex; align-items: center; gap: 6px; font: inherit; color: var(--primary-text-color);
        background: var(--secondary-background-color); border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 12px;
        min-height: 40px; box-sizing: border-box; cursor: pointer; }
      .share a.btn:hover { border-color: var(--primary-color); }
      .share a.btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
      @media (max-width: 640px) {
        table.list thead { display: none; }
        table.list, table.list tbody, table.list tr, table.list td { display: block; width: auto; }
        table.list tr { border-bottom: 1px solid var(--divider-color); padding: 8px 0; }
        table.list td { border: 0; padding: 2px 0; }
        table.list td.kind, table.list td.kind + td { display: inline-block; margin-right: 8px; }
        .acts { justify-content: flex-start; margin-top: 6px; }
      }
    </style>
    <ha-card>
      <div class="title">${esc(this._config.title || "Codes en badges")}</div>
      <div class="bar">
        <input id="q" type="search" placeholder="Zoek persoon, code of badgenummer" autocomplete="off">
        <select id="kind"><option value="all">Codes en badges</option><option value="code">Codes</option><option value="badge">Badges</option></select>
        <button id="toggle" class="btn"><ha-icon icon="mdi:eye" style="--mdc-icon-size:18px"></ha-icon> Codes tonen</button>
        <button id="quick" class="btn primary"><ha-icon icon="mdi:timer-outline" style="--mdc-icon-size:18px"></ha-icon> Tijdelijke code</button>
        <button id="new" class="btn primary"><ha-icon icon="mdi:plus" style="--mdc-icon-size:18px"></ha-icon> Nieuwe code</button>
        <button id="newbadge" class="btn primary"><ha-icon icon="mdi:card-account-details-outline" style="--mdc-icon-size:18px"></ha-icon> Nieuwe badge</button>
      </div>
      <div class="tabs" id="tabs"></div>
      <div id="panel" class="panel"></div>
      <div id="hist" class="panel hist"></div>
      <div id="msg" class="msg"></div>
      <div id="out" class="scroll"><div class="muted">Laden...</div></div>
      <div class="title" style="margin-top:20px;font-size:1.05rem">Wijzigingen</div>
      <div id="audit" class="scroll"></div>
    </ha-card>`;
    const $ = (id) => this.shadowRoot.getElementById(id);
    $("q").addEventListener("input", (e) => { this._q = e.target.value.trim().toLowerCase(); this._render(); });
    $("kind").addEventListener("change", (e) => { this._kind = e.target.value; this._render(); });
    $("toggle").addEventListener("click", () => {
      this._show = !this._show;
      $("toggle").innerHTML = `<ha-icon icon="${this._show ? "mdi:eye-off" : "mdi:eye"}" style="--mdc-icon-size:18px"></ha-icon> ${this._show ? "Codes verbergen" : "Codes tonen"}`;
      this._render();
    });
    $("new").addEventListener("click", () => this._openForm("add", null));
    $("quick").addEventListener("click", () => this._openQuick());
    $("newbadge").addEventListener("click", () => this._openForm("addbadge", null));
    this.shadowRoot.addEventListener("click", (e) => {
      const h = e.target.closest("[data-hist]");
      if (h) return this._history(h.dataset.hist);
      const b = e.target.closest("[data-act]");
      if (!b) return;
      const entry = this._data && this._data.entries.find((x) => x.id === b.dataset.id);
      this._action(b.dataset.act, entry);
    });
    this._load();
    this._timer = setInterval(() => this._load(), 30000);
  }
  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
  }
  connectedCallback() {
    if (this._hass && !this._timer) {
      this._load();
      this._timer = setInterval(() => this._load(), 30000);
    }
  }
  async _load() {
    // niet verversen terwijl een formulier open is of een actie loopt: anders verdwijnt de invoer
    if (this._busy || this._form) return;
    try {
      this._data = await this._ws({ type: "btechnics_vto/manage/list" });
      this._render();
    } catch (e) {
      const msg = e && e.code === "unauthorized" ? "Enkel beheerders kunnen de codes bekijken." : `Kon de codes niet laden: ${esc(errText(e))}`;
      this.shadowRoot.getElementById("out").innerHTML = `<div class="error">${msg}</div>`;
    }
  }
  async _history(name, more) {
    // toegangsgeschiedenis van een persoon (codes en badges), laatste jaar, uit het archief
    const el = this.shadowRoot.getElementById("hist");
    if (!name) return;
    const f = this._fmt;
    if (!more) {
      this._hist = { name, res: null, seq: (this._hist ? this._hist.seq : 0) + 1 };
      el.innerHTML = `<div class="head"><h3>Geschiedenis: ${esc(name)}</h3><button class="btn" id="hclose">Sluiten</button></div><div class="muted">Laden...</div>`;
      el.classList.add("on");
      el.querySelector("#hclose").addEventListener("click", () => this._closeHistory());
      el.scrollIntoView({ block: "nearest" });
    }
    const h = this._hist, seq = h.seq;
    try {
      const msg = { type: "btechnics_vto/history", person: name, days: 365, limit: 25, offset: more ? h.res.rows.length : 0 };
      if (more) msg.max_id = h.res.max_id;
      const res = await this._ws(msg);
      if (!this._hist || this._hist.seq !== seq) return; // intussen een andere persoon gekozen of gesloten
      if (more) { h.res.rows = h.res.rows.concat(res.rows); } else { h.res = res; }
      const r = h.res;
      const last = r.rows[0];
      el.innerHTML = `<div class="head"><h3>Geschiedenis: ${esc(name)}</h3><button class="btn" id="hclose">Sluiten</button></div>
        <div class="kpis"><span><b>${r.total.toLocaleString("nl-BE")}</b> toegangen (laatste jaar)</span><span><b>${r.opened.toLocaleString("nl-BE")}</b> geopend</span>
          <span><b>${r.refused.toLocaleString("nl-BE")}</b> geweigerd</span>${last ? `<span>Laatst: <b>${esc(f.dateTime.format(new Date(last.ts * 1000)))}</b></span>` : ""}</div>
        ${r.rows.length ? `<div class="scroll"><table><thead><tr><th>Wanneer</th><th>Deur</th><th>Hoe</th><th>Status</th></tr></thead><tbody>
          ${r.rows.map((x) => `<tr><td class="when">${esc(f.dateTime.format(new Date(x.ts * 1000)))}</td><td>${esc(x.door)}</td><td class="muted">${esc(x.method)}</td>
            <td><span class="status"><span class="dot ${x.opened ? "open" : "refused"}"></span>${x.opened ? "Geopend" : "Geweigerd"}${photoBtn(this._hass, x, `${name}, ${x.door}, ${f.dateTime.format(new Date(x.ts * 1000))}`)}</span></td></tr>`).join("")}
          </tbody></table></div>` : `<div class="empty">Geen toegangen in het laatste jaar.</div>`}
        ${r.rows.length < r.total ? `<div class="more"><button class="btn" id="hmore">Meer tonen (${r.rows.length} van ${r.total.toLocaleString("nl-BE")})</button></div>` : ""}`;
      el.querySelector("#hclose").addEventListener("click", () => this._closeHistory());
      const hm = el.querySelector("#hmore");
      if (hm) hm.addEventListener("click", () => { hm.disabled = true; this._history(name, true); });
    } catch (e) {
      if (!this._hist || this._hist.seq !== seq) return;
      el.insertAdjacentHTML("beforeend", `<div class="error">Kon de geschiedenis niet laden: ${esc(errText(e))}</div>`);
    }
  }
  _closeHistory() {
    const el = this.shadowRoot.getElementById("hist");
    this._hist = { seq: (this._hist ? this._hist.seq : 0) + 1 };
    el.classList.remove("on");
    el.innerHTML = "";
  }
  _message(text, ok) {
    const el = this.shadowRoot.getElementById("msg");
    el.className = "msg " + (ok ? "ok" : "error");
    el.textContent = text || "";
  }
  _waiting(e) {
    return e.status === "blocked" && e.valid_from && e.until === e.valid_from;
  }
  _statusText(e) {
    if (e.status === "active") return "Actief";
    if (this._waiting(e)) return "Wacht op begin: " + this._fmt.dateTime.format(new Date(e.valid_from));
    if (e.status === "retired") return "Uit dienst";
    if (!e.until) return "Geblokkeerd tot deblokkeren";
    return "Geblokkeerd tot " + this._fmt.dateTime.format(new Date(e.until));
  }
  _render() {
    if (!this._data) return;
    const { entries, audit } = this._data;
    const $ = (id) => this.shadowRoot.getElementById(id);
    const count = (s) => entries.filter((e) => e.status === s).length;
    const tabs = [["active", "Actief", count("active")], ["blocked", "Geblokkeerd", count("blocked")],
      ["retired", "Uit dienst", count("retired")], ["all", "Alles", entries.length]];
    $("tabs").innerHTML = tabs.map(([v, l, n]) => `<button class="btn ${this._status === v ? "active" : ""}" data-tab="${v}">${l} (${n})</button>`).join("");
    $("tabs").querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { this._status = b.dataset.tab; this._render(); }));
    const q = this._q;
    const list = entries.filter((e) => (this._status === "all" || e.status === this._status)
      && (this._kind === "all" || e.kind === this._kind)
      && (!q || e.name.toLowerCase().includes(q) || String(e.secret).toLowerCase().includes(q)));
    const sec = (e) => e.kind === "badge"
      ? `<span class="mono">${esc(e.secret)}</span>`
      : (this._show ? `<span class="mono">${esc(e.secret)}</span>` : `<span class="hidden">&bull;&bull;&bull;&bull;&bull;&bull;</span>`);
    const acts = (e) => {
      const b = (act, label, cls) => `<button class="btn ${cls || ""}" data-act="${act}" data-id="${esc(e.id)}">${label}</button>`;
      const share = e.kind === "code" ? b("share", "Delen") : "";
      if (e.status === "active") return share + b("edit", "Aanpassen") + b("block", "Blokkeren") + b("retire", "Uit dienst", "danger");
      if (this._waiting(e)) return share + b("edit", "Aanpassen") + b("unblock", "Nu al activeren") + b("retire", "Uit dienst", "danger");
      if (e.status === "blocked") return b("unblock", "Deblokkeren", "primary") + b("block", "Einde aanpassen") + b("edit", "Aanpassen") + b("retire", "Uit dienst", "danger");
      return b("restore", "Herstellen", "primary") + b("edit", "Aanpassen") + b("forget", "Definitief verwijderen", "danger");
    };
    const dot = (e) => `<span class="dot ${e.status === "active" ? "open" : e.status}"></span>`;
    if (!list.length) {
      $("out").innerHTML = `<div class="empty">Niets gevonden</div>`;
    } else {
      $("out").innerHTML = `<table class="list"><thead><tr><th>Persoon</th><th>Soort</th><th>Code of badge</th><th>Deuren</th><th>Status</th><th></th></tr></thead><tbody>
        ${list.map((e) => `<tr>
          <td><button class="pname linkbtn" data-hist="${esc(e.name || "")}" title="Toegangsgeschiedenis van ${esc(e.name || "?")}">${esc(e.name || "?")}</button></td>
          <td class="muted kind">${e.kind === "badge" ? "badge" : "code"}</td>
          <td>${sec(e)}</td>
          <td>${e.doors.map((d) => `<span class="chip">${esc(d.name)}</span>`).join("")}${e.stored.map((d) => `<span class="chip stored" title="bewaard, niet op het toestel">${esc(d.name)}</span>`).join("")}</td>
          <td><span class="status">${dot(e)}${esc(this._statusText(e))}</span>
            ${e.valid_until && e.status !== "retired" ? `<div class="valid">Geldig tot ${esc(this._fmt.dateTime.format(new Date(e.valid_until)))}</div>` : ""}
            ${e.max_uses && e.status !== "retired" ? `<div class="valid">${e.max_uses === 1 ? "Eenmalig" : `${e.uses || 0} van ${e.max_uses} keer gebruikt`}</div>` : ""}
            ${e.status !== "active" && e.doors.length ? `<div class="warn">Nog actief op ${e.doors.map((d) => esc(d.name)).join(", ")}</div>` : ""}</td>
          <td><div class="acts">${acts(e)}</div></td></tr>`).join("")}
        </tbody></table>`;
    }
    const shown = this._auditAll ? audit : audit.slice(0, 15);
    $("audit").innerHTML = audit.length ? `<table class="audit"><thead><tr><th>Wanneer</th><th>Wie</th><th>Actie</th><th>Persoon</th><th>Deuren</th><th>Details</th></tr></thead><tbody>
      ${shown.map((a) => `<tr><td class="when">${this._fmt.dateTime.format(new Date(a.ts))}</td><td>${esc(a.user)}</td><td>${esc(a.action)}</td>
        <td>${esc(a.name)} <span class="muted">(${a.kind === "badge" ? "badge" : a.kind === "deur" ? "deur" : "code"})</span></td><td class="muted">${esc((a.doors || []).join(", "))}</td>
        <td class="muted">${esc(a.detail || "")}</td></tr>`).join("")}</tbody></table>
      ${audit.length > 15 ? `<div class="more"><button class="btn" id="auditmore">${this._auditAll ? "Minder tonen" : `Alles tonen (${audit.length})`}</button></div>` : ""}`
      : `<div class="muted">Nog geen wijzigingen.</div>`;
    const am = $("auditmore");
    if (am) am.addEventListener("click", () => { this._auditAll = !this._auditAll; this._render(); });
  }
  _action(act, e) {
    if (act === "edit" || act === "block") return this._openForm(act, e);
    if (act === "share") return this._openShare(e);
    if (act === "retire") return this._openForm("confirm", e, "retire",
      `'${esc(e.name)}' uit dienst halen? De ${e.kind === "badge" ? "badge" : "code"} wordt van alle toestellen gehaald en bewaard, zodat je ze later kan herstellen.`);
    if (act === "forget") return this._openForm("confirm", e, "forget",
      `'${esc(e.name)}' definitief uit de lijst verwijderen? Dit kan niet ongedaan gemaakt worden. De toegangshistoriek blijft bewaard.`);
    return this._run({ action: act, entry: e.id }, act === "unblock" ? (this._waiting(e) ? "Geactiveerd" : "Gedeblokkeerd") : "Hersteld");
  }
  _localInput(ms) {
    // datum en uur voor een datetime-local veld, in de tijdzone van Home Assistant (niet die van de browser)
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: (this._hass.config || {}).time_zone || undefined,
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(new Date(ms)).filter((p) => p.type !== "literal").map((p) => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour === "24" ? "00" : parts.hour}:${parts.minute}`;
  }
  _validityRows(e) {
    const vf = e && e.valid_from ? this._localInput(Date.parse(e.valid_from)) : "";
    const vu = e && e.valid_until ? this._localInput(Date.parse(e.valid_until)) : "";
    return `<div class="row"><label>Geldig vanaf <input id="vfrom" type="datetime-local" value="${vf}"></label>
        <label>Geldig tot <input id="vuntil" type="datetime-local" value="${vu}"></label>
        <span class="muted">Leeg = vanaf nu, zonder einde. Na het einde gaat de code vanzelf uit dienst (herstelbaar).</span></div>`;
  }
  _readValidity(p, curVu) {
    const full = (v) => (v && v.length === 16 ? v + ":00" : v || "");
    const vf = full(p.querySelector("#vfrom").value), vu = full(p.querySelector("#vuntil").value);
    if (vf && vu && vu <= vf) return { error: "Het einde van de geldigheid moet na het begin liggen." };
    if (vu && vu !== curVu && vu <= this._localInput(Date.now()) + ":00") return { error: "Het einde van de geldigheid ligt in het verleden." };
    return { vf, vu };
  }
  _shareText(e) {
    const f = this._fmt;
    const doors = (e.doors.length ? e.doors : e.stored).map((d) => d.name);
    const doorTxt = doors.length > 1 ? `${doors.slice(0, -1).join(", ")} en ${doors[doors.length - 1]}` : doors.join("");
    const when = (iso) => f.dateTime.format(new Date(iso)).replace(", ", " om ");
    let valid = "Geldig vanaf nu, zonder einddatum.";
    if (e.valid_from && e.valid_until) valid = `Geldig van ${when(e.valid_from)} tot ${when(e.valid_until)}.`;
    else if (e.valid_from) valid = `Geldig vanaf ${when(e.valid_from)}.`;
    else if (e.valid_until) valid = `Geldig tot ${when(e.valid_until)}.`;
    if (e.max_uses) valid += e.max_uses === 1 ? " De code werkt maar een keer." : ` De code werkt maximaal ${e.max_uses} keer.`;
    const hi = /^(Pakket|Technicus|Gast|Andere)\b/.test(e.name) ? "Hallo" : `Hallo ${e.name}`;
    return `${hi},\n\nJe toegangscode voor Trefpunt is ${e.secret}.\n${doors.length > 1 ? "Deuren" : "Deur"}: ${doorTxt}.\n${valid}\n\nZo open je de deur: typ op het klavier # ${e.secret} # (hekje, je code, hekje). Hou de code voor jezelf.`;
  }
  _openQuick() {
    // Tijdelijke code in een paar klikken, naar het voorbeeld van eenmalige en tijdelijke pincodes bij slimme sloten:
    // doel kiezen, deur(en), geldigheid; Home Assistant kiest zelf een willekeurige vrije code; meteen delen.
    if (!this._data) return this._message("De codes zijn nog niet geladen.");
    const p = this.shadowRoot.getElementById("panel");
    const doors = this._data.doors;
    this._message("");
    this._form = { type: "quick" };
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem("btxvto_quick_doors") || "[]"); } catch (err) { saved = []; }
    const TYPES = [["Pakket", "24", 1], ["Technicus", "today", 0], ["Gast", "3d", 0], ["Andere", "24", 0]];
    const DUR = [["1", "1 uur"], ["today", "Vandaag"], ["24", "24 uur"], ["3d", "3 dagen"], ["7d", "1 week"], ["custom", "Eigen"]];
    const st = { type: "Pakket", dur: "24", once: true };
    const day = () => { const x = this._localInput(Date.now()); return `${Number(x.slice(8, 10))}/${Number(x.slice(5, 7))}`; };
    p.innerHTML = `<h3>Tijdelijke code</h3>
      <div class="row" id="qtype">${TYPES.map(([t]) => `<button class="btn" data-qt="${t}">${t}</button>`).join("")}</div>
      <div class="row"><label>Naam <input id="qname" type="text" maxlength="30"></label>
        <span class="muted">Bv. "Pakket bol" of "Technicus Fluvius". Komt in de historiek.</span></div>
      <div class="row">Deuren: ${doors.map((d) => `<label><input type="checkbox" class="qd" value="${esc(d.id)}" ${saved.includes(d.id) || (!saved.length && d === doors[0]) ? "checked" : ""}> ${esc(d.name)}</label>`).join("")}</div>
      <div class="row" id="qdur">Geldig: ${DUR.map(([k, l]) => `<button class="btn" data-qd="${k}">${l}</button>`).join("")}</div>
      <div class="row" id="qcustom" style="display:none"><label>Vanaf <input id="vfrom" type="datetime-local"></label><label>Tot <input id="vuntil" type="datetime-local"></label></div>
      <div class="row"><label><input type="checkbox" id="qonce"> Eenmalig: na de eerste opening gaat de code vanzelf uit dienst</label></div>
      <div class="row muted" id="qsum"></div>
      <div class="row"><button class="btn primary" id="ok">Maak code en deel</button><button class="btn" id="cancel">Annuleren</button></div>`;
    const $ = (id) => p.querySelector("#" + id);
    let nameTouched = false;
    $("qname").addEventListener("input", () => { nameTouched = true; });
    const until = () => {
      const now = Date.now(), H = 3600000;
      const endOfDay = () => { const x = this._localInput(now); return `${x.slice(0, 10)}T23:59:00`; };
      if (st.dur === "1") return this._localInput(now + H) + ":00";
      if (st.dur === "today") return endOfDay();
      if (st.dur === "24") return this._localInput(now + 24 * H) + ":00";
      if (st.dur === "3d") return this._localInput(now + 72 * H) + ":00";
      if (st.dur === "7d") return this._localInput(now + 168 * H) + ":00";
      const v = $("vuntil").value;
      return v ? (v.length === 16 ? v + ":00" : v) : "";
    };
    const draw = () => {
      p.querySelectorAll("[data-qt]").forEach((b) => b.classList.toggle("active", b.dataset.qt === st.type));
      p.querySelectorAll("[data-qd]").forEach((b) => b.classList.toggle("active", b.dataset.qd === st.dur));
      $("qcustom").style.display = st.dur === "custom" ? "flex" : "none";
      $("qonce").checked = st.once;
      if (!nameTouched) $("qname").value = `${st.type} ${day()}`;
      const u = until();
      $("qsum").textContent = u ? `Geldig tot ${this._fmt.dateTime.format(new Date(Date.parse(u + "Z") - this._tzOffset(u)))}${st.once ? ", eenmalig" : ""}. Home Assistant kiest een willekeurige code van 6 cijfers.` : "Kies een einde.";
    };
    p.querySelectorAll("[data-qt]").forEach((b) => b.addEventListener("click", () => {
      const t = TYPES.find((x) => x[0] === b.dataset.qt); st.type = t[0]; st.dur = t[1]; st.once = !!t[2]; draw();
    }));
    p.querySelectorAll("[data-qd]").forEach((b) => b.addEventListener("click", () => { st.dur = b.dataset.qd; draw(); }));
    $("qonce").addEventListener("change", (ev) => { st.once = ev.target.checked; draw(); });
    ["vfrom", "vuntil"].forEach((id) => $(id).addEventListener("input", draw));
    $("ok").addEventListener("click", () => {
      const name = $("qname").value.trim();
      const sel = [...p.querySelectorAll(".qd:checked")].map((x) => x.value);
      if (!name) return this._message("Geef een naam.");
      if (!sel.length) return this._message("Kies minstens een deur.");
      const vu = until();
      const vf = st.dur === "custom" && $("vfrom").value ? ($("vfrom").value.length === 16 ? $("vfrom").value + ":00" : $("vfrom").value) : "";
      if (!vu) return this._message("Kies tot wanneer de code geldig is.");
      if (vu <= this._localInput(Date.now()) + ":00") return this._message("Het einde ligt in het verleden.");
      if (vf && vu <= vf) return this._message("Het einde moet na het begin liggen.");
      try { localStorage.setItem("btxvto_quick_doors", JSON.stringify(sel)); } catch (err) { /* niet erg */ }
      const msg = { action: "add", name, doors: sel, valid_until: vu };
      if (vf) msg.valid_from = vf;
      if (st.once) msg.max_uses = 1;
      this._run(msg, "Tijdelijke code gemaakt", (res) => {
        const id = res && res.result && res.result.id;
        const ne = id && this._data.entries.find((x) => x.id === id);
        if (ne) this._openShare(ne, "Tijdelijke code gemaakt. Deel ze meteen:");
      });
    });
    $("cancel").addEventListener("click", () => this._closeForm());
    draw();
    p.classList.add("on");
    p.scrollIntoView({ block: "nearest" });
  }
  _tzOffset(localIso) {
    // verschil tussen de gegeven lokale tijd (tijdzone van Home Assistant) en UTC, in ms
    const t = Date.parse(localIso + "Z");
    const back = Date.parse(this._localInput(t) + ":00Z");
    return back - t;
  }
  _openShare(e, note) {
    const p = this.shadowRoot.getElementById("panel");
    this._message("");
    this._form = { type: "share", entry: e };
    p.innerHTML = `<div class="share"><h3>Code delen: ${esc(e.name)}</h3>
      ${note ? `<div class="row msg ok">${esc(note)}</div>` : ""}
      <div class="row" style="display:block"><textarea id="stext">${esc(this._shareText(e))}</textarea></div>
      <div class="row sendbar">
        <a class="btn primary" id="s_wa" target="_blank" rel="noopener">WhatsApp</a>
        <a class="btn" id="s_sms">Sms</a>
        <a class="btn" id="s_mail">Mail</a>
        <button class="btn" id="s_copy">Kopieer tekst</button>
        ${navigator.share ? `<button class="btn" id="s_native">Deelmenu</button>` : ""}
        <button class="btn" id="cancel">Sluiten</button></div>
      <div class="row muted">De tekst kun je hierboven nog aanpassen. WhatsApp, sms en mail openen met de tekst klaar; je kiest daar zelf de ontvanger.</div></div>`;
    const $ = (id) => p.querySelector("#" + id);
    const upd = () => {
      const t = encodeURIComponent($("stext").value);
      $("s_wa").href = `https://wa.me/?text=${t}`;
      $("s_sms").href = `sms:?&body=${t}`;
      $("s_mail").href = `mailto:?subject=${encodeURIComponent("Je toegangscode voor Trefpunt")}&body=${t}`;
    };
    $("stext").addEventListener("input", upd);
    upd();
    $("s_copy").addEventListener("click", async () => {
      const txt = $("stext").value;
      try { await navigator.clipboard.writeText(txt); }
      catch (err) { $("stext").select(); document.execCommand("copy"); }
      this._message("Tekst gekopieerd.", true);
    });
    if ($("s_native")) $("s_native").addEventListener("click", () => navigator.share({ text: $("stext").value }).catch(() => {}));
    $("cancel").addEventListener("click", () => this._closeForm());
    p.classList.add("on");
    p.scrollIntoView({ block: "nearest" });
  }
  _openForm(type, e, act, text) {
    if (!this._data) return this._message("De codes zijn nog niet geladen.");
    const p = this.shadowRoot.getElementById("panel");
    const doors = this._data.doors;
    this._message("");
    this._form = { type, entry: e };
    if (type === "confirm") {
      p.innerHTML = `<h3>Bevestigen</h3><div class="row">${text}</div>
        <div class="row"><button class="btn danger" id="ok">Bevestigen</button><button class="btn" id="cancel">Annuleren</button></div>`;
      p.querySelector("#ok").addEventListener("click", () => this._run({ action: act, entry: e.id }, act === "retire" ? "Uit dienst gehaald" : "Definitief verwijderd"));
    } else if (type === "block") {
      // standaard morgen om dit uur, in de tijdzone van Home Assistant (niet die van de browser)
      const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: (this._hass.config || {}).time_zone || undefined,
        year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
        .formatToParts(new Date(Date.now() + 86400000)).filter((p) => p.type !== "literal").map((p) => [p.type, p.value]));
      const def = `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
      p.innerHTML = `<h3>${e.status === "blocked" ? "Blokkering aanpassen" : "Blokkeren"}: ${esc(e.name)}</h3>
        <div class="row"><label><input type="radio" name="bm" value="until" checked> Tot</label><input id="until" type="datetime-local" value="${def}"></div>
        <div class="row"><label><input type="radio" name="bm" value="manual"> Tot ik deblokkeer</label></div>
        <div class="row muted">De ${e.kind === "badge" ? "badge" : "code"} wordt van alle toestellen gehaald en werkt meteen niet meer. Met een einddatum komt ze daarna vanzelf terug.</div>
        <div class="row"><button class="btn primary" id="ok">Blokkeren</button><button class="btn" id="cancel">Annuleren</button></div>`;
      p.querySelector("#ok").addEventListener("click", () => {
        const manual = p.querySelector("input[name=bm]:checked").value === "manual";
        const until = p.querySelector("#until").value;
        if (!manual && !until) return this._message("Kies een datum en uur.");
        // zonder tijdzone doorgeven: Home Assistant leest dit in zijn eigen tijdzone (die van de kaart), niet die van de browser
        this._run({ action: "block", entry: e.id, ...(manual ? {} : { until: until.length === 16 ? until + ":00" : until }) }, "Geblokkeerd");
      });
    } else if (type === "addbadge") {
      const unknown = (this._data.unknown_cards || []);
      p.innerHTML = `<h3>Nieuwe badge</h3>
        <div class="row"><label>Naam <input id="name" type="text"></label>
        <label>Badgenummer <input id="card" type="text" maxlength="16" placeholder="bv. 3CFC52F1" style="font-family:monospace"></label></div>
        ${unknown.length ? `<div class="row">Onlangs geweigerd aan een lezer: ${unknown.map((u) => `<button class="btn" data-card="${esc(u.card)}" title="${esc(u.doors.join(", "))}, ${u.count} keer">${esc(u.card)} <span class="muted">${esc(this._fmt.dateTime.format(new Date(u.last * 1000)))}</span></button>`).join("")}</div>`
          : `<div class="row muted">Tip: hou de nieuwe badge eerst voor een lezer. Ze wordt geweigerd, en verschijnt hier dan met haar nummer.</div>`}
        <div class="row">Deuren: ${doors.map((d) => `<label><input type="checkbox" value="${esc(d.id)}"> ${esc(d.name)}</label>`).join("")}</div>
        <div class="row muted">De badge krijgt dezelfde rechten als de bestaande badges en codes.</div>
        <div class="row"><button class="btn primary" id="ok">Opslaan</button><button class="btn" id="cancel">Annuleren</button></div>`;
      p.querySelectorAll("[data-card]").forEach((b) => b.addEventListener("click", () => { p.querySelector("#card").value = b.dataset.card; }));
      p.querySelector("#ok").addEventListener("click", () => {
        const name = p.querySelector("#name").value.trim();
        const card = p.querySelector("#card").value.trim().toUpperCase();
        const sel = [...p.querySelectorAll("input[type=checkbox]:checked")].map((x) => x.value);
        if (!name) return this._message("Geef een naam.");
        if (!/^[0-9A-F]{4,16}$/.test(card)) return this._message("Een badgenummer heeft 4 tot 16 tekens (cijfers en A tot F).");
        if (!sel.length) return this._message("Kies minstens een deur.");
        this._run({ action: "add_badge", name, card, doors: sel }, "Badge toegevoegd");
      });
    } else {
      const isNew = type === "add";
      const isBadge = e && e.kind === "badge";
      const active = isNew || e.status === "active";
      const on = new Set(isNew ? [] : e.doors.map((d) => d.id));
      const waiting = !isNew && this._waiting(e);
      const canValid = isNew || e.status !== "retired";
      p.innerHTML = `<h3>${isNew ? "Nieuwe code" : "Aanpassen: " + esc(e.name)}</h3>
        <div class="row"><label>Naam <input id="name" type="text" value="${isNew ? "" : esc(e.name)}"></label>
        ${isBadge ? "" : `<label>Code <input id="code" type="text" inputmode="numeric" pattern="[0-9]*" maxlength="8" placeholder="${isNew ? "6 tot 8 cijfers" : "ongewijzigd"}"></label>`}</div>
        ${!isBadge && active ? `<div class="row">Deuren: ${doors.map((d) => `<label><input type="checkbox" value="${esc(d.id)}" ${on.has(d.id) ? "checked" : ""}> ${esc(d.name)}</label>`).join("")}</div>` : ""}
        ${!isBadge && waiting ? `<div class="row">Deuren: ${e.stored.map((d) => `<span class="chip">${esc(d.name)}</span>`).join("")}</div>` : ""}
        ${canValid ? this._validityRows(e) : ""}
        ${!isNew && !active && !waiting ? `<div class="row muted">Deze ${isBadge ? "badge" : "code"} staat niet op de toestellen. De aanpassing wordt gebruikt bij deblokkeren of herstellen.</div>` : ""}
        <div class="row"><button class="btn primary" id="ok">Opslaan</button><button class="btn" id="cancel">Annuleren</button></div>`;
      p.querySelector("#ok").addEventListener("click", () => {
        const name = p.querySelector("#name").value.trim();
        const code = p.querySelector("#code") ? p.querySelector("#code").value.trim() : "";
        const sel = [...p.querySelectorAll("input[type=checkbox]:checked")].map((x) => x.value);
        if (!name) return this._message("Geef een naam.");
        if (code && !/^[0-9]{6,8}$/.test(code)) return this._message("Een code heeft 6 tot 8 cijfers.");
        const curVu = !isNew && e.valid_until ? this._localInput(Date.parse(e.valid_until)) + ":00" : "";
        const v = canValid ? this._readValidity(p, curVu) : { vf: "", vu: "" };
        if (v.error) return this._message(v.error);
        if (isNew) {
          if (!code) return this._message("Geef een code.");
          if (!sel.length) return this._message("Kies minstens een deur.");
          const add = { action: "add", name, code, doors: sel };
          if (v.vf) add.valid_from = v.vf;
          if (v.vu) add.valid_until = v.vu;
          return this._run(add, "Code toegevoegd", (res) => {
            const id = res && res.result && res.result.id;
            const ne = id && this._data.entries.find((x) => x.id === id);
            if (ne) this._openShare(ne, "Code toegevoegd. Deel ze meteen:");
          });
        }
        const steps = [];
        const cur = { vf: e.valid_from ? this._localInput(Date.parse(e.valid_from)) + ":00" : "", vu: e.valid_until ? this._localInput(Date.parse(e.valid_until)) + ":00" : "" };
        const vmsg = canValid && (v.vf !== cur.vf || v.vu !== cur.vu) ? { action: "validity", entry: e.id, valid_from: v.vf, valid_until: v.vu } : null;
        if (isBadge) {
          if (name !== e.name) steps.push({ action: "rename_badge", entry: e.id, name });
          if (vmsg) steps.push(vmsg);
          if (!steps.length) return this._message("Er is niets gewijzigd.");
          return this._run(steps, "Aangepast");
        }
        const msg = { action: "update", entry: e.id };
        if (name !== e.name) msg.name = name;
        if (code) msg.code = code;
        if (active) {
          if (!sel.length) return this._message("Kies minstens een deur. Wil je de code overal weg, gebruik dan Uit dienst.");
          const now = e.doors.map((d) => d.id).sort().join(",");
          if (sel.slice().sort().join(",") !== now) msg.doors = sel;
        }
        if (Object.keys(msg).length > 2) steps.push(msg);
        if (vmsg) steps.push(vmsg);
        if (!steps.length) return this._message("Er is niets gewijzigd.");
        this._run(steps, "Aangepast");
      });
    }
    p.querySelector("#cancel").addEventListener("click", () => this._closeForm());
    p.classList.add("on");
    p.scrollIntoView({ block: "nearest" });
  }
  _closeForm() {
    const p = this.shadowRoot.getElementById("panel");
    p.classList.remove("on");
    p.innerHTML = "";
    this._form = null;
  }
  async _run(msgs, okText, after) {
    if (this._busy) return;
    this._busy = true;
    this.shadowRoot.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    this._message("Bezig...", true);
    let res = null, ok = false;
    try {
      for (const msg of Array.isArray(msgs) ? msgs : [msgs]) {
        const data = { type: "btechnics_vto/manage/action", ...msg };
        Object.keys(data).forEach((k) => { if (data[k] === "") delete data[k]; });
        res = await this._ws(data);
      }
      this._closeForm();
      this._message(okText, true);
      ok = true;
    } catch (e) {
      this._message(`Niet gelukt: ${errText(e)}`);
    } finally {
      this._busy = false;
      await this._load();
      this.shadowRoot.querySelectorAll("button").forEach((b) => { b.disabled = false; });
    }
    if (ok && after) after(res);
  }
  getCardSize() {
    return 10;
  }
  static getStubConfig() {
    return {};
  }
}


/* ------------------------------------------------------------------ handleiding (gedeelde opbouw)
   Opbouw: zoeken, "Wat wil je doen?" als tegels met korte stappen, uitleg per scherm inklapbaar, begrippen. */
const HELP_CSS = `
  .hp { max-width: 980px; line-height: 1.5; }
  .hp-search { width: 100%; max-width: 420px; margin: 0 0 14px; }
  .hp h3 { font-size: 1.05rem; font-weight: 500; margin: 18px 0 10px; }
  .hp-tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; }
  .hp-tile { display: flex; gap: 10px; align-items: flex-start; text-align: left; font: inherit; color: var(--primary-text-color);
    background: var(--secondary-background-color); border: 1px solid transparent; border-radius: 10px; padding: 12px; cursor: pointer; }
  .hp-tile:hover, .hp-tile.on { border-color: var(--primary-color); }
  .hp-tile ha-icon { color: var(--primary-color); flex: none; --mdc-icon-size: 22px; }
  .hp-tile b { display: block; font-weight: 500; }
  .hp-tile span { font-size: 0.85rem; color: var(--secondary-text-color); }
  .hp-steps { border: 1px solid var(--primary-color); border-radius: 10px; padding: 12px 16px; margin: 12px 0 4px; }
  .hp-steps h4 { margin: 0 0 6px; font-size: 1rem; font-weight: 500; display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
  .hp-steps ol { margin: 4px 0 6px; padding-left: 22px; }
  .hp-steps li { margin: 3px 0; }
  .hp-steps .hp-tip { font-size: 0.85rem; color: var(--secondary-text-color); margin: 4px 0 0; }
  .hp details { border-bottom: 1px solid var(--divider-color); }
  .hp summary { cursor: pointer; padding: 10px 2px; list-style: none; display: flex; gap: 10px; align-items: baseline; }
  .hp summary::-webkit-details-marker { display: none; }
  .hp summary::before { content: "+"; width: 14px; color: var(--primary-color); font-weight: 600; flex: none; }
  .hp details[open] summary::before { content: "\\2212"; }
  .hp summary b { font-weight: 500; }
  .hp summary span { font-size: 0.85rem; color: var(--secondary-text-color); }
  .hp .body { padding: 0 2px 12px 24px; }
  .hp .body p { margin: 4px 0 8px; }
  .hp .body td:first-child, .hp-terms td:first-child { white-space: nowrap; font-weight: 500; width: 1%; }
  .hp .hidden { display: none; }
  .hp .none { color: var(--secondary-text-color); padding: 8px 0; }
  @media (max-width: 640px) { .hp .body td:first-child, .hp-terms td:first-child { white-space: normal; } .hp .body { padding-left: 6px; } }
`;
const hpTable = (rows) => `<div class="scroll"><table><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
function helpHtml(doc) {
  return `<div class="hp">
    <input class="hp-search" type="search" placeholder="Zoek in de handleiding, ${doc.hint || ""}" aria-label="Zoek in de handleiding">
    <h3>Wat wil je doen?</h3>
    <div class="hp-tiles">${doc.tasks.map((t, i) => `<button class="hp-tile" data-task="${i}"><ha-icon icon="${t.icon}"></ha-icon><div><b>${t.title}</b><span>${t.sub}</span></div></button>`).join("")}</div>
    <div class="hp-steps hidden"></div>
    <div class="none hidden">Niets gevonden.</div>
    <h3>Uitleg per scherm</h3>
    ${doc.topics.map((t) => `<details data-topic><summary><b>${t.title}</b><span>${t.sub}</span></summary><div class="body">${t.html}</div></details>`).join("")}
    <h3>Begrippen</h3>
    <div class="hp-terms">${hpTable(doc.terms)}</div>
    ${doc.foot ? `<p class="muted" style="font-size:0.8rem;margin-top:12px">${doc.foot}</p>` : ""}
  </div>`;
}
function wireHelp(root, doc, openTab) {
  const steps = root.querySelector(".hp-steps");
  const show = (i) => {
    const t = doc.tasks[i];
    root.querySelectorAll(".hp-tile").forEach((b) => b.classList.toggle("on", Number(b.dataset.task) === i));
    steps.innerHTML = `<h4><span>${t.title}</span>${t.tab ? `<button class="btn" data-open-tab="${t.tab}">Naar ${t.tabName || t.tab} &rsaquo;</button>` : ""}</h4>
      <ol>${t.steps.map((s) => `<li>${s}</li>`).join("")}</ol>${t.tip ? `<p class="hp-tip">${t.tip}</p>` : ""}`;
    steps.classList.remove("hidden");
    const ob = steps.querySelector("[data-open-tab]");
    if (ob) ob.addEventListener("click", () => openTab(ob.dataset.openTab));
    steps.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };
  root.querySelectorAll(".hp-tile").forEach((b) => b.addEventListener("click", () => show(Number(b.dataset.task))));
  const text = (html) => html.replace(/<[^>]+>/g, " ").toLowerCase();
  const input = root.querySelector(".hp-search");
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    let hits = 0;
    root.querySelectorAll(".hp-tile").forEach((b) => {
      const t = doc.tasks[Number(b.dataset.task)];
      const ok = !q || text(`${t.title} ${t.sub} ${t.steps.join(" ")} ${t.tip || ""}`).includes(q);
      b.classList.toggle("hidden", !ok); hits += ok;
    });
    root.querySelectorAll("details[data-topic]").forEach((d, i) => {
      const t = doc.topics[i];
      const ok = !q || text(`${t.title} ${t.sub} ${t.html}`).includes(q);
      d.classList.toggle("hidden", !ok); d.open = !!q && ok; hits += ok;
    });
    if (q) steps.classList.add("hidden");
    root.querySelector(".none").classList.toggle("hidden", hits > 0);
  });
}

const HELP_DOC = {
  hint: "bv. tijdelijke code of badge",
  tasks: [
    { icon: "mdi:timer-outline", title: "Tijdelijke code geven", sub: "Pakket, technicus of gast", tab: "codes", tabName: "Codes en badges",
      steps: ["Open <b>Codes en badges</b> en klik op <b>Tijdelijke code</b>.", "Kies <b>Pakket</b>, <b>Technicus</b>, <b>Gast</b> of <b>Andere</b>; pas de naam aan, bv. \"Pakket bol\".", "Vink de deur of deuren aan (je laatste keuze wordt onthouden).", "Kies hoe lang: 1 uur, Vandaag, 24 uur, 3 dagen, 1 week of Eigen.", "Laat <b>Eenmalig</b> aan voor een pakket: na de eerste opening vervalt de code.", "Klik op <b>Maak code en deel</b> en kies WhatsApp, Sms, Mail of Kopieer tekst."],
      tip: "Home Assistant kiest zelf een willekeurige, vrije code van 6 cijfers. Na het einde gaat ze vanzelf uit dienst." },
    { icon: "mdi:account-key", title: "Vaste code toevoegen", sub: "Voor een medewerker of vrijwilliger", tab: "codes", tabName: "Codes en badges",
      steps: ["Open <b>Codes en badges</b> en klik op <b>Nieuwe code</b>.", "Vul de naam en een code van 6 tot 8 cijfers in.", "Vink de deuren aan.", "Eventueel <b>Geldig vanaf</b> en <b>Geldig tot</b>; leeg = vanaf nu, zonder einde.", "Klik op <b>Opslaan</b> (10 tot 20 seconden) en deel de code in het venster dat opent."] },
    { icon: "mdi:share-variant", title: "Een code (opnieuw) delen", sub: "WhatsApp, sms, mail of kopieer", tab: "codes", tabName: "Codes en badges",
      steps: ["Zoek de persoon in <b>Codes en badges</b>.", "Klik op <b>Delen</b>.", "Pas de tekst eventueel aan en kies WhatsApp, Sms, Mail of Kopieer tekst. De ontvanger kies je daar zelf."],
      tip: "De tekst vermeldt de deur(en), de geldigheid en hoe je opent: # code #." },
    { icon: "mdi:lock-clock", title: "Iemand tijdelijk blokkeren", sub: "Tot een datum of tot je deblokkeert", tab: "codes", tabName: "Codes en badges",
      steps: ["Zoek de persoon en klik op <b>Blokkeren</b>.", "Kies <b>Tot</b> een datum en uur, of <b>Tot ik deblokkeer</b>.", "Klik op <b>Blokkeren</b>: de code of badge werkt meteen niet meer.", "Met een einddatum komt ze vanzelf terug; anders klik je later op <b>Deblokkeren</b>."] },
    { icon: "mdi:account-cancel", title: "Iemand de toegang afnemen", sub: "Uit dienst, later herstelbaar", tab: "codes", tabName: "Codes en badges",
      steps: ["Zoek de persoon en klik op <b>Uit dienst</b>.", "Bevestig. De code of badge wordt van alle deuren gehaald en bewaard.", "Terug nodig? Tab <b>Uit dienst</b>, klik op <b>Herstellen</b>. Definitief weg: <b>Definitief verwijderen</b>."] },
    { icon: "mdi:card-account-details-outline", title: "Nieuwe badge registreren", sub: "Badge voor de lezer houden en kiezen", tab: "codes", tabName: "Codes en badges",
      steps: ["Hou de nieuwe badge tegen een lezer. Ze wordt geweigerd; dat is de bedoeling.", "Klik op <b>Nieuwe badge</b>. Het nummer staat bij Onlangs geweigerd; klik erop.", "Vul de naam in, vink de deuren aan en klik op <b>Opslaan</b>."] },
    { icon: "mdi:door-open", title: "Deur openen op afstand", sub: "Vanop de pagina Overzicht", tab: "overzicht", tabName: "Overzicht",
      steps: ["Open <b>Overzicht</b>.", "Klik bij de juiste deur op <b>Deur openen</b>.", "De knop wordt rood: klik binnen 6 seconden nog eens.", "\"... is geopend.\" verschijnt. In de historiek staat Op afstand met je naam."],
      tip: "Enkel voor beheerders. Elke opening op afstand staat bij Wijzigingen." },
    { icon: "mdi:history", title: "Nagaan wie binnenkwam", sub: "Zoeken, per persoon, CSV", tab: "historiek", tabName: "Historiek",
      steps: ["Open <b>Historiek</b>.", "Zoek op naam of kies een deur, geopend of geweigerd, en een periode.", "Klik op <b>Per persoon</b> voor een overzicht per persoon; klik op een naam om te filteren.", "Camera-icoon = foto van die toegang. <b>CSV</b> geeft alles voor Excel."] },
  ],
  topics: [
    { title: "Overzicht", sub: "Status per deur, laatste toegang, deur openen", html: hpTable([["Kaart per deur", "Online of Offline, laatste toegang (wie, hoe, wanneer) met foto, aantallen van vandaag, codes en badges"], ["Deur openen", "Enkel beheerders, met bevestiging"], ["Recente toegangen", "De laatste 6 van die deur"]]) },
    { title: "Historiek", sub: "Toegangen van het laatste jaar", html: `<p>Filters: zoeken, deur, status, periode (vandaag tot 1 jaar of eigen). Weergave Toegangen of Per persoon. CSV voor Excel.</p>` + hpTable([["Binnenpost 9901, 9902, 9903", "Geopend via die binnenpost"], ["Op afstand", "Geopend met Deur openen; bij Hoe staat wie"], ["Foute code", "Een code die niet bestaat of niet geldig is voor die deur"], ["Onbekende badge", "Een badge die niet gekend is"], ["Ongeldige invoer", "Onvolledige invoer op het klavier"], ["Exitknop", "Geopend met de knop binnen"]]) },
    { title: "Codes en badges", sub: "Statussen en knoppen", html: hpTable([["Actief", "Staat op de toestellen en werkt"], ["Wacht op begin", "Geldigheid begint later; komt er vanzelf op (of Nu al activeren)"], ["Geblokkeerd", "Tijdelijk van de toestellen, tot een tijdstip of tot deblokkeren"], ["Uit dienst", "Van de toestellen, bewaard en herstelbaar"], ["Eenmalig", "Vervalt na de eerste opening"], ["Wijzigingen", "Onderaan: wie wat wanneer deed, ook de planner en openen op afstand"]]) },
    { title: "Aan de deur", sub: "Hoe open je", html: hpTable([["Code", "Typ <b># code #</b> op het klavier"], ["Badge", "Hou de badge tegen de lezer"], ["Binnenpost", "Opentoets op de binnenpost"]]) },
    { title: "Foto's", sub: "Bij elke toegang, 30 dagen", html: `<p>Bij elke toegang neemt Home Assistant een foto met de camera van de buitenpost. Enkel beheerders zien ze. Na 30 dagen worden ze gewist: de Belgische camerawet laat camerabeelden maximaal een maand bewaren, tenzij als bewijs nodig. Kammerstraat maakt nog geen foto's (camera moet ter plaatse aangezet worden).</p>` },
    { title: "Goed om te weten", sub: "Snelheid en veiligheid", html: hpTable([["Snelheid", "Een actie op een toestel duurt 10 tot 20 seconden"], ["Veiligheid", "Voor elke wijziging wordt het toestel nagekeken; klopt iets niet, dan gebeurt er niets"], ["Niets verloren", "Blokkeren en uit dienst bewaren eerst een kopie"], ["Archief", "Het toestel onthoudt 1000 toegangen; Home Assistant bewaart ze 400 dagen"]]) },
  ],
  terms: [
    ["Code", "6 tot 8 cijfers, openen met # code #"], ["Badge", "Kaart of sleutelhanger voor de lezer"],
    ["Binnenpost", "Toestel binnen (9901, 9902, 9903) waarmee iemand de deur opent"], ["Geldigheid", "Van en tot wanneer een code werkt"],
    ["Uit dienst", "Niet meer op de deuren, maar bewaard en herstelbaar"], ["Planner", "Home Assistant zelf: zet codes erop en eraf op het juiste moment"],
  ],
  foot: "Bron bewaartermijn camerabeelden: besafe.be.",
};

class VtoHandleiding extends VtoBase {
  _init() {
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}${HELP_CSS}</style>
    <ha-card><div class="title">${esc(this._config.title || "Handleiding toegangscontrole")}</div><div id="hb">${helpHtml(HELP_DOC)}</div></ha-card>`;
    wireHelp(this.shadowRoot.getElementById("hb"), HELP_DOC, (view) => {
      // naar een andere pagina van hetzelfde dashboard
      const base = location.pathname.split("/").slice(0, 2).join("/");
      history.pushState(null, "", `${base}/${view}`);
      window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
    });
  }
  getCardSize() { return 12; }
  static getStubConfig() { return {}; }
}

// Home Assistant laadt deze module heel vroeg en installeert daarna een eigen custom element
// registry. Een kaart die we te vroeg registreren is daar niet zichtbaar ("Custom element doesn't
// exist"). Daarom registreren we opnieuw zolang het nodig is, telkens via window.customElements
// (het register dat NU actief is) en met een nieuwe subklasse (een constructor mag maar een keer).
const CARD_CLASSES = [["btechnics-vto-overzicht", VtoOverzicht], ["btechnics-vto-toegang", VtoToegang], ["btechnics-vto-codes", VtoCodes], ["btechnics-vto-handleiding", VtoHandleiding]];
const CARDS_VERSION = "0.8.1";
const define = (name, cls) => {
  if (window.customElements.get(name)) return;
  try {
    const R = class extends cls {};
    R.__btxVto = CARDS_VERSION;
    window.customElements.define(name, R);
  } catch (e) { /* volgende poging */ }
};
// Een oude kopie van de pagina (service worker) kan eerst een oudere versie van dit script laden. Een custom
// element kan niet opnieuw gedefinieerd worden; daarom de al geregistreerde klasse op deze code laten steunen
// en bestaande kaarten opnieuw opbouwen (vastgesteld 26/09/2026: v0.3.8 uit de cache, v0.7.0 via de resource).
const reinit = (tag) => {
  const walk = (root) => {
    for (const el of root.querySelectorAll("*")) {
      if (el.tagName === tag && el._hass) {
        clearInterval(el._timer);
        el._timer = null;
        try { el._init(); } catch (e) { /* volgende kaart */ }
      }
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
};
const upgrade = (name, cls) => {
  const R = window.customElements.get(name);
  if (!R || R.__btxVto === CARDS_VERSION || R.prototype instanceof cls) return;
  // enkel naar een nieuwere versie (twee scriptversies mogen elkaar niet om beurten vervangen)
  const v = (x) => String(x || "0").split(".").map(Number);
  const [a, b] = [v(CARDS_VERSION), v(R.__btxVto)];
  const newer = a.findIndex((n, i) => n !== (b[i] || 0));
  if (R.__btxVto && (newer < 0 || a[newer] < (b[newer] || 0))) return;
  try {
    Object.setPrototypeOf(R.prototype, cls.prototype);
    Object.setPrototypeOf(R, cls);
    R.__btxVto = CARDS_VERSION;
    reinit(name.toUpperCase());
  } catch (e) { /* laat de oude versie staan */ }
};
const registerAll = () => { for (const [name, cls] of CARD_CLASSES) { define(name, cls); upgrade(name, cls); } };
registerAll();
let registerTries = 0;
const registerTimer = setInterval(() => { registerAll(); if (++registerTries >= 120) clearInterval(registerTimer); }, 500);
window.customCards = window.customCards || [];
for (const [type, name, description] of [
  ["btechnics-vto-overzicht", "Btechnics VTO deuren", "Status van alle VTO-deuren, nieuwe deuren verschijnen automatisch"],
  ["btechnics-vto-toegang", "Btechnics VTO toegangshistoriek", "Zoeken en filteren in een jaar toegangen, per persoon en per maand"],
  ["btechnics-vto-codes", "Btechnics VTO codes", "Codes en badges per persoon over alle deuren"],
  ["btechnics-vto-handleiding", "Btechnics VTO handleiding", "Uitleg bij de toegangscontrole"],
]) {
  if (!window.customCards.some((c) => c.type === type)) window.customCards.push({ type, name, description, preview: false });
}
