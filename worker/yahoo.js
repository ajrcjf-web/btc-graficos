// Cloudflare Worker: intermediario gratuito entre la web y Yahoo Finance.
// Yahoo no deja que una web le pida datos directamente (CORS); este Worker los pide por ella,
// los guarda en caché unos segundos u horas y los devuelve con permiso CORS solo para la web.
//
//   /chart?symbol=AAPL&interval=1d&range=max     velas (intervalos 1d, 1wk, 60m, 5m)
//   /search?q=apple                              buscador de acciones, ETF e índices
//
// Despliegue: Cloudflare → Workers & Pages → Create → Worker → pegar este archivo → Deploy.

const ALLOWED_ORIGINS = ['https://ajrcjf-web.github.io', 'http://localhost', 'http://127.0.0.1'];
const Y = 'https://query1.finance.yahoo.com';
const UA = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36', 'Accept': 'application/json' };
const RANGES = { '1d': ['5d', '1mo', '1y', '5y', 'max'], '1wk': ['max', '10y'], '60m': ['1d', '5d', '1mo', '730d'], '5m': ['1d', '5d'] };

function cors(origin) {
  const ok = ALLOWED_ORIGINS.some(o => origin === o || origin.startsWith(o + ':'));
  return ok ? { 'Access-Control-Allow-Origin': origin, 'Vary': 'Origin' } : null;
}
function reply(body, status, headers) {
  return new Response(body, { status, headers: { 'Content-Type': 'application/json; charset=utf-8', ...headers } });
}

export default {
  async fetch(request, env, ctx) {
    const origin = request.headers.get('Origin') || '';
    const ch = cors(origin);
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: { ...(ch || {}), 'Access-Control-Allow-Methods': 'GET', 'Access-Control-Max-Age': '86400' } });
    if (!ch) return reply('{"error":"origin not allowed"}', 403, {});
    if (request.method !== 'GET') return reply('{"error":"method"}', 405, ch);

    const url = new URL(request.url);
    let upstream, ttl;
    if (url.pathname === '/chart') {
      const sym = (url.searchParams.get('symbol') || '').toUpperCase();
      const iv = url.searchParams.get('interval') || '1d';
      const rng = url.searchParams.get('range') || '1y';
      if (!/^[A-Z0-9.^=\-]{1,15}$/.test(sym) || !RANGES[iv] || !RANGES[iv].includes(rng)) return reply('{"error":"bad request"}', 400, ch);
      // con range=max Yahoo agrupa las velas por meses: para el histórico diario completo se pide por fechas
      const span = rng === 'max' ? 'period1=0&period2=9999999999' : `range=${rng}`;
      upstream = `${Y}/v8/finance/chart/${encodeURIComponent(sym)}?interval=${iv}&${span}&includeAdjustedClose=false`;
      // velas recientes: 60 s; histórico largo: 6 h
      ttl = (rng === 'max' || rng === '10y' || rng === '5y' || rng === '730d') ? 21600 : 60;
    } else if (url.pathname === '/search') {
      const q = (url.searchParams.get('q') || '').trim().slice(0, 40);
      if (!q) return reply('{"quotes":[]}', 200, ch);
      upstream = `${Y}/v1/finance/search?q=${encodeURIComponent(q)}&quotesCount=12&newsCount=0&listsCount=0`;
      ttl = 86400;
    } else {
      return reply('{"error":"not found"}', 404, ch);
    }

    // caché de Cloudflare: la misma petición de varios visitantes solo llega una vez a Yahoo
    const cache = caches.default;
    const key = new Request(upstream, { method: 'GET' });
    let res = await cache.match(key);
    if (!res) {
      const r = await fetch(upstream, { headers: UA });
      if (!r.ok) return reply(JSON.stringify({ error: 'upstream', status: r.status }), 502, ch);
      res = new Response(r.body, { headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': `public, max-age=${ttl}` } });
      ctx.waitUntil(cache.put(key, res.clone()));
    }
    return new Response(res.body, { status: 200, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': `public, max-age=${Math.min(ttl, 60)}`, ...ch } });
  },
};
