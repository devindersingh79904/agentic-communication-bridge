import logging
import re
import json
from typing import Dict, Any, Optional
from app.models.workflow_state import WorkflowState
from app.core.enums import TaskState
from app.core import config
from app.services.llm_service import _llm_call
from app.tools.vendor_search_tool import vendor_search_tool
from app.tools.external_vendor_search_tool import external_vendor_search_tool
from app.tools.pricing_analysis_tool import pricing_analysis_tool
from app.tools.recommendation_tool import recommendation_tool

logger = logging.getLogger("app.services.agent_planner")

class AgentPlanner:
    def _fallback_vendors_for_category(self, category: str, top_k: int = 3) -> list[Dict[str, Any]]:
        """
        Returns deterministic local vendors when vector/web search produces no candidates.
        This keeps the human approval gate actionable even if embeddings or Chroma are empty.
        """
        try:
            from app.rag.vector_store import SAMPLE_VENDORS
        except Exception as exc:
            logger.warning("Could not load sample vendor fallback data: %s", exc)
            SAMPLE_VENDORS = []

        normalized_category = (category or "computer").lower()
        candidates = [
            {
                **vendor,
                "confidence": vendor.get("confidence", 0.5),
                "source_type": "db",
                "source": "sample_db",
            }
            for vendor in SAMPLE_VENDORS
            if vendor.get("category", "").lower() == normalized_category
        ]

        if not candidates and normalized_category != "computer":
            candidates = [
                {
                    **vendor,
                    "confidence": vendor.get("confidence", 0.5),
                    "source_type": "db",
                    "source": "sample_db",
                }
                for vendor in SAMPLE_VENDORS
                if vendor.get("category", "").lower() == "computer"
            ]

        return candidates[:top_k]



    async def detect_category(self, prompt: str) -> str:
        """
        Classifies the request prompt into a procurement category using the LLM.
        """
        system_prompt = (
            "You are a procurement classification agent. Analyze the user's request and classify "
            "it into exactly one of the following categories: computer, transport, food, stationery, other. "
            "If the request does not fit computer, transport, food, or stationery, return 'other'. "
            "Return ONLY the category name in lowercase. No other text, no formatting."
        )
        try:
            logger.info("Category LLM call prompt=%r", prompt)
            category = await _llm_call("category_detection", system_prompt, prompt, max_tokens=10)
            logger.info("Category LLM raw result=%r", category)
            category = category.strip().lower()
            if category in ["computer", "transport", "food", "stationery", "other"]:
                logger.info("Category selected by LLM: '%s'", category)
                return category
            logger.warning("Category LLM returned unsupported category: %r", category)
        except Exception as e:
            logger.warning(f"Failed to detect category for prompt via LLM: {e}")
        return "computer"

    async def extract_location_context(self, prompt: str) -> Dict[str, Optional[str]]:
        """
        Uses LLM to extract city, locality, and pincode ONLY if they are explicitly
        mentioned in the user prompt. Returns None for any unmentioned field.
        """
        system_prompt = (
            "You are a geography and location extraction assistant. Analyze the user prompt and extract "
            "the target city, locality, and pincode only if explicitly mentioned. "
            "Return a raw JSON object of structure:\n"
            "{\n"
            '  "city": "<extracted city or null>",\n'
            '  "locality": "<extracted locality or null>",\n'
            '  "pincode": "<extracted pincode or null>"\n'
            "}\n"
            "Return ONLY the raw JSON object. No explanation, no markdown formatting."
        )
        try:
            res = await _llm_call("extract_location", system_prompt, prompt, max_tokens=150)
            json_match = re.search(r'\{.*\}', res, re.DOTALL)
            if json_match:
                res = json_match.group(0)
            data = json.loads(res)
            return {
                "city": data.get("city") if data.get("city") and str(data.get("city")).lower() != "null" else None,
                "locality": data.get("locality") if data.get("locality") and str(data.get("locality")).lower() != "null" else None,
                "pincode": data.get("pincode") if data.get("pincode") and str(data.get("pincode")).lower() != "null" else None
            }
        except Exception as e:
            logger.warning(f"Failed to extract location context via LLM: {e}")
            return {
                "city": None,
                "locality": None,
                "pincode": None
            }



    async def run_research(self, state: WorkflowState, task_id: str, transition_callback) -> None:
        """
        Executes internal RAG search. If confidence is low or query explicitly asks for web search,
        transitions to EXTERNAL_SEARCHING and triggers the external vendor search tool.
        """
        logger.info(f"Planner starting research step for task {task_id}")
        
        # 1. Extract location context if not already set
        if not state.constraints.get("location_extracted"):
            extracted_loc = await self.extract_location_context(state.prompt)
            state.constraints.update({
                "city": extracted_loc.get("city"),
                "locality": extracted_loc.get("locality"),
                "pincode": extracted_loc.get("pincode"),
                "location_extracted": True
            })
            logger.info(f"Extracted location context: city={extracted_loc.get('city')}, locality={extracted_loc.get('locality')}, pincode={extracted_loc.get('pincode')}")

        city = state.constraints.get("city") or config.DEFAULT_CITY
        locality = state.constraints.get("locality") or config.DEFAULT_LOCALITY
        pincode = state.constraints.get("pincode") or config.DEFAULT_PINCODE

        location_explicitly_provided = any([
            state.constraints.get("city") is not None,
            state.constraints.get("locality") is not None,
            state.constraints.get("pincode") is not None
        ])

        # 2. Refine query and detect category
        search_query = state.prompt
        if state.rejection_feedback:
            logger.info(f"Refining search query using rejection feedback: '{state.rejection_feedback}'")
            system_prompt = (
                "You are a search query refinement agent. Given the original user request and the feedback on the rejected results, "
                "generate a refined search query to locate better vendors. Return ONLY the refined search query string. "
                "Keep it concise (no extra explanation, no quotes)."
            )
            user_content = f"Original Query: {state.prompt}\nRejection Feedback: {state.rejection_feedback}"
            try:
                refined = await _llm_call("refine_search_query", system_prompt, user_content, max_tokens=100)
                search_query = refined.strip()
                logger.info(f"Refined search query: '{search_query}'")
            except Exception as e:
                logger.warning(f"Failed to refine query via LLM: {e}")

        category = await self.detect_category(search_query)
        rag_category = None if category == "other" else category

        # Formulate RAG search query (include location context if explicitly provided)
        rag_search_query = search_query
        if location_explicitly_provided:
            loc_parts = []
            if state.constraints.get("locality"):
                loc_parts.append(state.constraints.get("locality"))
            if state.constraints.get("city"):
                loc_parts.append(state.constraints.get("city"))
            if state.constraints.get("pincode"):
                loc_parts.append(state.constraints.get("pincode"))
            rag_search_query = f"{search_query} in " + ", ".join(loc_parts)
            logger.info(f"Using location-aware RAG search query: '{rag_search_query}'")
        
        # 3. Query Internal RAG
        internal_results = await vendor_search_tool(query=rag_search_query, category=rag_category)
        internal_vendors = internal_results.get("vendors", [])
        confidence = internal_results.get("confidence", 0.0)
        state.internal_rag_confidence = confidence
        
        # 4. Decision: Trigger external search?
        # Triggered if confidence is low (< 0.7) OR if user explicitly asks for external/web search OR if location was explicitly provided
        user_explicit = any(word in search_query.lower() for word in ["external", "web", "online", "internet", "google", "search"])
        trigger_external = (confidence < 0.7) or user_explicit or location_explicitly_provided
        
        external_vendors = []
        if trigger_external:
            logger.info(f"Planner decision: Triggering external search (confidence={confidence:.2f}, user_explicit={user_explicit}, location_explicit={location_explicitly_provided}).")
            # Transition state to EXTERNAL_SEARCHING via callback
            await transition_callback(TaskState.EXTERNAL_SEARCHING)
            
            # Run external vendor search tool with dynamic location parameters
            external_results = await external_vendor_search_tool(
                query=search_query,
                category=category,
                city=city,
                locality=locality,
                pincode=pincode
            )
            external_vendors = external_results.get("vendors", [])
            
            # Transition state back to RUNNING
            await transition_callback(TaskState.RUNNING)
            
        all_vendors = internal_vendors + external_vendors
        if state.rejected_vendors:
            rejected_set = set(state.rejected_vendors)
            logger.info(f"Filtering out previously rejected vendors: {rejected_set}")
            all_vendors = [
                v for v in all_vendors 
                if (v.get("vendor_name") or v.get("name")) not in rejected_set
            ]
            
        if not all_vendors:
            logger.warning(
                "Planner research returned no vendors for category '%s'. Applying local sample fallback.",
                category,
            )
            fallback = self._fallback_vendors_for_category(category, top_k=15)
            if state.rejected_vendors:
                rejected_set = set(state.rejected_vendors)
                fallback = [
                    v for v in fallback 
                    if (v.get("vendor_name") or v.get("name")) not in rejected_set
                ]
            all_vendors = fallback[:3]
        
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
        vendors = state.selected_vendors or (state.research_data.get("vendors", []) if state.research_data else [])
        if not vendors:
            # Fallback to empty if none found
            state.analysis_summary = "No candidate vendors to analyze."
            state.selected_vendor = None
            return
            
        analysis_result = await pricing_analysis_tool(query=state.prompt, vendors=vendors)
        state.analysis_summary = analysis_result.get("analysis_summary", "")

        if len(state.selected_vendors) > 1:
            state.selected_vendor = analysis_result.get("recommended_vendor", state.selected_vendors[0])
            vendor_name = (state.selected_vendor.get('vendor_name') or state.selected_vendor.get('name')) if state.selected_vendor else 'None'
            logger.info(f"Pricing analysis recommended vendor from user-selected candidates: {vendor_name}")
        elif not state.selected_vendor:
            state.selected_vendor = analysis_result.get("recommended_vendor", None)
            vendor_name = (state.selected_vendor.get('vendor_name') or state.selected_vendor.get('name')) if state.selected_vendor else 'None'
            logger.info(f"Pricing analysis recommended vendor: {vendor_name}")
        else:
            # User already selected a vendor, keep it
            user_selected = state.selected_vendor.get('vendor_name') or state.selected_vendor.get('name')
            logger.info(f"🔒 Keeping user-selected vendor (NOT overwriting with pricing recommendation): {user_selected}")

    async def run_draft(self, state: WorkflowState) -> None:
        """
        Generates outreach proposal/draft targeting the selected vendor.
        Incorporates user feedback from Step 1 vendor selection to guide tone/content.
        """
        logger.info("Planner starting draft generation step")
        if not state.selected_vendor:
            state.draft = "No vendor selected to draft outreach for."
            return

        # If user provided feedback at any approval gate, use it to guide the draft.
        user_feedback = state.rejection_feedback if state.rejection_feedback else None
        evaluator_feedback = state.constraints.get("evaluator_feedback") if state.constraints else None
        if user_feedback and evaluator_feedback:
            user_feedback = (
                f"{user_feedback}\n\n"
                f"Additional quality corrections to satisfy without overriding user feedback: {evaluator_feedback}"
            )
        if user_feedback:
            logger.info(f"📝 Using user feedback to guide draft generation: {user_feedback}")

        # Pass memory_context to enforce vendor constraint in draft generation
        rec_result = await recommendation_tool(
            query=state.prompt,
            selected_vendor=state.selected_vendor,
            analysis_summary=state.analysis_summary or "",
            user_feedback=user_feedback,
            memory_context=state.memory_context
        )
        state.draft = rec_result.get("draft", "")
        logger.info("Planner completed draft generation")

# Singleton instance
planner = AgentPlanner()
