from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "D.Marina Branch Server"
    port: int = 4174
    db_url: str = "sqlite://./branch.db"
    jwt_secret: str = "change-me-branch"
    # "*" is fine pre-launch (no cookies are used — auth is a Bearer header, so wildcard + no
    # credentials is safe per the CORS spec). Revisit before any real deployment: set this to
    # the actual frontend origin(s) via .env instead of leaving it wide open.
    cors_origins: str = "*"

    # --- Sync scheduler ------------------------------------------------------------------------
    # Every two hours, as agreed. The retry interval is shorter on purpose: "sync whenever the
    # internet is available" and "sync every two hours" only mean the same thing on a link that is
    # always up. On a branch link that drops, a failed tick that waited the full two hours before
    # trying again would routinely leave a shop hours behind for the sake of a connection that came
    # back three minutes later. So: two hours when things are working, ten minutes while they
    # aren't, back to two hours once a run succeeds.
    sync_enabled: bool = True
    sync_interval_seconds: int = 7200
    sync_retry_seconds: int = 600
    # A freshly-verified branch should not sit silent for two hours before proving it works.
    sync_startup_delay_seconds: int = 60


settings = Settings()

TORTOISE_ORM = {
    "connections": {"default": settings.db_url},
    "apps": {
        "models": {
            "models": ["app.models", "aerich.models"],
            "default_connection": "default",
        }
    },
}
