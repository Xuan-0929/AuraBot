from pydantic import BaseModel


class ServiceEndpoint(BaseModel):
    name: str
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


FRONTEND_DEV_SERVER = ServiceEndpoint(name="frontend", host="127.0.0.1", port=6400)
BACKEND_GATEWAY = ServiceEndpoint(name="gateway", host="127.0.0.1", port=6401)

# Register feature services here as they are designed. Ports start at 6402.
SERVICE_REGISTRY: list[ServiceEndpoint] = []


def list_services() -> list[ServiceEndpoint]:
    return [*SERVICE_REGISTRY]
