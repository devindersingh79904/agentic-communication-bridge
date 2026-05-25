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

    def classify_rejection_feedback(self, feedback: str) -> str:
        """
        Classifies rejection feedback to determine whether to rerun 'vendor_search' or 'draft_outreach'.
        """
        feedback_lower = feedback.lower()
        
        # Vendor-related cues
        vendor_keywords = [
            "expensive", "cheaper", "cost", "price", "vendor", "supplier", 
            "delivery", "location", "local", "bangalore", "mumbai", "delhi", 
            "city", "cheapest", "budget", "near"
        ]
        if any(kw in feedback_lower for kw in vendor_keywords):
            return "vendor_search"
            
        # Draft-related cues
        draft_keywords = [
            "tone", "aggressive", "professional", "rewrite", "email", "outreach", 
            "shorter", "longer", "subject", "wording", "text", "message", "grammar"
        ]
        if any(kw in feedback_lower for kw in draft_keywords):
            return "draft_outreach"
            
        # Default fallback
        return "draft_outreach"

    async def decide_next_action(self, state: WorkflowState) -> Dict[str, Any]:
        """
        Agentic Decision Engine: Dynamically determines the next action based on current state,
        constraints, and user feedback history.
        """
        import json
        import re
        from app.core.enums import TaskState, ApprovalAction
        
        logger.info(f"Decision Engine: Evaluating next action. Current state step: {state.current_step}")
        
        # Rule-based fast paths to avoid unnecessary LLM latency for standard state transitions
        if state.current_step in (TaskState.SCHEDULED, TaskState.RUNNING):
            return {
                "next_action": "vendor_search",
                "reason": "Starting vendor research.",
                "parameters": {}
            }
            
        if state.current_step == TaskState.SEARCHING_VENDORS:
            return {
                "next_action": "pricing_analysis",
                "reason": "Vendor search completed. Proceeding autonomously to pricing analysis.",
                "parameters": {}
            }
            
        if state.current_step == TaskState.ANALYZING_PRICING:
            return {
                "next_action": "draft_outreach",
                "reason": "Pricing analysis completed. Proceeding autonomously to outreach drafting.",
                "parameters": {}
            }
            
        # After drafting outreach, we always run self-reflection immediately
        if state.current_step == TaskState.DRAFTING_OUTREACH:
            return {
                "next_action": "self_reflection",
                "reason": "Outreach draft generated. Initiating self-reflection audit.",
                "parameters": {}
            }
            
        # After self-reflection completes
        if state.current_step == TaskState.SELF_REFLECTION:
            from app.core import config
            reflection = state.reflection_metadata or {}
            score_passed = reflection.get("tone_check_passed", True) and reflection.get("hallucination_free", True) and reflection.get("formatting_valid", True)
            
            if not score_passed and state.regeneration_count < config.MAX_REGENERATION_ATTEMPTS:
                state.regeneration_count += 1
                state.rejection_feedback = f"Self-reflection critique: {reflection.get('critique', 'Polishing needed.')}"
                return {
                    "next_action": "draft_outreach",
                    "reason": f"Self-reflection quality checks failed. Regenerating draft (Attempt {state.regeneration_count}/{config.MAX_REGENERATION_ATTEMPTS}).",
                    "parameters": {}
                }
                
            from app.core import config as system_config
            if system_config.AUTO_APPROVE:
                return {
                    "next_action": "execute_outreach",
                    "reason": "Auto-approve enabled. Proceeding to outreach execution.",
                    "parameters": {}
                }
            else:
                return {
                    "next_action": "wait_for_human",
                    "reason": "Awaiting human final approval of the outreach proposal.",
                    "parameters": {"step": "final_approval"}
                }
                
        # If waiting for final approval
        if state.current_step == TaskState.WAITING_FINAL_APPROVAL:
            if state.approval_action == ApprovalAction.APPROVE:
                state.approval_action = None
                state.rejection_feedback = None
                return {
                    "next_action": "execute_outreach",
                    "reason": "Outreach proposal approved. Proceeding to final execution.",
                    "parameters": {}
                }
            elif state.approval_action == ApprovalAction.REJECT:
                feedback = state.rejection_feedback or ""
                next_act = self.classify_rejection_feedback(feedback)
                
                # Clear appropriate downstream states depending on what is being re-run
                if next_act == "vendor_search":
                    state.research_data = None
                    state.analysis_summary = None
                    state.selected_vendor = None
                    state.draft = None
                    state.improved_draft = None
                    state.selected_vendors = []
                    state.reflection_metadata = None
                    reason = f"Re-running vendor search based on feedback: '{feedback}'"
                else:
                    state.draft = None
                    state.improved_draft = None
                    state.reflection_metadata = None
                    reason = f"Re-running draft outreach based on feedback: '{feedback}'"
                    
                if feedback:
                    state.feedback_history.append(feedback)
                    
                # Reset approval actions/feedback before rerunning
                state.approval_action = None
                state.rejection_feedback = None
                
                return {
                    "next_action": next_act,
                    "reason": reason,
                    "parameters": {}
                }
            else:
                return {
                    "next_action": "wait_for_human",
                    "reason": "Still waiting for human final approval.",
                    "parameters": {"step": "final_approval"}
                }
                
        if state.current_step == TaskState.COMPLETED or state.execution_result:
            return {
                "next_action": "complete",
                "reason": "Task successfully executed and completed.",
                "parameters": {}
            }

        # --- LLM Dynamic Replanning Fallback ---
        logger.info("Decision Engine: Invoking LLM Planner to evaluate feedback and replan.")
        
        system_prompt = (
            "You are the Core Decision Engine/Planner of a human-in-the-loop procurement agent.\n"
            "Given the user's original prompt, current constraints, candidate vendors, selected vendor, "
            "feedback history, and the latest user feedback/rejection action, decide the best next action.\n\n"
            "Available Actions:\n"
            "- 'vendor_search': Re-run semantic RAG vendor search with updated constraints.\n"
            "- 'pricing_analysis': Select/analyze vendor pricing based on selected vendors.\n"
            "- 'draft_outreach': Re-generate outreach email using feedback details.\n"
            "- 'self_reflection': Re-run self-reflection check on draft.\n"
            "- 'wait_for_human': Pause and request human input (params: 'step': 'final_approval').\n\n"
            "Return ONLY a JSON object of this structure:\n"
            "{\n"
            '  "next_action": "vendor_search" | "pricing_analysis" | "draft_outreach" | "wait_for_human",\n'
            '  "reason": "<detailed rationale for this decision>",\n'
            '  "parameters": {\n'
            '     "step": "<if wait_for_human, specifies step>",\n'
            '     "updated_constraints": {"budget": "low/high", "delivery": "fast", "location": "<preferred city/locality>"}\n'
            '  }\n'
            "}\n"
            "Do not output markdown code fences or any other text."
        )
        
        user_content = (
            f"Original Prompt: {state.prompt}\n"
            f"Current Step: {state.current_step.value}\n"
            f"Current Constraints: {json.dumps(state.constraints)}\n"
            f"Feedback History: {json.dumps(state.feedback_history)}\n"
            f"Latest Feedback: {state.rejection_feedback}\n"
            f"Latest Action: {state.approval_action.value if state.approval_action else 'None'}\n"
            f"Discovered Vendors: {json.dumps(state.research_data.get('vendors') if state.research_data else [])}\n"
            f"Selected Vendor: {json.dumps(state.selected_vendor)}\n"
            f"Outreach Draft: {state.improved_draft or state.draft}\n\n"
            f"Evaluate the feedback. If the user wants different options, cheaper price, faster delivery, or a specific city, "
            f"output 'vendor_search' and specify the 'updated_constraints'. If they want to modify the draft wording, "
            f"output 'draft_outreach'."
        )
        
        try:
            raw_decision = await _llm_call("planner_decide_next_action", system_prompt, user_content, max_tokens=300)
            json_match = re.search(r'\{.*\}', raw_decision, re.DOTALL)
            if json_match:
                raw_decision = json_match.group(0)
            decision = json.loads(raw_decision)
            
            params = decision.get("parameters", {})
            if "updated_constraints" in params:
                state.constraints.update(params["updated_constraints"])
                logger.info(f"Decision Engine updated constraints: {state.constraints}")
                
            return decision
        except Exception as e:
            logger.error(f"Decision Engine: LLM call failed, falling back to safe recovery transition: {e}")
            return {"next_action": "draft_outreach", "reason": "Fallback: rewrite outreach.", "parameters": {}}

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
            
            # Transition state back to SEARCHING_VENDORS
            await transition_callback(TaskState.SEARCHING_VENDORS)
            
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
