import logging
from typing import Dict, Any, Optional
from app.models.workflow_state import WorkflowState
from app.core.enums import TaskState
from app.services.llm_service import _llm_call
from app.tools.vendor_search_tool import vendor_search_tool
from app.tools.external_vendor_search_tool import external_vendor_search_tool
from app.tools.pricing_analysis_tool import pricing_analysis_tool
from app.tools.recommendation_tool import recommendation_tool
from app.tools.reflection_tool import reflection_tool

logger = logging.getLogger("app.services.agent_planner")

class AgentPlanner:
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
        Uses rule-based classification first, then falls back to LLM.
        """
        # 1. Rule-based check
        category = self.detect_category_rules(prompt)
        if category:
            logger.info(f"Category detected via rule-based matching: '{category}'")
            return category
            
        # 2. LLM Fallback
        logger.info("Category detection: no keywords matched. Falling back to LLM.")
        system_prompt = (
            "You are a procurement classification agent. Analyze the user's request and classify "
            "it into exactly one of the following categories: computer, transport, food, stationery. "
            "Return ONLY the category name in lowercase. No other text, no formatting."
        )
        try:
            category = await _llm_call("category_detection", system_prompt, prompt, max_tokens=10)
            category = category.strip().lower()
            if category in ["computer", "transport", "food", "stationery"]:
                logger.info(f"Classified prompt category via LLM as: '{category}'")
                return category
        except Exception as e:
            logger.warning(f"Failed to detect category for prompt via LLM: {e}")
        return "computer"

    async def run_research(self, state: WorkflowState, task_id: str, transition_callback) -> None:
        """
        Executes internal RAG search. If confidence is low or query explicitly asks for web search,
        transitions to EXTERNAL_SEARCHING and triggers the external vendor search tool.
        """
        logger.info(f"Planner starting research step for task {task_id}")
        
        # 1. Detect Category
        category = await self.detect_category(state.prompt)
        
        # 2. Query Internal RAG
        internal_results = await vendor_search_tool(query=state.prompt, category=category)
        internal_vendors = internal_results.get("vendors", [])
        confidence = internal_results.get("confidence", 0.0)
        state.internal_rag_confidence = confidence
        
        # 3. Decision: Trigger external search?
        # Triggered if confidence is low (< 0.7) OR if user explicitly asks for external/web search
        user_explicit = any(word in state.prompt.lower() for word in ["external", "web", "online", "internet", "google", "search"])
        
        external_vendors = []
        if confidence < 0.7 or user_explicit:
            logger.info(f"Planner decision: Internal RAG confidence {confidence:.2f} is low or explicit web search requested. Triggering external search.")
            # Transition state to EXTERNAL_SEARCHING via callback
            await transition_callback(TaskState.EXTERNAL_SEARCHING)
            
            # Run external vendor search tool
            external_results = await external_vendor_search_tool(query=state.prompt, category=category)
            external_vendors = external_results.get("vendors", [])
            
            # Transition state back to RUNNING
            await transition_callback(TaskState.RUNNING)
            
        all_vendors = internal_vendors + external_vendors
        
        # Aggregate results back to workflow state
        state.research_data = {
            "category": category,
            "vendors": all_vendors,
            "internal_confidence": confidence,
            "external_search_triggered": len(external_vendors) > 0,
            "market_insights": f"Semantic analysis matched category '{category}'. Found {len(internal_vendors)} local vendor catalogs and {len(external_vendors)} web search matches."
        }
        logger.info(f"Planner completed research step. Found {len(all_vendors)} total vendors.")

    async def run_analysis(self, state: WorkflowState) -> None:
        """
        Orchestrates vendor pricing/delivery comparison using the pricing analysis tool.
        """
        logger.info("Planner starting pricing analysis step")
        vendors = state.research_data.get("vendors", []) if state.research_data else []
        if not vendors:
            # Fallback to empty if none found
            state.analysis_summary = "No candidate vendors to analyze."
            state.selected_vendor = None
            return
            
        analysis_result = await pricing_analysis_tool(query=state.prompt, vendors=vendors)
        state.analysis_summary = analysis_result.get("analysis_summary", "")
        state.selected_vendor = analysis_result.get("recommended_vendor", None)
        logger.info(f"Planner selected vendor: {state.selected_vendor.get('vendor_name') if state.selected_vendor else 'None'}")

    async def run_draft(self, state: WorkflowState) -> None:
        """
        Generates outreach proposal/draft targeting the selected vendor.
        """
        logger.info("Planner starting draft generation step")
        if not state.selected_vendor:
            state.draft = "No vendor selected to draft outreach for."
            return
            
        rec_result = await recommendation_tool(
            query=state.prompt,
            selected_vendor=state.selected_vendor,
            analysis_summary=state.analysis_summary or ""
        )
        state.draft = rec_result.get("draft", "")
        logger.info("Planner completed draft generation")

    async def run_reflection(self, state: WorkflowState) -> None:
        """
        Runs quality audits (reflection) and produces improved draft.
        """
        logger.info("Planner starting self reflection step")
        await reflection_tool(state)
        logger.info("Planner completed self reflection")

# Singleton instance
planner = AgentPlanner()
