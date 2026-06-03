from fastapi import FastAPI

app = FastAPI(title="AuraBot Template Service")


@app.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "service": "_template"}
