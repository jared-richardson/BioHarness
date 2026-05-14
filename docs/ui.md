# UI

## Primary UI

The primary public UI is the React/Vite app staged at:

```text
apps/web/
```

Start the API backend from the repository root:

```bash
.venv/bin/python ui_v2_api.py
```

The backend is local-only by default (`127.0.0.1:8000`). Use
`BIO_HARNESS_UI_HOST`, `BIO_HARNESS_UI_PORT`, and `VITE_API_BASE` for custom
local setups. Set `BIO_HARNESS_UI_HOST=0.0.0.0` only on a trusted network
because the API includes a local terminal endpoint. If you serve the frontend
from a LAN hostname, set `BIO_HARNESS_UI_CORS_ORIGINS` to the explicit
comma-separated browser origins you want to allow.

Then start the Vite frontend:

```bash
cd apps/web
npm ci
npm run dev
```

Open:

```text
http://localhost:5173
```

If setup is incomplete, the UI opens the first-run setup wizard. To force the
wizard for QA after setup is already complete, open:

```text
http://localhost:5173/?setup=1
```

Release checks:

```bash
cd apps/web
npm ci
npm run lint
npm run build
npm audit --audit-level=moderate
```

`apps/web/` is staged as source only: no `node_modules/`, no `.vite/`, and no
`dist/`.

## Compatibility UI

The Streamlit UI remains available at:

```text
apps/streamlit/app.py
```

Launch it with:

```bash
.venv/bin/streamlit run apps/streamlit/app.py
```
