import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Centralized configuration variables
APPROVAL_TIMEOUT_SECONDS: int = int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "10"))

OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable is required and cannot be empty")

OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()

OPENAI_TEMPERATURE: float = float(os.getenv("OPENAI_TEMPERATURE", "0.3"))

AGENT_STEP_DELAY_SECONDS: int = int(os.getenv("AGENT_STEP_DELAY_SECONDS", "2"))

MAX_REGENERATION_ATTEMPTS: int = int(os.getenv("MAX_REGENERATION_ATTEMPTS", "3"))
