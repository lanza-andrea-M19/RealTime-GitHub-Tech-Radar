from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # GitHub
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")

    # PostgreSQL Database
    database_url: str = Field(
        default="postgresql://techradar:techradar@localhost:5433/techradar",
        alias="DATABASE_URL",
    )
    postgres_user: str = Field(default="techradar", alias="POSTGRES_USER")
    postgres_password: str = Field(default="techradar", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="techradar", alias="POSTGRES_DB")
    postgres_port: int = Field(default=5433, alias="POSTGRES_PORT")

    # Gemini LLM
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")

    # LangSmith
    langchain_tracing_v2: bool = Field(default=False, alias="LANGCHAIN_TRACING_V2")
    langchain_api_key: str | None = Field(default=None, alias="LANGCHAIN_API_KEY")
    langchain_project: str = Field(default="tech-radar-pipeline", alias="LANGCHAIN_PROJECT")


settings = Settings()
