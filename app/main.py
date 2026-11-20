"""FastAPI entrypoint for the AI-Powered Website Link Analysis API."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.schemas import ErrorResponse, LinkAnalysisRequest, LinkAnalysisResponse
from app.services.analysis_service import AnalysisService
from app.services.llm_service import LLMServiceError
from app.services.website_extractor import WebsiteExtractionError
from app.utils.validators import URLValidationError


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory for clean testing and production deployment."""
    resolved_settings = settings or get_settings()
    _configure_logging(resolved_settings.log_level)

    app = FastAPI(
        title="AI-Powered Website Link Analysis API",
        description=(
            "Analyzes a submitted website URL using live HTML content extraction and an LLM "
            "to generate structured competitor, search query, SEO, service, and target audience insights."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    app.state.settings = resolved_settings
    app.state.analysis_service = AnalysisService(resolved_settings)

    def get_analysis_service(request: Request) -> AnalysisService:
        return request.app.state.analysis_service

    # ------------------------------------------------------------------
    # Exception Handlers (consistent structured JSON errors)
    # ------------------------------------------------------------------

    @app.exception_handler(URLValidationError)
    async def handle_url_validation_error(
        _request: Request, exc: URLValidationError
    ) -> JSONResponse:
        status_code = (
            status.HTTP_403_FORBIDDEN
            if exc.error_code == "SSRF_BLOCKED"
            else status.HTTP_400_BAD_REQUEST
        )
        return JSONResponse(
            status_code=status_code,
            content=ErrorResponse(
                error_code=exc.error_code,
                message=str(exc),
            ).model_dump(),
        )

    @app.exception_handler(WebsiteExtractionError)
    async def handle_extraction_error(
        _request: Request, exc: WebsiteExtractionError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error_code=exc.error_code,
                message=exc.message,
                details=exc.details,
            ).model_dump(),
        )

    @app.exception_handler(LLMServiceError)
    async def handle_llm_error(_request: Request, exc: LLMServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error_code=exc.error_code,
                message=exc.message,
                details=exc.details,
            ).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error_code="VALIDATION_ERROR",
                message="Invalid request payload. Provide a JSON body with a valid 'url' field.",
                details=str(exc.errors()),
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger(__name__).exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error_code="INTERNAL_SERVER_ERROR",
                message="An unexpected internal error occurred while processing the request.",
            ).model_dump(),
        )

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    @app.get("/", tags=["General"])
    async def root() -> dict[str, object]:
        """Basic service metadata and discovery links."""
        return {
            "service": "AI-Powered Website Link Analysis API",
            "version": "1.0.0",
            "docs": "/docs",
            "health": "/health",
            "endpoint": "POST /link-analysis",
        }

    @app.get("/health", tags=["General"])
    async def health() -> dict[str, str]:
        """Health check endpoint for hosting platforms and monitoring."""
        return {
            "status": "ok",
            "provider": resolved_settings.llm_provider,
            "model": resolved_settings.resolved_model,
        }

    @app.post(
        "/link-analysis",
        response_model=LinkAnalysisResponse,
        responses={
            400: {"model": ErrorResponse, "description": "Invalid or unresolvable URL"},
            403: {"model": ErrorResponse, "description": "Blocked private/internal URL (SSRF)"},
            422: {"model": ErrorResponse, "description": "Validation or inaccessible website error"},
            502: {"model": ErrorResponse, "description": "Upstream website or LLM failure"},
            503: {"model": ErrorResponse, "description": "LLM credentials not configured"},
            504: {"model": ErrorResponse, "description": "Target website timed out"},
        },
        summary="Analyze a website URL across 5 business, competitor, SEO, and audience sections",
        tags=["Link Analysis"],
    )
    async def analyze_link(
        payload: LinkAnalysisRequest,
        service: AnalysisService = Depends(get_analysis_service),
    ) -> LinkAnalysisResponse:
        """Fetch website content, run LLM structured analysis, and return 5-section JSON."""
        return await service.analyze_url(payload.url)

    return app


app = create_app()
