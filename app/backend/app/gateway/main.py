from fastapi import FastAPI

from .chat_routes import router as chat_router
from .debug_routes import router as debug_router
from .status_routes import health, router as status_router, services
from .task_routes import router as task_router

app = FastAPI(title="AuraBot Backend Gateway")

app.include_router(status_router)
app.include_router(chat_router)
app.include_router(task_router)
app.include_router(debug_router)
