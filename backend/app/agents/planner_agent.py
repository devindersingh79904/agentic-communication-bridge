import json
import logging
import re
from typing import Dict, Any, Optional, List

from app.core import config
from app.core.logger import get_logger
from app.models.workflow_models import ExecutionPlan, PlanStep, WorkflowSession
from app.services.llm_service import _llm_call
from app.storage.workflow_repository import workflow_repo

logger = get_logger("agents.planner")

class PlannerAgent:
    def detect_category_rules(self, prompt: str) -> Optional[str]:
        """
        Quick rule-based keyword matching for procurement categories.
        """
        prompt_lower = prompt.lower()
        keywords = {
            "computer": ["computer", "laptop", "pc", "desktop", "server", "hardware", "macbook", "monitor", "screen", "keyboard", "gaming rig", "workstation"],
            "transport": ["transport", "logistics", "delivery", "truck", "van", "courier", "shipping", "bike", "cargo", "movers", "ev hire", "transit"],
            "food": ["food", "catering", "lunch", "meal", "buffet", "snack", "organic", "sweet", "catering", "bites", "beverage", "pastry"],
            "stationery": ["stationery", "paper", "notebook", "pen", "marker", "chair", "stapler", "office supplies", "whiteboard", "easel", "supplies"]
        }
        for cat, kw_list in keywords.items():
            for kw in kw_list:
                if kw in prompt_lower:
                    return cat
        return None

    async def detect_category(self, prompt: str) -> str:
        """
        Classifies the request prompt into a procurement category.
        """
        category = self.detect_category_rules(prompt)
        if category:
            logger.info(f"Planner category detected via rule-based matching: '{category}'")
            return category
            
        logger.info("Category detection: no keywords matched. Falling back to LLM classification.")
        system_prompt = (
            "You are a procurement classification agent. Analyze the user's request and classify "
            "it into exactly one of the following categories: computer, transport, food, stationery. "
            "Return ONLY the category name in lowercase. No other text, no formatting."
        )
        try:
            category_res = await _llm_call("category_detection", system_prompt, prompt, max_tokens=10)
            category_res = category_res.strip().lower()
            if category_res in ["computer", "transport", "food", "stationery"]:
                logger.info(f"Classified prompt category via LLM: '{category_res}'")
                return category_res
        except Exception as e:
            logger.warning(f"Failed to detect category for prompt via LLM: {e}")
        return "computer"

    async def _load_semantic_memory(self, prompt: str) -> str:
        """
        Loads context from recent successful tasks matching the same category keywords
        to serve as semantic preference/memory.
        """
        try:
            recent_tasks = workflow_repo.get_recent_successful_tasks(limit=3)
            if not recent_tasks:
                return "No prior execution history found."
            
            memory_blocks = []
            for task in recent_tasks:
                memory_blocks.append(
                    f"Task Prompt: {task['user_prompt']}\n"
                    f"Selected Vendor: {task['memory'].get('vendor_name', 'None')}\n"
                    f"Final Outcome: {task['final_output'][:150]}..."
                )
            return "\n---\n".join(memory_blocks)
        except Exception as e:
            logger.warning(f"Failed to load semantic memory: {e}")
            return "Failed to load prior history."

    async def generate_plan(self, prompt: str) -> ExecutionPlan:
        """
        Analyzes the task description, detects category, loads memory, and generates
        a dependency-aware JSON execution plan.
        """
        category = await self.detect_category(prompt)
        memory_context = await self._load_semantic_memory(prompt)
        
        logger.info(f"Generating execution plan for category={category} prompt='{prompt[:50]}'")
        
        system_prompt = (
            "You are a staff-level planner agent. Your job is to create an execution plan for a procurement task. "
            "You must select tools from this list and define their execution dependencies to represent a DAG graph:\n"
            "- 'vendor_search': Finds suppliers in internal database or online web indices.\n"
            "- 'pricing_analysis': Compares prices/speed across candidate vendors.\n"
            "- 'draft_outreach': Drafts a procurement outreach email.\n"
            "- 'self_reflection': Runs a self-reflection audit check on the generated draft.\n"
            "- 'execute_outreach': Performs the final send of the email.\n\n"
            "Output must be a raw JSON object of structure:\n"
            "{\n"
            '  "plan": [\n'
            '    {\n'
            '      "step_id": "1",\n'
            '      "tool": "vendor_search",\n'
            '      "reason": "<reasoning for step>",\n'
            '      "depends_on": []\n'
            '    },\n'
            '    {\n'
            '      "step_id": "2",\n'
            '      "tool": "pricing_analysis",\n'
            '      "reason": "<reasoning for step>",\n'
            '      "depends_on": ["1"]\n'
            '    },\n'
            '    ...\n'
            '  ]\n'
            "}\n"
            "Ensure pricing_analysis depends on vendor_search, draft_outreach depends on pricing_analysis, and so on. "
            "Return ONLY the JSON block. No explanation, no formatting code blocks."
        )
        
        user_content = (
            f"Original prompt: {prompt}\n"
            f"Detected Category: {category}\n\n"
            f"Prior Successful History Context (Semantic Memory):\n{memory_context}"
        )
        
        try:
            raw_plan = await _llm_call("generate_plan", system_prompt, user_content, max_tokens=600)
            return self._parse_plan_json(raw_plan)
        except Exception as e:
            logger.error(f"Failed to generate plan via LLM: {e}. Falling back to default plan.")
            return self._get_fallback_plan()

    async def replan(self, session: WorkflowSession, failure_reason: str) -> ExecutionPlan:
        """
        Dynamically adjusts remaining steps when a task execution failure or user rejection occurs.
        """
        logger.info(f"Replanning task {session.task_id} due to: {failure_reason}")
        
        system_prompt = (
            "You are a staff-level replanning agent. Given the original prompt, the current event history, "
            "and the reason for failure/modification request, re-calculate the remaining plan.\n"
            "You can choose to insert steps, clear completed steps, or re-route tools:\n"
            "- 'vendor_search', 'pricing_analysis', 'draft_outreach', 'self_reflection', 'execute_outreach'\n\n"
            "Output MUST be raw JSON object conforming to:\n"
            "{\n"
            '  "plan": [\n'
            '    {\n'
            '      "step_id": "<id>",\n'
            '      "tool": "<tool_name>",\n'
            '      "reason": "<reason>",\n'
            '      "depends_on": [<predecessor ids>]\n'
            '    }\n'
            '  ]\n'
            "}\n"
            "Return ONLY raw JSON, no markdown fences."
        )
        
        user_content = (
            f"Original Task: {session.user_prompt}\n"
            f"Rejection Feedback/Failure Reason: {failure_reason}\n"
            f"Legacy JSON state: {session.workflow_state_json}\n"
            f"Completed Steps: {[s.tool for s in session.execution_plan.plan if s.status == 'completed']}\n"
            f"Current Event History: {json.dumps(session.event_history[-5:])}"
        )
        
        try:
            raw_plan = await _llm_call("replan", system_prompt, user_content, max_tokens=600)
            return self._parse_plan_json(raw_plan)
        except Exception as e:
            logger.error(f"Failed to replan: {e}. Keeping default fallback plan.")
            return self._get_fallback_plan()

    def _parse_plan_json(self, raw_plan: str) -> ExecutionPlan:
        # Regex sanitization to extract JSON
        json_match = re.search(r'\{.*\}', raw_plan, re.DOTALL)
        if json_match:
            raw_plan = json_match.group(0)
            
        data = json.loads(raw_plan)
        steps = []
        for step in data.get("plan", []):
            steps.append(PlanStep(
                step_id=str(step.get("step_id")),
                tool=step.get("tool"),
                reason=step.get("reason"),
                depends_on=[str(d) for d in step.get("depends_on", [])],
                status="pending"
            ))
        return ExecutionPlan(plan=steps)

    def _get_fallback_plan(self) -> ExecutionPlan:
        """
        Returns a default DAG sequential path if LLM planning fails.
        """
        return ExecutionPlan(plan=[
            PlanStep(step_id="1", tool="vendor_search", reason="Locate suppliers", depends_on=[]),
            PlanStep(step_id="2", tool="pricing_analysis", reason="Analyze pricing specs", depends_on=["1"]),
            PlanStep(step_id="3", tool="draft_outreach", reason="Create draft template", depends_on=["2"]),
            PlanStep(step_id="4", tool="self_reflection", reason="Verify draft quality", depends_on=["3"]),
            PlanStep(step_id="5", tool="execute_outreach", reason="Send outreach communication", depends_on=["4"])
        ])

# Singleton instance
planner_agent = PlannerAgent()
