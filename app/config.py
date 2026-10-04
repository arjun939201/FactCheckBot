from functools import lru_cache
from pydantic_settings import BaseSettings,SettingsConfigDict
class Settings(BaseSettings):
    app_env:str="development"; groq_api_key:str|None=None
    groq_model:str="llama-3.3-70b-versatile"; groq_fallback_model:str="openai/gpt-oss-120b"
    groq_vision_model:str="meta-llama/llama-4-scout-17b-16e-instruct"
    groq_transcription_model:str="whisper-large-v3-turbo"
    database_url:str="sqlite:///./factcheck.db"; request_timeout:float=20.0; max_article_chars:int=30000
    model_config=SettingsConfigDict(env_file=".env",case_sensitive=False,extra="ignore")
@lru_cache
def get_settings()->Settings:return Settings()
