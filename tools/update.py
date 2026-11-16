#!/usr/bin/env python3
"""Actualiza los datos de precios del artefacto «BTC Gráficos».

Uso:
  python3 -I update.py DATA_DIR [--watch watch.json] [--cdc CDC_DIR] [--from-raw RAW_DIR]

DATA_DIR  carpeta con index.json y los archivos {SYM}_1h.json / {SYM}_1d.json (formato columnas t,o,h,l,c,v).
--watch   JSON con la lista del usuario: {"symbols":[{"sym":"KAS","cdc":"KAS_USD"}, ...]} (de la base de datos del artefacto).
--cdc     carpeta con respuestas guardadas del conector Crypto.com: {SYM}_1h.json y {SYM}_1D.json
          (el texto devuelto por get_candlestick; se usa para los activos que no cotizan en Binance).
--from-raw  usa zips ya descargados de data.binance.vision en vez de descargarlos.

Fuente principal: data.binance.vision (Binance spot, par XXXUSDT). Índices (campo "yahoo"): Yahoo Finance. Guarda 1D completo y 1H de los últimos 730 días.
Imprime NEED_CDC: SYM=INSTRUMENTO ... (activos sin Binance ni Yahoo, que se completan con el conector Crypto.com)
y al final la lista de archivos que han cambiado (CHANGED: ...).
"""
import sys, os, json, io, zipfile, time, datetime as dt, urllib.request, urllib.error, urllib.parse, argparse, re

B = 'https://data.binance.vision/data/spot'
H1_KEEP_DAYS = 730

def months_between(a, b):
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        yield f'{y}-{m:02d}'
        m += 1
        if m > 12: y += 1; m = 1

def fetch(url, raw=None):
    name = url.rsplit('/', 1)[1]
    if raw:
        p = os.path.join(raw, name)
        return open(p, 'rb').read() if os.path.exists(p) else None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return None

def parse_zip(data):
    rows = []
    z = zipfile.ZipFile(io.BytesIO(data))
    for line in io.TextIOWrapper(z.open(z.namelist()[0])):
        p = line.strip().split(',')
        if not p or not p[0].isdigit(): continue
        t = int(p[0]); t = t // 1000000 if t > 1e14 else t // 1000
        rows.append([t] + [float(x) for x in p[1:6]])
    return rows

def load(path):
    if not os.path.exists(path): return {}
    o = json.load(open(path))
    v = o.get('v') or [0] * len(o['t'])
    return {t: [t, o['o'][i], o['h'][i], o['l'][i], o['c'][i], v[i]] for i, t in enumerate(o['t'])}

def dump(path, rows):
    rows = sorted(rows.values())
    o = {'t': [r[0] for r in rows], 'o': [r[1] for r in rows], 'h': [r[2] for r in rows], 'l': [r[3] for r in rows], 'c': [r[4] for r in rows], 'v': [round(r[5] if len(r) > 5 else 0, 4) for r in rows]}
    s = json.dumps(o, separators=(',', ':'))
    old = open(path).read() if os.path.exists(path) else None
    if s != old:
        open(path, 'w').write(s); return True
    return False

def binance_has(pair):
    y = dt.date.today().replace(day=1) - dt.timedelta(days=1)
    return fetch(f'{B}/monthly/klines/{pair}/1d/{pair}-1d-{y:%Y-%m}.zip') is not None

def update_binance(pair, rows, iv, start, raw=None):
    """Descarga desde `start` (date) hasta ayer: meses completos en ficheros mensuales, el mes en curso en diarios."""
    today = dt.date.today()
    first_cur = today.replace(day=1)
    for mo in months_between(start, first_cur - dt.timedelta(days=1)):
        if start.replace(day=1) <= dt.date(int(mo[:4]), int(mo[5:]), 1) < first_cur:
            d = fetch(f'{B}/monthly/klines/{pair}/{iv}/{pair}-{iv}-{mo}.zip', raw)
            if d:
                for r in parse_zip(d): rows[r[0]] = r
    day = max(start, first_cur)
    while day < today:
        d = fetch(f'{B}/daily/klines/{pair}/{iv}/{pair}-{iv}-{day:%Y-%m-%d}.zip', raw)
        if d:
            for r in parse_zip(d): rows[r[0]] = r
        day += dt.timedelta(days=1)

YNAME = {}

def yahoo(ysym, interval, rng=None):
    """Índices desde Yahoo Finance (velas diarias completas o de 60 min de los últimos 730 días).
    Las velas de 60 min empiezan a las :30; se guardan redondeadas a la hora en punto."""
    q = f'interval={interval}&includeAdjustedClose=false&' + (f'range={rng}' if rng else 'period1=0&period2=9999999999')
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ysym)}?{q}'
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            j = json.load(urllib.request.urlopen(req, timeout=60))['chart']['result'][0]
            break
        except Exception:
            time.sleep(3)
    else:
        return {}
    step = 86400 if interval == '1d' else 3600
    qd = j['indicators']['quote'][0]; rows = {}
    meta = j.get('meta') or {}
    YNAME[ysym] = meta.get('longName') or meta.get('shortName') or YNAME.get(ysym)
    for i, t in enumerate(j.get('timestamp') or []):
        o, h, l, c, v = (qd[k][i] for k in ('open', 'high', 'low', 'close', 'volume'))
        if None in (o, h, l, c) or c <= 0: continue
        t = t // step * step
        rows[t] = [t, round(o, 4), round(h, 4), round(l, 4), round(c, 4), v or 0]
    return rows

API = 'https://data-api.binance.vision/api/v3'

def binance_rest(pair, iv, rows):
    """Completa desde la última vela guardada hasta ahora (incluye la vela en curso)."""
    start = (max(rows) if rows else int(time.time()) - 3 * 86400) * 1000
    for _ in range(40):
        try:
            with urllib.request.urlopen(f'{API}/klines?symbol={pair}&interval={iv}&startTime={start}&limit=1000', timeout=30) as r:
                k = json.load(r)
        except Exception:
            return False
        for x in k:
            t = int(x[0]) // 1000
            rows[t] = [t, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])]
        if len(k) < 1000: return True
        start = int(k[-1][0]) + 1
    return True

def binance_list(path):
    """Lista de tokens con par USDT en Binance spot (para el buscador de la página)."""
    try:
        with urllib.request.urlopen(f'{API}/exchangeInfo?permissions=SPOT&symbolStatus=TRADING', timeout=60) as r:
            d = json.load(r)
    except Exception:
        return False
    stable = {'USDC', 'FDUSD', 'TUSD', 'USDP', 'DAI', 'EUR', 'EURI', 'AEUR', 'BUSD', 'USD1', 'XUSD', 'BFUSD', 'PAXG', 'USDE'}
    out = sorted({x['baseAsset'] for x in d.get('symbols', []) if x.get('quoteAsset') == 'USDT' and x.get('status') == 'TRADING'
                  and x['baseAsset'] not in stable and not re.search(r'(UP|DOWN|BULL|BEAR)$', x['baseAsset'])})
    s = json.dumps(out, separators=(',', ':'))
    old = open(path).read() if os.path.exists(path) else None
    if s != old:
        open(path, 'w').write(s); return True
    return False

CDC_API = 'https://api.crypto.com/exchange/v1/public/get-candlestick'

def cdc_rest(inst, tf):
    """API pública de Crypto.com (para activos que no cotizan en Binance). Devuelve {} si no responde."""
    try:
        req = urllib.request.Request(f'{CDC_API}?instrument_name={inst}&timeframe={tf}&count=300', headers={'User-Agent': 'Mozilla/5.0'})
        d = json.load(urllib.request.urlopen(req, timeout=30))
        out = {}
        for x in (d.get('result') or {}).get('data') or []:
            t = int(x['t']) // 1000
            out[t] = [t, float(x['o']), float(x['h']), float(x['l']), float(x['c']), float(x.get('v') or 0)]
        return out
    except Exception:
        return {}

def parse_cdc(path):
    txt = open(path).read()
    a, b = txt.find('{'), txt.rfind('}')
    o = json.loads(txt[a:b + 1])
    out = {}
    for x in o.get('data', []):
        t = int(dt.datetime.strptime(x['timestamp'][:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=dt.timezone.utc).timestamp())
        out[t] = [t, float(x['open']), float(x['high']), float(x['low']), float(x['close']), float(x.get('volume') or 0)]
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('data'); ap.add_argument('--watch'); ap.add_argument('--cdc'); ap.add_argument('--from-raw'); ap.add_argument('--cdc-rest', action='store_true', help='usar la API pública de Crypto.com (GitHub Actions)')
    a = ap.parse_args()
    idx_path = os.path.join(a.data, 'index.json')
    idx = json.load(open(idx_path)) if os.path.exists(idx_path) else {'symbols': []}
    known = {s['sym']: s for s in idx['symbols']}
    if a.watch and os.path.exists(a.watch):
        for w in json.load(open(a.watch)).get('symbols', []):
            sym = re.sub(r'[^A-Z0-9.\-^]', '', str(w.get('sym', '')).upper())
            if not sym or sym in known: continue
            if w.get('type') == 'stock':
                ysym = w.get('yahoo') or sym
                if not yahoo(ysym, '1d', '5d'):
                    print('INVALID:', sym); continue
                s = {'sym': sym, 'name': YNAME.get(ysym) or sym, 'yahoo': ysym, 'type': 'stock', 'cdc': None, 'bin': None, 'top': False}
            else:
                pair = sym + 'USDT'
                s = {'sym': sym, 'cdc': w.get('cdc') or None, 'bin': pair if binance_has(pair) else None, 'top': False}
                if not s['bin'] and not s['cdc']:
                    print('INVALID:', sym); continue
            idx['symbols'].append(s); known[sym] = s
    changed = []
    now = dt.date.today()
    for s in idx['symbols']:
        sym = s['sym']
        for iv in ('1d', '1h'):
            path = os.path.join(a.data, f'{sym}_{iv}.json')
            rows = load(path)
            if s.get('yahoo'):
                new = yahoo(s['yahoo'], '1d') if iv == '1d' else yahoo(s['yahoo'], '60m', '730d')
                rows.update(new)
            elif s.get('bin'):
                if rows:
                    last = dt.datetime.fromtimestamp(max(rows), dt.timezone.utc).date()
                    start = last - dt.timedelta(days=1)
                else:
                    start = dt.date(2017, 7, 1) if iv == '1d' else now - dt.timedelta(days=H1_KEEP_DAYS)
                update_binance(s['bin'], rows, iv, start, a.from_raw)
                if not a.from_raw: binance_rest(s['bin'], iv, rows)
            if not s.get('bin') and not s.get('yahoo') and s.get('cdc') and not a.from_raw and a.cdc_rest:
                for t, r in cdc_rest(s['cdc'], '1h' if iv == '1h' else '1D').items(): rows[t] = r
            if a.cdc:
                p = os.path.join(a.cdc, f"{sym}_{'1h' if iv == '1h' else '1D'}.json")
                if os.path.exists(p):
                    new = parse_cdc(p)
                    # con Binance solo se añaden velas posteriores a las de Binance; sin Binance, todas
                    lim = max(rows) if (rows and s.get('bin')) else 0
                    for t, r in new.items():
                        if t > lim: rows[t] = r
            if iv == '1h' and rows:
                cut = max(rows) - H1_KEEP_DAYS * 86400
                rows = {t: r for t, r in rows.items() if t >= cut}
            if rows and dump(path, rows): changed.append(os.path.basename(path))
            if rows:
                s[f'end_{iv}'] = max(rows); s[f'start_{iv}'] = min(rows)
                if iv == '1d':
                    ks = sorted(rows); s['last'] = rows[ks[-1]][4]; s['prev'] = rows[ks[-2]][4] if len(ks) > 1 else None
    if not a.from_raw and binance_list(os.path.join(a.data, 'binance.json')): changed.append('binance.json')
    need = [f"{s['sym']}={s['cdc']}" for s in idx['symbols'] if not s.get('bin') and not s.get('yahoo') and s.get('cdc')]
    print('NEED_CDC:', ' '.join(need))
    idx['updated'] = int(time.time())
    json.dump(idx, open(idx_path, 'w'), separators=(',', ':'))
    changed.append('index.json')
    print('CHANGED:', ' '.join(changed))

if __name__ == '__main__':
    main()
