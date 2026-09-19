from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    federato_client_id: str | None
    federato_client_secret: str | None
    federato_auth_url: str
    federato_audience: str
    federato_handler_url: str
    request_timeout_seconds: float
    cors_origins: tuple[str, ...]
    openai_api_key: str | None
    openai_model: str
    openai_reasoning_effort: str
    openai_request_timeout_seconds: float
    openai_max_turns: int
    openai_max_query_calls: int
    openai_base_url: str

    @property
    def federato_configured(self) -> bool:
        return bool(self.federato_client_id and self.federato_client_secret)

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)

    @classmethod
    def from_env(cls) -> "Settings":
        origins = os.getenv(
            "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        )
        return cls(
            federato_client_id=os.getenv("FEDERATO_CLIENT_ID") or None,
            federato_client_secret=os.getenv("FEDERATO_CLIENT_SECRET") or None,
            federato_auth_url=os.getenv(
                "FEDERATO_AUTH_URL",
                "https://auth.product.federato.ai/oauth/token",
            ),
            federato_audience=os.getenv(
                "FEDERATO_AUDIENCE", "https://product.federato.ai/core-api"
            ),
            federato_handler_url=os.getenv(
                "FEDERATO_HANDLER_URL",
                "https://product.federato.ai/integrations-api/handlers/"
                "federato-hack-north?outputOnly=true",
            ),
            request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")),
            cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-terra"),
            openai_reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "medium"),
            openai_request_timeout_seconds=max(
                20.0,
                min(
                    float(os.getenv("OPENAI_REQUEST_TIMEOUT_SECONDS", "180")),
                    300.0,
                ),
            ),
            openai_max_turns=max(1, min(int(os.getenv("OPENAI_MAX_TURNS", "6")), 10)),
            openai_max_query_calls=max(
                1, min(int(os.getenv("OPENAI_MAX_QUERY_CALLS", "4")), 8)
            ),
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        )


settings = Settings.from_env()
