from openai import AsyncOpenAI
from app.core import config
from app.core.logger import get_logger

logger = get_logger("services.llm")

# Initialize centrally once using OPENAI_API_KEY
client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

async def generate_outreach_draft() -> str:
    """
    Generates a professional and concise vendor outreach message requesting a pricing discussion.
    """
    logger.info("Draft generation started")
    response = await client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": "You are a professional procurement assistant. Generate extremely concise vendor outreach messages."},
            {"role": "user", "content": "Generate a concise professional outreach message requesting vendor pricing discussion."}
        ],
        max_tokens=150
    )
    draft = response.choices[0].message.content.strip()
    logger.info("Draft generation completed")
    return draft

async def self_reflect_draft(draft: str) -> str:
    """
    Reviews and improves an outreach message for professionalism, tone, and clarity while keeping it concise.
    """
    logger.info("Self-reflection started")
    response = await client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": "You are reviewing an outreach message for professionalism, tone, and clarity. Keep it concise."},
            {"role": "user", "content": f"Improve this outreach draft while keeping it concise and professional:\n\n{draft}"}
        ],
        max_tokens=150
    )
    improved = response.choices[0].message.content.strip()
    logger.info("Self-reflection completed")
    return improved
