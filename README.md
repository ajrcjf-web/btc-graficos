# BTC Gráficos

Gráficos de criptomonedas, acciones e índices con indicadores (BMSB, medias, RSI, volumen, ciclo del halving) y herramientas de dibujo.

**Web:** https://ajrcjf-web.github.io/btc-graficos/

## Cómo se actualiza

- **Criptomonedas:** el histórico está en `data/` y lo actualiza GitHub Actions cada 30 minutos desde Binance. Al abrir la web, el navegador completa las velas que falten y recibe los precios en directo de Binance (API pública de datos de mercado).
- **Acciones e índices:** Yahoo Finance a través de un Worker gratuito de Cloudflare (`worker/yahoo.js`): la web busca cualquier acción, ETF o índice, carga todo su histórico y se actualiza cada minuto (con posible retraso respecto al mercado). Sin Worker, se usan los datos que GitHub Actions publica cada 30 minutos.
- Todo es gratuito: GitHub Pages + GitHub Actions en un repositorio público.

## Archivos

- `index.html` — la web (una sola página).
- `tools/update.py` — descarga y actualiza los datos.
- `data/` — datos iniciales; las versiones al día se publican en la web.
- `worker/yahoo.js` — Worker de Cloudflare (se pega en el panel de Cloudflare).
- `.github/workflows/datos.yml` — actualización cada 30 minutos y publicación en GitHub Pages.

Para añadir una acción o un token con histórico guardado, añade su entrada a `data/index.json` (las acciones con `"type": "stock"` y `"yahoo": "TICKER"`).

Los datos son de fuentes públicas (Binance, Yahoo Finance, Crypto.com) y pueden tener retrasos o errores. Nada de esto es asesoramiento financiero.
