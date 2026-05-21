# AuraBot Backend

Python services live here. The gateway is the only backend endpoint that the
Electron and Vue layers should know about.

## Ports

- Frontend dev server: `127.0.0.1:6400`
- Backend gateway: `127.0.0.1:6401`
- Feature services: `127.0.0.1:6402+`

## Run gateway

```bash
python -m uvicorn app.gateway.main:app --host 127.0.0.1 --port 6401
```

## Add a microservice

Copy `app/services/_template` to `app/services/<service_name>`, assign the
next available port in `service.toml`, and register it in
`app/gateway/service_registry.py`. Every service must expose `GET /health`.
