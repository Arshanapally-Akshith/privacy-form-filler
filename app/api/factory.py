from fastapi import FastAPI

from app.api.cases import router as cases_router
from app.api.form_schemas import router as form_schemas_router
from app.config.logging import configure_logging
from app.config.settings import Settings


def create_app() -> FastAPI:
    settings = Settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(cases_router)
    app.include_router(form_schemas_router)

    return app
