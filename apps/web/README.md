# Bio-Harness Web UI

This is the primary React/Vite UI source for Bio-Harness. The Streamlit app at
`apps/streamlit/app.py` remains available as a compatibility fallback.

## Development

Start the API backend from the repository root:

```bash
.venv/bin/python ui_v2_api.py
```

The backend binds to `127.0.0.1:8000` by default. Use these environment
variables only when you need a custom local setup:

```bash
BIO_HARNESS_UI_HOST=127.0.0.1 BIO_HARNESS_UI_PORT=8000 .venv/bin/python ui_v2_api.py
```

Set `BIO_HARNESS_UI_HOST=0.0.0.0` only on a trusted network; the API exposes a
local terminal endpoint intended for single-user local development. If you also
serve the Vite frontend from a LAN hostname, set
`BIO_HARNESS_UI_CORS_ORIGINS` to a comma-separated list of allowed origins.
Localhost and `127.0.0.1` frontend ports are allowed by default for local
development; set `BIO_HARNESS_UI_CORS_ORIGIN_REGEX=` to disable that regex.

Then start the Vite UI:

```bash
cd apps/web
npm ci
npm run lint
npm run build
npm audit --audit-level=moderate
npm run dev
```

To point the frontend at a non-default backend URL, set `VITE_API_BASE` or put
it in `.env.local`:

```bash
VITE_API_BASE=http://127.0.0.1:8000 npm run dev
```

For a custom local frontend port:

```bash
BIO_HARNESS_UI_CORS_ORIGINS=http://127.0.0.1:15173,http://localhost:15173 .venv/bin/python ui_v2_api.py
VITE_API_BASE=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 15173
```

Generated folders such as `node_modules/`, `.vite/`, and `dist/` are not part of
the public source tree.
