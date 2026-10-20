"""Application configuration and environment settings."""

import os
from typing import Literal, Optional
from dotenv import load_dotenv
from pydantic import Field, BaseModel

load_dotenv()

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Settings(BaseSettings):
        """Configuration loaded from environment variables and .env file."""

        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore",
        )

        # GitLab Configuration
        gitlab_url: str = Field(default="https://gitlab.com", alias="GITLAB_URL")
        gitlab_private_token: str = Field(default="", alias="GITLAB_PRIVATE_TOKEN")
        gitlab_webhook_secret: str = Field(default="", alias="GITLAB_WEBHOOK_SECRET")

        # Orchestrator Server
        app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
        app_port: int = Field(default=8000, alias="APP_PORT")
        app_env: str = Field(default="development", alias="APP_ENV")
        log_level: str = Field(default="INFO", alias="LOG_LEVEL")
        dry_run: bool = Field(default=False, alias="DRY_RUN")

        # LLM Settings
        llm_provider: Literal["openai", "anthropic", "google"] = Field(
            default="openai", alias="LLM_PROVIDER"
        )
        llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
        openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
        anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")
        google_api_key: Optional[str] = Field(default=None, alias="GOOGLE_API_KEY")

        # Agent Toggles
        enable_security_agent: bool = Field(default=True, alias="ENABLE_SECURITY_AGENT")
        enable_verification_agent: bool = Field(default=True, alias="ENABLE_VERIFICATION_AGENT")
        enable_release_agent: bool = Field(default=True, alias="ENABLE_RELEASE_AGENT")
        enable_monitoring_agent: bool = Field(default=True, alias="ENABLE_MONITORING_AGENT")
        enable_governance_agent: bool = Field(default=True, alias="ENABLE_GOVERNANCE_AGENT")

        # Security Policy
        security_gate_policy: Literal["block", "warn"] = Field(
            default="block", alias="SECURITY_GATE_POLICY"
        )
        max_allowed_critical_vulnerabilities: int = Field(
            default=0, alias="MAX_ALLOWED_CRITICAL_VULNERABILITIES"
        )
        max_allowed_high_vulnerabilities: int = Field(
            default=0, alias="MAX_ALLOWED_HIGH_VULNERABILITIES"
        )

        # Release Configuration
        default_release_branch: str = Field(default="main", alias="DEFAULT_RELEASE_BRANCH")
        auto_semver: bool = Field(default=True, alias="AUTO_SEMVER")
        tag_prefix: str = Field(default="v", alias="TAG_PREFIX")

        # Monitoring & Notifications
        prometheus_url: Optional[str] = Field(default=None, alias="PROMETHEUS_URL")
        sentry_auth_token: Optional[str] = Field(default=None, alias="SENTRY_AUTH_TOKEN")
        slack_webhook_url: Optional[str] = Field(default=None, alias="SLACK_WEBHOOK_URL")
        teams_webhook_url: Optional[str] = Field(default=None, alias="TEAMS_WEBHOOK_URL")

except ImportError:
    # Graceful fallback when pydantic-settings is not installed yet
    class Settings(BaseModel):  # type: ignore
        """Fallback configuration using os.getenv."""

        gitlab_url: str = os.getenv("GITLAB_URL", "https://gitlab.com")
        gitlab_private_token: str = os.getenv("GITLAB_PRIVATE_TOKEN", "")
        gitlab_webhook_secret: str = os.getenv("GITLAB_WEBHOOK_SECRET", "")

        app_host: str = os.getenv("APP_HOST", "0.0.0.0")
        app_port: int = int(os.getenv("APP_PORT", "8000"))
        app_env: str = os.getenv("APP_ENV", "development")
        log_level: str = os.getenv("LOG_LEVEL", "INFO")
        dry_run: bool = os.getenv("DRY_RUN", "false").lower() in ["true", "1", "yes"]

        llm_provider: str = os.getenv("LLM_PROVIDER", "openai")
        llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
        openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
        anthropic_api_key: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
        google_api_key: Optional[str] = os.getenv("GOOGLE_API_KEY")

        enable_security_agent: bool = os.getenv("ENABLE_SECURITY_AGENT", "true").lower() in ["true", "1"]
        enable_verification_agent: bool = os.getenv("ENABLE_VERIFICATION_AGENT", "true").lower() in ["true", "1"]
        enable_release_agent: bool = os.getenv("ENABLE_RELEASE_AGENT", "true").lower() in ["true", "1"]
        enable_monitoring_agent: bool = os.getenv("ENABLE_MONITORING_AGENT", "true").lower() in ["true", "1"]
        enable_governance_agent: bool = os.getenv("ENABLE_GOVERNANCE_AGENT", "true").lower() in ["true", "1"]

        security_gate_policy: str = os.getenv("SECURITY_GATE_POLICY", "block")
        max_allowed_critical_vulnerabilities: int = int(os.getenv("MAX_ALLOWED_CRITICAL_VULNERABILITIES", "0"))
        max_allowed_high_vulnerabilities: int = int(os.getenv("MAX_ALLOWED_HIGH_VULNERABILITIES", "0"))

        default_release_branch: str = os.getenv("DEFAULT_RELEASE_BRANCH", "main")
        auto_semver: bool = os.getenv("AUTO_SEMVER", "true").lower() in ["true", "1"]
        tag_prefix: str = os.getenv("TAG_PREFIX", "v")

        prometheus_url: Optional[str] = os.getenv("PROMETHEUS_URL")
        sentry_auth_token: Optional[str] = os.getenv("SENTRY_AUTH_TOKEN")
        slack_webhook_url: Optional[str] = os.getenv("SLACK_WEBHOOK_URL")
        teams_webhook_url: Optional[str] = os.getenv("TEAMS_WEBHOOK_URL")


settings = Settings()
