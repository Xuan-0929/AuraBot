from fastapi import APIRouter

from .service_registry import BACKEND_GATEWAY, FRONTEND_DEV_SERVER, list_services

router = APIRouter()


@router.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "service": "gateway",
        "host": BACKEND_GATEWAY.host,
        "port": BACKEND_GATEWAY.port,
        "frontend": FRONTEND_DEV_SERVER.url,
    }


@router.get("/services")
def services() -> dict[str, object]:
    return {"services": [service.model_dump() for service in list_services()]}
