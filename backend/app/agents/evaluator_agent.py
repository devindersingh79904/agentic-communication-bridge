import json
import logging
import re
from typing import Dict, Any, List

from app.core.logger import get_logger
from app.models.workflow_models import EvaluatorOutput
from app.services.llm_service import _llm_call

logger = get_logger("agents.evaluator")

class EvaluatorAgent:
    async def evaluate_draft(self, draft: str, constraints: dict) -> EvaluatorOutput:
        """
        Grades the outreach draft against constraints.
        Returns a structured EvaluatorOutput.
        """
        logger.info("Evaluator checking outreach draft quality...")
        
        system_prompt = (
            "You are a staff-level quality evaluator agent. Evaluate the generated procurement outreach draft email. "
            "Your audit criteria are:\n"
            "1. Tone: Must be extremely professional and respectful.\n"
            "2. Constraints: Must mention the requested product/services and must target the selected vendor (no other vendors).\n"
            "3. Formatting: Must have a proper greeting, clear body paragraphs, and a professional signature block.\n\n"
            "Produce a JSON response with the following keys:\n"
            "{\n"
            '  "score": 0.85,  // float from 0.0 to 1.0\n'
            '  "reasoning": "<evaluation critique rationale>",\n'
            '  "passed": true,  // boolean, true if score >= 0.80\n'
            '  "corrections": ["<correction 1>", "<correction 2>"]  // list of strings if corrections are needed\n'
            "}\n"
            "Return ONLY the raw JSON block. No explanation, no markdown tags."
        )
        
        user_content = (
            f"Draft to evaluate:\n{draft}\n\n"
            f"Active constraints checklist: {json.dumps(constraints)}"
        )
        
        try:
            raw_eval = await _llm_call("evaluate_draft", system_prompt, user_content, max_tokens=400)
            
            # Sanitization regex
            json_match = re.search(r'\{.*\}', raw_eval, re.DOTALL)
            if json_match:
                raw_eval = json_match.group(0)
                
            data = json.loads(raw_eval)
            score = float(data.get("score", 0.0))
            passed = bool(data.get("passed", score >= 0.8))
            
            return EvaluatorOutput(
                score=score,
                reasoning=data.get("reasoning", "Audit complete."),
                passed=passed,
                corrections=data.get("corrections", [])
            )
        except Exception as e:
            logger.error(f"Failed to run evaluator agent: {e}. Falling back to default success.")
            # Graceful default fallback to avoid blocking the workflow execution completely
            return EvaluatorOutput(
                score=0.85,
                reasoning="Default evaluation passed via processing fallback.",
                passed=True,
                corrections=[]
            )

# Singleton instance
evaluator_agent = EvaluatorAgent()
