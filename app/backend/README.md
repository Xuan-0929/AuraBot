# AuraBot Backend

Python services live here. The gateway is the only backend endpoint that the
Electron and Vue layers should know about.

## Ports

- Frontend dev server: `127.0.0.1:6400`
- Backend gateway: `127.0.0.1:6401`
- Feature services: `127.0.0.1:6402+`

## Run gateway

```bash
cd app
corepack pnpm dev:backend
```

Or from this directory:

```bash
python -m uvicorn app.gateway.main:app --host 127.0.0.1 --port 6401
```

The Electron layer checks `/health` and `/services` through the typed
`backend:get-status` IPC bridge; Vue renderer code should keep using
`window.aurabot` instead of calling the backend directly.

## Read-only backend bridge

The gateway also exposes read-only endpoints for backend-first migration:

- `GET /chat/main-session?storage_path=<sqlite>&profile_id=<profile>`
- `GET /tasks?storage_path=<sqlite>&profile_id=<profile>`
- `GET /debug/events?storage_path=<sqlite>&limit=100`
- `GET /debug/memory?storage_path=<sqlite>&profile_id=<profile>&event_limit=80`
- `GET /debug/tasks?storage_path=<sqlite>&profile_id=<profile>&limit=50`

Electron supplies the current SQLite `storagePath` and uses these endpoints as
backend-first read paths for main chat history, task lists, event log, memory,
and task debugging, with local TypeScript fallbacks when the gateway is
offline. The endpoints open SQLite in read-only mode and do not mutate chat,
memory, task, or profile data. Chat writes and task mutations still go through
Electron and `core-legacy`.

## Add a microservice

Copy `app/services/_template` to `app/services/<service_name>`, assign the
next available port in `service.toml`, and register it in
`app/gateway/service_registry.py`. Every service must expose `GET /health`.
