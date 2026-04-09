"""OpenAPI 3.0 spec generation from route definitions and Pydantic schemas."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from polyarb.api.schemas.requests import ConfigUpdate
from polyarb.api.schemas.responses import (
    ConfigResponse,
    ErrorResponse,
    HealthResponse,
    StatusResponse,
)

_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Polyarb Daemon API", "version": "1.0.0"},
    "paths": {
        "/health": {
            "get": {
                "summary": "Legacy health check",
                "responses": {
                    "200": {"description": "Healthy"},
                    "503": {"description": "Unhealthy"},
                },
            },
        },
        "/health/live": {
            "get": {
                "summary": "Kubernetes liveness probe",
                "responses": {"200": {"description": "Alive"}},
            },
        },
        "/health/ready": {
            "get": {
                "summary": "Kubernetes readiness probe",
                "responses": {
                    "200": {"description": "Ready"},
                    "503": {"description": "Not ready"},
                },
            },
        },
        "/health/deep": {
            "get": {
                "summary": "Deep health check",
                "responses": {
                    "200": {"description": "All probes passed"},
                    "503": {"description": "Probe failed"},
                },
            },
        },
        "/status": {
            "get": {
                "summary": "Daemon status",
                "responses": {"200": {"description": "Status"}},
            },
        },
        "/matches": {
            "get": {
                "summary": "List matched pairs",
                "responses": {"200": {"description": "Match list"}},
            },
        },
        "/matches/{id}": {
            "get": {
                "summary": "Get match by index",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                    }
                ],
                "responses": {
                    "200": {"description": "Match detail"},
                    "404": {"description": "Not found"},
                },
            },
        },
        "/opportunities": {
            "get": {
                "summary": "List opportunities",
                "responses": {"200": {"description": "Opportunity list"}},
            },
        },
        "/config": {
            "get": {
                "summary": "Get config",
                "responses": {"200": {"description": "Config"}},
            },
            "post": {
                "summary": "Update config",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": ConfigUpdate.model_json_schema()
                        }
                    }
                },
                "responses": {
                    "200": {"description": "Updated config"},
                    "400": {"description": "Validation error"},
                    "401": {"description": "Unauthorized"},
                },
            },
        },
        "/execute/{id}": {
            "post": {
                "summary": "Execute trade (disabled)",
                "responses": {"503": {"description": "Execution disabled"}},
            },
        },
        "/metrics": {
            "get": {
                "summary": "Prometheus metrics",
                "responses": {"200": {"description": "Metrics"}},
            },
        },
    },
    "components": {
        "schemas": {
            "ConfigUpdate": ConfigUpdate.model_json_schema(),
            "ConfigResponse": ConfigResponse.model_json_schema(),
            "HealthResponse": HealthResponse.model_json_schema(),
            "StatusResponse": StatusResponse.model_json_schema(),
            "ErrorResponse": ErrorResponse.model_json_schema(),
        },
    },
}


async def openapi_spec(request: Request) -> JSONResponse:
    """Serve OpenAPI 3.0 spec."""
    return JSONResponse(_SPEC)
