import fs from 'fs';
const src = fs.readFileSync(new URL('../../custom_components', import.meta.url).pathname + '/btechnics_vto/frontend/btechnics-vto-cards.js', 'utf8');
const grab = (naam) => { const i = src.indexOf('  ' + naam + '('); let d = 0, j = src.indexOf('{', i); for (let k = j; k < src.length; k++) { if (src[k] === '{') d++; if (src[k] === '}') { d--; if (!d) return src.slice(i, k + 1); } } };
const cls = `class X { constructor(tz){ this._hass = { config: { time_zone: tz } }; }\n${grab('_localInput')}\n${grab('_localToMs')}\n${grab('_tzOffset')}\n}; X`;
const X = eval(cls);
let ok = 0, nok = 0; const check = (c, t, x='') => { c ? ok++ : nok++; console.log((c ? 'OK  ' : 'NOK ') + t + ' ' + x); };
const x = new X('Europe/Brussels');
const iso = (ms) => new Date(ms).toISOString().slice(0, 16);
const gev = [
  ['2026-10-01T08:00', '2026-10-01T06:00', 'zomer: 08:00 Brussel = 06:00 UTC'],
  ['2026-12-01T08:00', '2026-12-01T07:00', 'winter: 08:00 Brussel = 07:00 UTC'],
  ['2026-10-25T01:30', '2026-10-24T23:30', 'dag van de wintertijd, voor de wissel'],
  ['2026-10-25T02:30', '2026-10-25T00:30', 'dubbel uur 02:30: het eerste (zomertijd), zoals HA fold=0'],
  ['2026-10-25T03:30', '2026-10-25T02:30', 'na de wissel naar wintertijd'],
  ['2026-03-29T01:30', '2026-03-29T00:30', 'dag van de zomertijd, voor de wissel'],
  ['2026-03-29T03:30', '2026-03-29T01:30', 'na de wissel naar zomertijd'],
  ['2026-10-01T23:59', '2026-10-01T21:59', 'einde van de dag'],
  ['2026-03-29T02:30', '2026-03-29T01:30', 'uur dat niet bestaat: 02:30 wordt 03:30 zomertijd, zoals HA fold=0'],
];
for (const [l, u, t] of gev) check(iso(x._localToMs(l)) === u, t, `${l} -> ${iso(x._localToMs(l))}`);
for (const [l] of gev) { const ms = x._localToMs(l); check(x._localInput(ms) === l || l === '2026-03-29T02:30', 'heen en terug ' + l, x._localInput(ms)); }
check(x._tzOffset('2026-10-01T08:00') === 7200000, 'offset zomer 2 uur');
check(x._tzOffset('2026-12-01T08:00') === 3600000, 'offset winter 1 uur');
// andere tijdzone van de browser mag niets veranderen
process.env.TZ = 'America/New_York';
check(iso(new X('Europe/Brussels')._localToMs('2026-10-01T08:00')) === '2026-10-01T06:00', 'browser in New York, HA in Brussel');
console.log(`RESULTAAT ${ok} ok, ${nok} fout`);
