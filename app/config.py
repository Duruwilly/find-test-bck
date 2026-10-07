from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite works out of the box for local dev. In production use e.g.
    # postgresql+asyncpg://user:pass@host/findme
    database_url: str = "sqlite+aiosqlite:///./findme.db"
    media_dir: str = "./media"
    public_base_url: str = "http://localhost:3001"
    # Leave empty to derive the API's public address from each request (recommended).
    api_base_url: str = ""
    cors_origins: str = "http://localhost:3001"
    ip_hash_salt: str = "change-me"

    max_photo_bytes: int = 8 * 1024 * 1024
    max_voice_bytes: int = 1 * 1024 * 1024
    photo_max_edge: int = 1280
    photo_quality: int = 70


settings = Settings()
