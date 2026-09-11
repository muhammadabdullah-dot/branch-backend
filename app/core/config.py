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
