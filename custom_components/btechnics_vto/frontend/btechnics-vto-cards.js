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

function formatters(tz) {
  const o = { timeZone: tz || undefined };
  return {
    dateTime: new Intl.DateTimeFormat("nl-BE", { ...o, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }),
    date: new Intl.DateTimeFormat("nl-BE", { ...o, weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" }),
    time: new Intl.DateTimeFormat("nl-BE", { ...o, hour: "2-digit", minute: "2-digit", second: "2-digit" }),
    short: new Intl.DateTimeFormat("nl-BE", { ...o, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }),
  };
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
`;

class VtoBase extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
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
      .last { display: flex; gap: 12px; align-items: center; }
      .icon { width: 44px; height: 44px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex: none; }
      .icon.open { background: rgba(2, 136, 209, 0.15); color: ${C_OPEN}; }
      .icon.refused { background: rgba(219, 68, 55, 0.15); color: ${C_REFUSED}; }
      .icon.none { background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .who { font-size: 1.2rem; font-weight: 500; }
      .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
      .stat { background: var(--secondary-background-color); border-radius: 8px; padding: 8px; }
      .stat .v { font-size: 1.2rem; font-weight: 500; font-variant-numeric: tabular-nums; }
      .stat .l { font-size: 0.8rem; color: var(--secondary-text-color); }
      .recent td { padding: 6px 4px; font-size: 0.9rem; }
      .foot { font-size: 0.8rem; color: var(--secondary-text-color); margin-top: 12px; }
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
      this._data = await this._ws({ type: "btechnics_vto/doors" });
      this._sig = this._data.doors.map((d) => { const s = this._hass.states[d.entities.last_unlock]; return s ? s.last_updated : ""; }).join("|");
      this._render();
    } catch (e) {
      this.shadowRoot.getElementById("body").innerHTML = `<div class="error">Kon de deuren niet laden: ${esc(e.message || e)}</div>`;
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
    const lastTxt = u ? `${u.opened ? "geopend" : "geweigerd"} via ${esc(u.method)}, ${f.dateTime.format(new Date(u.ts * 1000))}` : "";
    const sub = !d.available
      ? (u ? `Laatst gekend: ${esc(u.name === "?" ? "onbekende code" : u.name)}, ${lastTxt}` : "Geen verbinding met het toestel")
      : (u ? lastTxt.charAt(0).toUpperCase() + lastTxt.slice(1) : "");
    const recent = (d.recent || []).slice(0, 6).map((r) => `<tr>
        <td class="muted" style="white-space:nowrap">${f.short.format(new Date(r.ts * 1000))}</td>
        <td>${r.name === "?" ? '<span class="muted">onbekende code</span>' : esc(r.name)}</td>
        <td class="muted">${esc(r.method)}</td>
        <td><span class="status"><span class="dot ${r.opened ? "open" : "refused"}"></span>${r.opened ? "Geopend" : "Geweigerd"}</span></td></tr>`).join("");
    return `<div class="door">
      <div class="head"><span class="name">${esc(d.name)}</span>
        <span class="chip ${d.available ? "" : "off"}">${d.available ? "Online" : "Offline"}</span></div>
      <div class="last"><div class="icon ${cls}"><ha-icon icon="${icon}"></ha-icon></div>
        <div><div class="who">${esc(who)}</div><div class="muted">${sub}</div></div></div>
      <div class="stats">
        <div class="stat"><div class="v">${d.today.opened}</div><div class="l">Vandaag geopend</div></div>
        <div class="stat"><div class="v">${d.today.refused}</div><div class="l">Vandaag geweigerd</div></div>
        <div class="stat"><div class="v">${d.codes}</div><div class="l">Codes</div></div>
        <div class="stat"><div class="v">${d.cards}</div><div class="l">Badges</div></div>
      </div>
      ${recent ? `<table class="recent">${recent}</table>` : '<div class="empty">Geen recente toegangen</div>'}
    </div>`;
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
    try {
      const res = await this._ws({ type: "btechnics_vto/history", ...this._filters(), limit: PAGE, offset: 0 });
      if (seq !== this._seq) return; // er kwam intussen een nieuwere zoekopdracht
      this._res = res;
      if (!this._state.search && this._state.person === null) {
        this._names = res.people.map((p) => p.name).filter((n) => n !== "?");
        this.shadowRoot.getElementById("names").innerHTML = this._names.map((n) => `<option value="${esc(n)}">`).join("");
      }
      this._render();
    } catch (e) {
      if (seq !== this._seq) return;
      if (e && e.code === "unauthorized") {
        this.shadowRoot.getElementById("out").innerHTML = `<div class="error">Enkel beheerders kunnen de toegangshistoriek bekijken.</div>`;
        return;
      }
      this.shadowRoot.getElementById("out").innerHTML = `<div class="error">Kon de historiek niet laden: ${esc(e.message || e)}. Nieuwe poging binnen 10 seconden.</div>`;
      this._retry = setTimeout(() => this.isConnected && this._reload(), 10000);
    }
  }
  async _loadMore() {
    if (this._busy || !this._res) return;
    this._busy = true;
    const seq = this._seq;
    const btn = this.shadowRoot.getElementById("morebtn");
    if (btn) btn.disabled = true;
    try {
      // zelfde momentopname (max_id): geen dubbele of verschoven rijen als er intussen toegangen bijkomen
      const res = await this._ws({ type: "btechnics_vto/history", ...this._filters(), limit: PAGE, offset: this._res.rows.length, max_id: this._res.max_id });
      if (seq !== this._seq) return; // filters gewijzigd terwijl we laadden
      this._res.rows = this._res.rows.concat(res.rows);
      this._renderOut();
    } catch (e) {
      if (btn) btn.disabled = false;
      this.shadowRoot.getElementById("more").insertAdjacentHTML("beforeend", `<div class="error">Laden mislukt: ${esc(e.message || e)}</div>`);
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
      <div class="kpi"><div class="v">${r.people.filter((p) => p.name !== "?").length.toLocaleString("nl-BE")}</div><div class="l">Personen</div></div>`;
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
    const grid = [0, nice / 2, nice].map((v) => `<line class="grid" x1="${left}" x2="${W - right}" y1="${yv(v)}" y2="${yv(v)}"></line>
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
          <td><button class="link" data-person="${esc(p.name)}">${p.name === "?" ? "onbekende code" : esc(p.name)}</button></td>
          <td class="num">${p.count}</td><td class="num">${p.opened}</td><td class="num">${p.refused}</td>
          <td class="when">${f.dateTime.format(new Date(p.last * 1000))}</td><td class="muted">${p.doors.map(esc).join(", ")}</td></tr>`).join("")}
        </tbody></table>`;
      return;
    }
    if (!r.rows.length) { out.innerHTML = `<div class="empty">Geen toegangen gevonden</div>`; return; }
    out.innerHTML = `<table><thead><tr><th>Datum</th><th>Tijd</th><th>Deur</th><th>Persoon</th><th>Methode</th><th>Status</th></tr></thead><tbody>
      ${r.rows.map((x) => { const d = new Date(x.ts * 1000); return `<tr>
        <td class="when">${f.date.format(d)}</td><td class="when">${f.time.format(d)}</td><td>${esc(x.door)}</td>
        <td>${x.name === "?" ? '<button class="link muted" data-person="?">onbekende code</button>' : `<button class="link" data-person="${esc(x.name)}">${esc(x.name)}</button>`}</td>
        <td class="muted">${esc(x.method)}${x.card ? ` <span class="mono">${esc(x.card)}</span>` : ""}</td>
        <td><span class="status"><span class="dot ${x.opened ? "open" : "refused"}"></span>${x.opened ? "Geopend" : "Geweigerd"}</span></td></tr>`; }).join("")}
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
      btn.disabled = false;
    }
  }
  async _csvExport() {
    // in blokken ophalen (geen bovengrens), allemaal uit dezelfde momentopname
    const CHUNK = 20000;
    let rows = [], maxId;
    for (;;) {
      const res = await this._ws({ type: "btechnics_vto/history", ...this._filters(), limit: CHUNK, offset: rows.length, ...(maxId ? { max_id: maxId } : {}) });
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
      lines.push([f.date.format(d), f.time.format(d), x.door, x.name === "?" ? "onbekende code" : x.name, x.method, x.card, x.opened ? "Geopend" : "Geweigerd"].map(q).join(";"));
    }
    const blob = new Blob([String.fromCharCode(0xfeff) + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `toegangen-${new Date().toISOString().slice(0, 10)}.csv`;
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
        <button id="new" class="btn primary"><ha-icon icon="mdi:plus" style="--mdc-icon-size:18px"></ha-icon> Nieuwe code</button>
      </div>
      <div class="tabs" id="tabs"></div>
      <div id="panel" class="panel"></div>
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
    this.shadowRoot.addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]");
      if (!b) return;
      const entry = this._data && this._data.entries.find((x) => x.id === b.dataset.id);
      this._action(b.dataset.act, entry);
    });
    this._load();
  }
  async _load() {
    try {
      this._data = await this._ws({ type: "btechnics_vto/manage/list" });
      this._render();
    } catch (e) {
      const msg = e && e.code === "unauthorized" ? "Enkel beheerders kunnen de codes bekijken." : `Kon de codes niet laden: ${esc(e.message || e)}`;
      this.shadowRoot.getElementById("out").innerHTML = `<div class="error">${msg}</div>`;
    }
  }
  _message(text, ok) {
    const el = this.shadowRoot.getElementById("msg");
    el.className = "msg " + (ok ? "ok" : "error");
    el.textContent = text || "";
  }
  _statusText(e) {
    if (e.status === "active") return "Actief";
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
      if (e.status === "active") return b("edit", "Aanpassen") + b("block", "Blokkeren") + b("retire", "Uit dienst", "danger");
      if (e.status === "blocked") return b("unblock", "Deblokkeren", "primary") + b("block", "Einde aanpassen") + b("edit", "Aanpassen") + b("retire", "Uit dienst", "danger");
      return b("restore", "Herstellen", "primary") + b("edit", "Aanpassen") + b("forget", "Definitief verwijderen", "danger");
    };
    const dot = (e) => `<span class="dot ${e.status === "active" ? "open" : e.status}"></span>`;
    if (!list.length) {
      $("out").innerHTML = `<div class="empty">Niets gevonden</div>`;
    } else {
      $("out").innerHTML = `<table class="list"><thead><tr><th>Persoon</th><th>Soort</th><th>Code of badge</th><th>Deuren</th><th>Status</th><th></th></tr></thead><tbody>
        ${list.map((e) => `<tr>
          <td><span class="pname">${esc(e.name || "?")}</span></td>
          <td class="muted kind">${e.kind === "badge" ? "badge" : "code"}</td>
          <td>${sec(e)}</td>
          <td>${e.doors.map((d) => `<span class="chip">${esc(d.name)}</span>`).join("")}${e.stored.map((d) => `<span class="chip stored" title="bewaard, niet op het toestel">${esc(d.name)}</span>`).join("")}</td>
          <td><span class="status">${dot(e)}${esc(this._statusText(e))}</span>
            ${e.status !== "active" && e.doors.length ? `<div class="warn">Nog actief op ${e.doors.map((d) => esc(d.name)).join(", ")}</div>` : ""}</td>
          <td><div class="acts">${acts(e)}</div></td></tr>`).join("")}
        </tbody></table>`;
    }
    const shown = this._auditAll ? audit : audit.slice(0, 15);
    $("audit").innerHTML = audit.length ? `<table class="audit"><thead><tr><th>Wanneer</th><th>Wie</th><th>Actie</th><th>Persoon</th><th>Deuren</th><th>Details</th></tr></thead><tbody>
      ${shown.map((a) => `<tr><td class="when">${this._fmt.dateTime.format(new Date(a.ts))}</td><td>${esc(a.user)}</td><td>${esc(a.action)}</td>
        <td>${esc(a.name)} <span class="muted">(${a.kind === "badge" ? "badge" : "code"})</span></td><td class="muted">${esc((a.doors || []).join(", "))}</td>
        <td class="muted">${esc(a.detail || "")}</td></tr>`).join("")}</tbody></table>
      ${audit.length > 15 ? `<div class="more"><button class="btn" id="auditmore">${this._auditAll ? "Minder tonen" : `Alles tonen (${audit.length})`}</button></div>` : ""}`
      : `<div class="muted">Nog geen wijzigingen.</div>`;
    const am = $("auditmore");
    if (am) am.addEventListener("click", () => { this._auditAll = !this._auditAll; this._render(); });
  }
  _action(act, e) {
    if (act === "edit" || act === "block") return this._openForm(act, e);
    if (act === "retire") return this._openForm("confirm", e, "retire",
      `'${esc(e.name)}' uit dienst halen? De ${e.kind === "badge" ? "badge" : "code"} wordt van alle toestellen gehaald en bewaard, zodat je ze later kan herstellen.`);
    if (act === "forget") return this._openForm("confirm", e, "forget",
      `'${esc(e.name)}' definitief uit de lijst verwijderen? Dit kan niet ongedaan gemaakt worden. De toegangshistoriek blijft bewaard.`);
    return this._run({ action: act, entry: e.id }, act === "unblock" ? "Gedeblokkeerd" : "Hersteld");
  }
  _openForm(type, e, act, text) {
    const p = this.shadowRoot.getElementById("panel");
    const doors = this._data.doors;
    this._message("");
    if (type === "confirm") {
      p.innerHTML = `<h3>Bevestigen</h3><div class="row">${text}</div>
        <div class="row"><button class="btn danger" id="ok">Bevestigen</button><button class="btn" id="cancel">Annuleren</button></div>`;
      p.querySelector("#ok").addEventListener("click", () => this._run({ action: act, entry: e.id }, act === "retire" ? "Uit dienst gehaald" : "Definitief verwijderd"));
    } else if (type === "block") {
      const d = new Date(Date.now() + 86400000);
      const pad = (n) => String(n).padStart(2, "0");
      const def = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
      p.innerHTML = `<h3>${e.status === "blocked" ? "Blokkering aanpassen" : "Blokkeren"}: ${esc(e.name)}</h3>
        <div class="row"><label><input type="radio" name="bm" value="until" checked> Tot</label><input id="until" type="datetime-local" value="${def}"></div>
        <div class="row"><label><input type="radio" name="bm" value="manual"> Tot ik deblokkeer</label></div>
        <div class="row muted">De ${e.kind === "badge" ? "badge" : "code"} wordt van alle toestellen gehaald en werkt meteen niet meer. Met een einddatum komt ze daarna vanzelf terug.</div>
        <div class="row"><button class="btn primary" id="ok">Blokkeren</button><button class="btn" id="cancel">Annuleren</button></div>`;
      p.querySelector("#ok").addEventListener("click", () => {
        const manual = p.querySelector("input[name=bm]:checked").value === "manual";
        const until = p.querySelector("#until").value;
        if (!manual && !until) return this._message("Kies een datum en uur.");
        const when = new Date(until);
        if (!manual && !(when > new Date())) return this._message("Het einde moet in de toekomst liggen.");
        this._run({ action: "block", entry: e.id, ...(manual ? {} : { until: when.toISOString() }) }, "Geblokkeerd");
      });
    } else {
      const isNew = type === "add";
      const isBadge = e && e.kind === "badge";
      const active = isNew || e.status === "active";
      const on = new Set(isNew ? [] : e.doors.map((d) => d.id));
      p.innerHTML = `<h3>${isNew ? "Nieuwe code" : "Aanpassen: " + esc(e.name)}</h3>
        <div class="row"><label>Naam <input id="name" type="text" value="${isNew ? "" : esc(e.name)}"></label>
        ${isBadge ? "" : `<label>Code <input id="code" type="text" inputmode="numeric" pattern="[0-9]*" maxlength="8" placeholder="${isNew ? "4 tot 8 cijfers" : "ongewijzigd"}"></label>`}</div>
        ${!isBadge && active ? `<div class="row">Deuren: ${doors.map((d) => `<label><input type="checkbox" value="${esc(d.id)}" ${on.has(d.id) ? "checked" : ""}> ${esc(d.name)}</label>`).join("")}</div>` : ""}
        ${!isNew && !active ? `<div class="row muted">Deze ${isBadge ? "badge" : "code"} staat niet op de toestellen. De aanpassing wordt gebruikt bij deblokkeren of herstellen.</div>` : ""}
        <div class="row"><button class="btn primary" id="ok">Opslaan</button><button class="btn" id="cancel">Annuleren</button></div>`;
      p.querySelector("#ok").addEventListener("click", () => {
        const name = p.querySelector("#name").value.trim();
        const code = p.querySelector("#code") ? p.querySelector("#code").value.trim() : "";
        const sel = [...p.querySelectorAll("input[type=checkbox]:checked")].map((x) => x.value);
        if (!name) return this._message("Geef een naam.");
        if (code && !/^[0-9]{4,8}$/.test(code)) return this._message("Een code heeft 4 tot 8 cijfers.");
        if (isNew) {
          if (!code) return this._message("Geef een code.");
          if (!sel.length) return this._message("Kies minstens een deur.");
          return this._run({ action: "add", name, code, doors: sel }, "Code toegevoegd");
        }
        if (isBadge) return this._run({ action: "rename_badge", entry: e.id, name }, "Aangepast");
        const msg = { action: "update", entry: e.id };
        if (name !== e.name) msg.name = name;
        if (code) msg.code = code;
        if (active) {
          if (!sel.length) return this._message("Kies minstens een deur. Wil je de code overal weg, gebruik dan Uit dienst.");
          const now = e.doors.map((d) => d.id).sort().join(",");
          if (sel.slice().sort().join(",") !== now) msg.doors = sel;
        }
        if (Object.keys(msg).length === 2) return this._message("Er is niets gewijzigd.");
        this._run(msg, "Aangepast");
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
  }
  async _run(msg, okText) {
    if (this._busy) return;
    this._busy = true;
    this.shadowRoot.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    this._message("Bezig...", true);
    try {
      await this._ws({ type: "btechnics_vto/manage/action", ...msg });
      this._closeForm();
      this._message(okText, true);
    } catch (e) {
      this._message(`Niet gelukt: ${e.message || e}`);
    } finally {
      this._busy = false;
      await this._load();
      this.shadowRoot.querySelectorAll("button").forEach((b) => { b.disabled = false; });
    }
  }
  getCardSize() {
    return 10;
  }
  static getStubConfig() {
    return {};
  }
}

// Home Assistant laadt deze module heel vroeg en installeert daarna een eigen custom element
// registry. Een kaart die we te vroeg registreren is daar niet zichtbaar ("Custom element doesn't
// exist"). Daarom registreren we opnieuw zolang het nodig is, telkens via window.customElements
// (het register dat NU actief is) en met een nieuwe subklasse (een constructor mag maar een keer).
const CARD_CLASSES = [["btechnics-vto-overzicht", VtoOverzicht], ["btechnics-vto-toegang", VtoToegang], ["btechnics-vto-codes", VtoCodes]];
const define = (name, cls) => {
  if (window.customElements.get(name)) return;
  try { window.customElements.define(name, class extends cls {}); } catch (e) { /* volgende poging */ }
};
const registerAll = () => { for (const [name, cls] of CARD_CLASSES) define(name, cls); };
registerAll();
let registerTries = 0;
const registerTimer = setInterval(() => { registerAll(); if (++registerTries >= 120) clearInterval(registerTimer); }, 500);
window.customCards = window.customCards || [];
for (const [type, name, description] of [
  ["btechnics-vto-overzicht", "Btechnics VTO deuren", "Status van alle VTO-deuren, nieuwe deuren verschijnen automatisch"],
  ["btechnics-vto-toegang", "Btechnics VTO toegangshistoriek", "Zoeken en filteren in een jaar toegangen, per persoon en per maand"],
  ["btechnics-vto-codes", "Btechnics VTO codes", "Codes en badges per persoon over alle deuren"],
]) {
  if (!window.customCards.some((c) => c.type === type)) window.customCards.push({ type, name, description, preview: false });
}
