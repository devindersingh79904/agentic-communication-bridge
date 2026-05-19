import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Centralized configuration variables
APPROVAL_TIMEOUT_SECONDS: int = int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "10"))
