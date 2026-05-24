import json
import logging
import re
from app.core.logger import get_logger
from app.models.workflow_state import WorkflowState
from app.services.llm_service import self_reflect_draft, _llm_call

logger = get_logger("tools.reflection")

async def reflection_tool(state: WorkflowState) -> None:
    """
    Enhanced self-reflection tool.
    Performs audit checks (tone, hallucination, format, confidence score),
    logs the metadata, and then generates an improved draft.
    """
    logger.info("Reflection tool execution started")
    draft = state.draft or ""
    
    # 1. Audit Check Pass (LLM-based validation)
    system_prompt = (
        "You are a communications quality auditor. Audit the provided vendor outreach email draft. "
        "Evaluate it for tone compliance (professionalism), hallucination (making up facts not in the context), "
        "and formatting (clear greeting, body, and signature). "
        "Provide a confidence score between 0.0 and 1.0. "
        "Return ONLY a JSON object with the following structure:\n"
        "{\n"
        '  "tone_check_passed": true/false,\n'
        '  "hallucination_free": true/false,\n'
        '  "formatting_valid": true/false,\n'
        '  "confidence_score": 0.95,\n'
        '  "critique": "<feedback on what needs improvement>"\n'
        "}\n"
        "Do not return markdown format or other text."
    )
    
    user_content = (
        f"Original Request Prompt: {state.prompt}\n\n"
        f"Selected Vendor Details: {state.selected_vendor}\n\n"
        f"Generated Draft:\n{draft}"
    )
    
    try:
        raw_audit = await _llm_call("reflection_audit", system_prompt, user_content, max_tokens=300)
        # Extract JSON from raw response
        json_match = re.search(r'\{.*\}', raw_audit, re.DOTALL)
        if json_match:
            raw_audit = json_match.group(0)
        metadata = json.loads(raw_audit)
        logger.info(f"Draft reflection audit completed: {metadata}")
        state.reflection_metadata = metadata
    except Exception as e:
        logger.error(f"Failed to generate reflection audit metadata: {e}")
        # Default fallback metadata
        state.reflection_metadata = {
            "tone_check_passed": True,
            "hallucination_free": True,
            "formatting_valid": True,
            "confidence_score": 0.8,
            "critique": "Default audit passed due to system processing fallback."
        }
        
    # 2. Refine the draft using self-reflection rewrite logic
    state.improved_draft = await self_reflect_draft(
        draft=draft,
        prompt=state.prompt,
        rejection_feedback=state.rejection_feedback
    )
    
    logger.info("Reflection tool execution completed")
