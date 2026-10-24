"""FastAPI entry point. No service calls or database writes occur during import."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter
from starlette.exceptions import HTTPException

from app.adapters import HealthChecker, build_adapters
from app.adapters.bank import BankError
from app.api import demo, health, runs, world
from app.config import Settings, get_settings
from app.contract_models import ErrorDetail, ErrorResponse, RunEvent
from app.events import EventBus
from app.store import Store
from app.world.seed import seed_trusted_world, seed_world


def error_response(status: int, code: str, message: str, detail: dict[str, Any] | None = None) -> JSONResponse:
    fields: dict[str, Any] = {"code": code, "message": message}
    if detail is not None:
        fields["detail"] = detail
    body = ErrorResponse(error=ErrorDetail.model_validate(fields))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


class SafeErrorsMiddleware:
    """Return the contract error without leaking exception text to access logs."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except Exception:
            if started:
                raise RuntimeError("Response stream failed") from None
            await error_response(500, "internal", "Internal server error")(scope, receive, send)


def create_app(settings: Settings | None = None, *, db_path: str | Path | None = None) -> FastAPI:
    configuration = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(db_path) if db_path is not None else Store()
        store.initialize()
        app.state.settings = configuration
        app.state.store = store
        app.state.event_bus = EventBus(store)
        app.state.adapters = build_adapters(configuration, store)
        app.state.health_checker = HealthChecker(app.state.adapters)
        try:
            seed_trusted_world(store)
            if configuration.nessie_mode == "offline":
                await seed_world(store, app.state.adapters.bank)
            yield
        finally:
            store.close()

    app = FastAPI(title="Countersign API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(SafeErrorsMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=configuration.cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        codes = {
            403: "sod_violation", 404: "not_found", 409: "conflict",
            422: "validation_error", 429: "rate_limited", 503: "service_unavailable",
        }
        code = codes.get(exc.status_code, "internal")
        message = exc.detail if isinstance(exc.detail, str) and exc.status_code < 500 else "Service unavailable" if exc.status_code == 503 else "Internal server error"
        return error_response(exc.status_code, code, message)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Do not echo input values, which can include credentials or document text.
        issues = [
            {"location": list(error["loc"]), "type": error["type"]}
            for error in exc.errors()
        ]
        return error_response(422, "validation_error", "Request validation failed", {"errors": issues})

    @app.exception_handler(BankError)
    async def bank_error(request: Request, exc: BankError):
        return error_response(503, "service_unavailable", "Bank service unavailable")

    api = APIRouter(prefix="/api", responses={
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    })
    api.include_router(health.router)
    api.include_router(demo.router)
    api.include_router(runs.router)
    api.include_router(world.router)
    app.include_router(api)

    # SSE uses a streaming response; register its discriminated data schema for docs.
    original_openapi = app.openapi

    def openapi():
        schema = original_openapi()
        event_schema = TypeAdapter(RunEvent).json_schema(ref_template="#/components/schemas/{model}")
        definitions = event_schema.pop("$defs", {})
        schema.setdefault("components", {}).setdefault("schemas", {}).update(definitions)
        schema["components"]["schemas"]["RunEvent"] = event_schema
        return schema

    app.openapi = openapi
    return app


app = create_app()
