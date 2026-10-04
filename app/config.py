from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_env:str="development"; xai_api_key:str|None=None; grok_model:str="grok-4.1-fast"
    search_api_key:str|None=None; search_api_url:str|None=None; database_url:str="sqlite:///./factcheck.db"
    request_timeout:float=20.0; max_article_chars:int=30000
    model_config=SettingsConfigDict(env_file=".env",case_sensitive=False,extra="ignore")

@lru_cache
def get_settings()->Settings:return Settings()
