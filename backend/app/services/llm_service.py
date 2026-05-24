import re
from typing import Optional

from openai import AsyncOpenAI

from app.core import config
from app.core.logger import get_logger

logger = get_logger("services.llm")

# Maximum allowed output length for defensive truncation
MAX_OUTPUT_LENGTH = 2000

# ---------------------------------------------------------------------------
# Provider-aware client & model helpers
# ---------------------------------------------------------------------------

def _get_client() -> AsyncOpenAI:
    """
    Returns the appropriate AsyncOpenAI client based on AGENT_PROVIDER.

    - For "openai": uses OPENAI_API_KEY (standard OpenAI endpoint).
    - For "ollama": points base_url at the local Ollama OpenAI-compatible server.
    """
    if config.AGENT_PROVIDER == "ollama":
        # Ollama's OpenAI-compatible endpoint; api_key may be empty or a
        # user-configured key if the Ollama setup requires one.
        return AsyncOpenAI(
            api_key=config.OPENAI_API_KEY or "ollama",
            base_url=config.OLLAMA_BASE_URL,
        )
    # Default: standard OpenAI
    return AsyncOpenAI(api_key=config.OPENAI_API_KEY)


def _get_model() -> str:
    """Returns the model name for the currently configured AGENT_PROVIDER."""
    if config.AGENT_PROVIDER == "ollama":
        return config.OLLAMA_MODEL
    return config.OPENAI_MODEL


# Shared client instance – created lazily so config is fully resolved.
_client: Optional[AsyncOpenAI] = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = _get_client()
    return _client


def get_provider_name() -> str:
    """Returns a human-readable provider name for use in error messages."""
    return config.AGENT_PROVIDER


# ---------------------------------------------------------------------------
# Token usage logging helper
# ---------------------------------------------------------------------------

def _log_llm_used(step_name: str) -> None:
    """Log a clear 'llm used' message with provider and model name."""
    logger.info(
        "llm used : %s (%s provider)",
        _get_model(),
        config.AGENT_PROVIDER,
        extra={
            "llm_step": step_name,
            "model": _get_model(),
            "provider": config.AGENT_PROVIDER,
        },
    )


def _log_usage(step_name: str, response) -> None:
    """Log model name and token usage from an OpenAI-compatible response."""
    usage = getattr(response, "usage", None)
    if usage:
        logger.info(
            "Step=%s Model=%s PromptTokens=%s CompletionTokens=%s TotalTokens=%s",
            step_name,
            getattr(response, "model", _get_model()),
            getattr(usage, "prompt_tokens", "?"),
            getattr(usage, "completion_tokens", "?"),
            getattr(usage, "total_tokens", "?"),
            extra={
                "llm_step": step_name,
                "model": getattr(response, "model", _get_model()),
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            },
        )
    else:
        # Ollama may not return usage info in all configurations
        logger.info(
            "Step=%s Model=%s (no token usage reported by provider)",
            step_name,
            _get_model(),
        )


# ---------------------------------------------------------------------------
# LLM call helpers
# ---------------------------------------------------------------------------

async def generate_outreach_draft(prompt: str, analysis_summary: str, selected_vendor: Optional[dict] = None) -> str:
    """
    Generates a professional and concise vendor outreach message using the user's
    task prompt and analysis summary for context.
    """
    logger.info("Draft generation started (provider=%s, model=%s)", config.AGENT_PROVIDER, _get_model())

    location_context = (
        f"Procurement region:\n"
        f"City: {config.DEFAULT_CITY}\n"
        f"Locality: {config.DEFAULT_LOCALITY}\n"
        f"Pincode: {config.DEFAULT_PINCODE}"
    )

    vendor_context = ""
    if selected_vendor:
        vendor_context = (
            f"Preferred vendor:\n"
            f"{selected_vendor['name']} located in {selected_vendor['location']}"
        )

    user_content = (
        f"{location_context}\n\n"
        f"{vendor_context}\n\n"
        f"Task: {prompt}\n\n"
        f"Analysis: {analysis_summary}\n\n"
        f"Generate a concise, realistic, professional vendor outreach email.\n\n"
        f"Sign the message as:\n"
        f"{config.DEFAULT_USER_NAME}\n"
        f"{config.DEFAULT_COMPANY_NAME}"
    )

    _log_llm_used("generate_outreach_draft")
    try:
        response = await get_client().chat.completions.create(
            model=_get_model(),
            messages=[
                {
                    "role": "system",
                    "content": "You are a professional procurement assistant. Generate extremely concise vendor outreach messages."
                },
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            temperature=config.OPENAI_TEMPERATURE,
            max_tokens=150
        )
    except Exception as e:
        logger.exception("LLM request failed during draft generation (provider=%s)", config.AGENT_PROVIDER)
        raise

    _log_usage("generate_outreach_draft", response)
    draft = response.choices[0].message.content.strip()

    # Validate LLM output
    if not draft:
        logger.warning("LLM returned empty draft output")
        raise ValueError("Empty draft generated by LLM")

    # Defensive truncation
    draft = draft[:MAX_OUTPUT_LENGTH]

    logger.info("Draft generation completed")
    return draft


async def self_reflect_draft(draft: str, prompt: str, rejection_feedback: Optional[str] = None) -> str:
    """
    Reviews and improves an outreach message using a two-pass LLM approach:
    Pass 1: Internal evaluation (generates improvement suggestions, not returned to user).
    Pass 2: Rewrite using evaluation insights (returned as the improved draft).
    """
    logger.info("Self-reflection started (provider=%s, model=%s)", config.AGENT_PROVIDER, _get_model())

    # --- Pass 1: Internal evaluation (not returned to user) ---
    evaluation_prompt = (
        f"Evaluate this outreach draft for professionalism, clarity, persuasion, "
        f"specificity, and business tone. List 2-3 concrete improvement suggestions.\n\n"
        f"Draft:\n{draft}"
    )
    _log_llm_used("self_reflect_pass1_evaluation")
    try:
        eval_response = await get_client().chat.completions.create(
            model=_get_model(),
            messages=[
                {
                    "role": "system",
                    "content": "You are a senior procurement communication reviewer. Provide a brief internal evaluation."
                },
                {
                    "role": "user",
                    "content": evaluation_prompt
                }
            ],
            temperature=config.REFLECTION_TEMPERATURE,
            max_tokens=200
        )
        _log_usage("self_reflect_pass1_evaluation", eval_response)
        evaluation = eval_response.choices[0].message.content.strip()
        logger.info("Self-reflection pass 1 (evaluation) completed")
    except Exception:
        logger.warning("Self-reflection pass 1 failed, proceeding with direct rewrite")
        evaluation = "Improve professionalism, clarity, and business tone."

    # --- Pass 2: Rewrite using evaluation insights ---
    rewrite_content = (
        f"Original task:\n{prompt}\n\n"
        f"Internal evaluation notes:\n{evaluation}\n\n"
        f"Current outreach draft:\n{draft}\n\n"
    )
    if rejection_feedback:
        rewrite_content += (
            f"User rejected the previous draft with feedback:\n"
            f"'{rejection_feedback}'\n\n"
        )
    rewrite_content += (
        "Rewrite the draft into a polished, professional procurement outreach email "
        "that addresses all evaluation points.\n\n"
        "Return ONLY the rewritten email. No commentary, no headings, no analysis."
    )
    _log_llm_used("self_reflect_pass2_rewrite")
    try:
        response = await get_client().chat.completions.create(
            model=_get_model(),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional email writer. "
                        "Return ONLY the final rewritten outreach email. "
                        "Do NOT include any evaluation, commentary, markdown headings, "
                        "bullet points, or analysis text."
                    )
                },
                {
                    "role": "user",
                    "content": rewrite_content
                }
            ],
            temperature=config.REFLECTION_TEMPERATURE,
            max_tokens=150
        )
    except Exception:
        logger.exception("LLM request failed during self-reflection pass 2 (provider=%s)", config.AGENT_PROVIDER)
        raise

    _log_usage("self_reflect_pass2_rewrite", response)
    improved = response.choices[0].message.content.strip()

    # Validate LLM output
    if not improved:
        logger.warning("LLM returned empty self-reflection output")
        raise ValueError("Empty self-reflection output generated by LLM")

    # Defensive sanitization: strip any leaked evaluation/critique preamble
    # Uses regex to detect and remove non-email content before the actual message
    critique_pattern = re.compile(
        r'^.*?(?=(?:Subject:|Dear\s|To\s*:))',
        re.DOTALL | re.IGNORECASE
    )
    match = critique_pattern.match(improved)
    if match:
        preamble = match.group(0).lower()
        critique_keywords = ["evaluation", "professionalism", "critique", "clarity",
                             "persuasion", "assessment", "analysis", "rating", "score"]
        if any(kw in preamble for kw in critique_keywords):
            improved = improved[match.end():]
            logger.info("Sanitized leaked evaluation preamble from LLM output")

    # Strip leading/trailing markdown artifacts (```, ##, etc.)
    improved = re.sub(r'^[`#\-\s]+', '', improved).strip()
    improved = re.sub(r'[`]+$', '', improved).strip()

    # Defensive truncation
    improved = improved[:MAX_OUTPUT_LENGTH]

    logger.info("Self-reflection completed (two-pass)")
    return improved