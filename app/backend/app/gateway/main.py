from fastapi import FastAPI

from .service_registry import BACKEND_GATEWAY, FRONTEND_DEV_SERVER, list_services

app = FastAPI(title="AuraBot Backend Gateway")


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "service": "gateway",
        "host": BACKEND_GATEWAY.host,
        "port": BACKEND_GATEWAY.port,
        "frontend": FRONTEND_DEV_SERVER.url,
    }


@app.get("/services")
def services() -> dict[str, object]:
    return {"services": [service.model_dump() for service in list_services()]}
