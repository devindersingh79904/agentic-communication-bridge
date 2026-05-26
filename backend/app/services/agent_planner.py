import logging
import re
import json
from typing import Dict, Any, Optional
from app.models.workflow_state import WorkflowState
from app.core.enums import TaskState
from app.core import config
from app.services.llm_service import _llm_call
from app.utils.time import utc_now_iso
from app.tools.vendor_search_tool import vendor_search_tool
from app.tools.external_vendor_search_tool import external_vendor_search_tool
from app.tools.pricing_analysis_tool import pricing_analysis_tool
from app.tools.recommendation_tool import recommendation_tool
from app.tools.reflection_tool import reflection_tool

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

    async def extract_selected_vendor_semantic(self, feedback: str, available_vendors: list) -> Optional[Dict[str, Any]]:
        """
        Uses LLM to semantically understand which vendor the user wants from their feedback.
        Handles natural language like "proceed with", "use", "go with", "prefer", etc.
        Returns the selected vendor object or None if no vendor mentioned.
        """
        if not feedback or not available_vendors:
            return None

        vendor_list_str = "\n".join([f"- {v.get('vendor_name') or v.get('name')}" for v in available_vendors])

        system_prompt = (
            "You are a semantic understanding assistant. Given user feedback and a list of vendors, "
            "determine which vendor (if any) the user wants to select.\n\n"
            "The user may say things like:\n"
            '- "proceed with X vendor"\n'
            '- "use Y company"\n'
            '- "prefer Z"\n'
            '- "go with X"\n'
            '- etc.\n\n'
            "If the user clearly indicates a vendor preference, respond with ONLY the exact vendor name from the list.\n"
            "If no vendor is mentioned or preference is unclear, respond with NONE."
        )

        user_content = (
            f"Available vendors:\n{vendor_list_str}\n\n"
            f"User feedback: {feedback}\n\n"
            f"Which vendor does the user want? Respond with the exact vendor name or NONE."
        )

        try:
            response = await _llm_call(
                "extract_vendor_semantic",
                system_prompt,
                user_content,
                max_tokens=50
            )
            selected_vendor_name = response.strip()
            logger.info(f"LLM response for vendor extraction: '{selected_vendor_name}'")

            if selected_vendor_name.upper() == "NONE":
                logger.info(f"No vendor extracted from feedback: {feedback}")
                return None

            # Find matching vendor by name (exact match first)
            selected_vendor_name_lower = selected_vendor_name.lower()
            for vendor in available_vendors:
                vendor_name = vendor.get('vendor_name') or vendor.get('name')
                if vendor_name and vendor_name.lower() == selected_vendor_name_lower:
                    logger.info(f"✅ LLM extracted vendor (exact match): {vendor_name}")
                    return vendor

            # Fallback: partial/fuzzy match if exact match fails
            logger.info(f"No exact match for '{selected_vendor_name}', trying fuzzy match...")
            for vendor in available_vendors:
                vendor_name = vendor.get('vendor_name') or vendor.get('name')
                if vendor_name:
                    # Check if vendor name appears in LLM response or vice versa
                    if (selected_vendor_name_lower in vendor_name.lower() or
                        vendor_name.lower() in selected_vendor_name_lower):
                        logger.info(f"✅ LLM extracted vendor (fuzzy match): {vendor_name}")
                        return vendor

            # If still no match, log all available vendors for debugging
            logger.warning(f"LLM returned '{selected_vendor_name}' but not in list")
            logger.warning(f"Available vendors: {[v.get('vendor_name') or v.get('name') for v in available_vendors]}")
            return None

        except Exception as e:
            logger.warning(f"Failed to extract vendor semantically: {e}")
            return None

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

    async def decide_next_action(self, state: WorkflowState) -> Dict[str, Any]:
        """
        Agentic Decision Engine: Dynamically determines the next action based on current state,
        constraints, and user feedback history.
        """
        import json
        import re
        from app.core.enums import TaskState, ApprovalAction

        logger.info(f"Decision Engine: Evaluating next action. Current state step: {state.current_step}")
        logger.info(f"Decision Engine: approval_action={state.approval_action}, selected_vendors={state.selected_vendors}, rejection_feedback={state.rejection_feedback}")
        
        # Rule-based fast paths to avoid unnecessary LLM latency for standard state transitions
        if state.current_step == TaskState.SCHEDULED:
            return {
                "next_action": "vendor_search",
                "reason": "Task scheduled, starting vendor research.",
                "parameters": {}
            }

        # If we're in RUNNING state, check what to do based on prior progress
        if state.current_step == TaskState.RUNNING:
            # No vendor data — run search (fresh start or after rejection cleared research_data)
            if not state.research_data:
                return {
                    "next_action": "vendor_search",
                    "reason": "No vendor data available. Starting vendor research.",
                    "parameters": {}
                }
            # If we have vendors but no pricing analysis, check if user already selected a vendor
            elif state.research_data and not state.analysis_summary:
                # If we already have a draft (vendor was user-selected, pricing skipped), move to reflection
                if state.draft:
                    if not state.reflection_metadata:
                        return {
                            "next_action": "self_reflection",
                            "reason": "Draft exists without analysis_summary (vendor user-selected). Proceeding to reflection.",
                            "parameters": {}
                        }
                # If user already selected a vendor, skip pricing analysis and go to drafting
                elif state.selected_vendor:
                    vendor_name = state.selected_vendor.get('vendor_name') or state.selected_vendor.get('name', 'Selected Vendor')
                    logger.info(f"User previously selected vendor: {vendor_name}. Skipping pricing analysis.")
                    # Set analysis_summary now to prevent re-entering this branch on next iteration
                    state.analysis_summary = f"User selected {vendor_name} from available options. This vendor meets procurement requirements."
                    return {
                        "next_action": "draft_outreach",
                        "reason": "User selected vendor. Skipping pricing analysis and proceeding to drafting.",
                        "parameters": {}
                    }
                else:
                    # No vendor selected yet, run pricing analysis
                    return {
                        "next_action": "pricing_analysis",
                        "reason": "Vendor search completed. Proceeding to pricing analysis to select best option.",
                        "parameters": {}
                    }
            # If we have pricing analysis but no draft, proceed to drafting
            elif state.analysis_summary and not state.draft:
                return {
                    "next_action": "draft_outreach",
                    "reason": "Pricing analysis complete. Proceeding to outreach drafting.",
                    "parameters": {}
                }
            # If we have draft but no reflection, proceed to reflection
            elif state.draft and not state.reflection_metadata:
                return {
                    "next_action": "self_reflection",
                    "reason": "Outreach draft complete. Running self-reflection audit.",
                    "parameters": {}
                }
            # If we have reflection but haven't executed, proceed to execution
            elif state.reflection_metadata and not state.execution_result:
                return {
                    "next_action": "execute_outreach",
                    "reason": "Final approval received. Proceeding to execution.",
                    "parameters": {}
                }

        # If we just completed searching vendors and AUTO_APPROVE is false, we must wait for selection
        if state.current_step == TaskState.SEARCHING_VENDORS:
            from app.core import config
            if config.AUTO_APPROVE:
                vendors = state.research_data.get("vendors", []) if state.research_data else []
                state.selected_vendors = vendors
                return {
                    "next_action": "pricing_analysis",
                    "reason": "Auto-approve is enabled. Proceeding directly to pricing analysis.",
                    "parameters": {}
                }
            else:
                return {
                    "next_action": "wait_for_human",
                    "reason": "Awaiting human vendor selection and feedback.",
                    "parameters": {"step": "vendor_selection"}
                }
                
        # If we are waiting for vendor selection and received approval/selection
        if state.current_step == TaskState.WAITING_VENDOR_SELECTION:
            if state.approval_action == ApprovalAction.APPROVE:
                # Try to extract vendor semantically from user feedback
                selected_vendor_from_feedback = None
                if state.rejection_feedback and state.selected_vendors:
                    # Use LLM to understand which vendor user wants from natural language feedback
                    selected_vendor_from_feedback = await self.extract_selected_vendor_semantic(
                        state.rejection_feedback,
                        state.selected_vendors
                    )
                    if selected_vendor_from_feedback:
                        logger.info(f"LLM extracted vendor from feedback: {selected_vendor_from_feedback.get('vendor_name') or selected_vendor_from_feedback.get('name')}")

                # If user selected a specific vendor (from feedback or explicit selection), use it and SKIP pricing analysis
                selected = selected_vendor_from_feedback or (state.selected_vendors[0] if state.selected_vendors else None)

                if selected:
                    state.selected_vendor = selected
                    vendor_name = state.selected_vendor.get('vendor_name') or state.selected_vendor.get('name')
                    logger.info(f"User selected vendor (SKIPPING pricing analysis): {vendor_name}")

                    # Populate analysis_summary so draft has context
                    if not state.analysis_summary:
                        state.analysis_summary = f"User selected {vendor_name} from available options. This vendor meets procurement requirements."

                    # IMPORTANT: Skip pricing_analysis and go directly to drafting
                    return {
                        "next_action": "draft_outreach",
                        "reason": f"User selected {vendor_name}. Skipping pricing analysis and proceeding directly to draft.",
                        "parameters": {}
                    }
                else:
                    # User just approved vendors without specific selection - analyze pricing
                    logger.info("User approved vendor list. Running pricing analysis to select best option.")
                    return {
                        "next_action": "pricing_analysis",
                        "reason": "Human approved vendor list. Running analysis to select best option.",
                        "parameters": {}
                    }
            elif state.approval_action in (ApprovalAction.REJECT, ApprovalAction.MODIFY_REQUEST):
                # User rejected vendors - search again with updated constraints based on feedback
                logger.info(f"🔄 REJECT detected! User rejected vendors with feedback: {state.rejection_feedback}")
                state.rejection_feedback = state.rejection_feedback or "Vendors not acceptable. Search for alternatives."
                state.feedback_history.append({
                    "action": "REJECT_VENDORS",
                    "feedback": state.rejection_feedback,
                    "timestamp": utc_now_iso()
                })
                logger.info(f"🔄 TRIGGERING RE-SEARCH with feedback: {state.rejection_feedback}")
                return {
                    "next_action": "vendor_search",
                    "reason": f"Human rejected vendor selection. Re-searching with feedback: {state.rejection_feedback}",
                    "parameters": {"updated_constraints": {"feedback": state.rejection_feedback}}
                }
            else:
                return {
                    "next_action": "wait_for_human",
                    "reason": "Still waiting for human vendor selection.",
                    "parameters": {"step": "vendor_selection"}
                }
                
        # If we completed pricing analysis — proceed directly to drafting (no user gate)
        if state.current_step == TaskState.ANALYZING_PRICING:
            return {
                "next_action": "draft_outreach",
                "reason": "Pricing analysis complete. Proceeding to outreach drafting.",
                "parameters": {}
            }

        # WAITING_PRICE_APPROVAL is no longer a user gate — auto-proceed
        if state.current_step == TaskState.WAITING_PRICE_APPROVAL:
            return {
                "next_action": "draft_outreach",
                "reason": "Pricing stage auto-approved. Proceeding to drafting.",
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

            if config.AUTO_APPROVE:
                logger.info("AUTO_APPROVE enabled - skipping final approval gate")
                return {
                    "next_action": "execute_outreach",
                    "reason": "Auto-approve enabled. Proceeding to outreach execution.",
                    "parameters": {}
                }
            else:
                logger.info("🔔 FINAL APPROVAL GATE: Waiting for human to approve/reject draft")
                return {
                    "next_action": "wait_for_human",
                    "reason": "Awaiting human final approval of the outreach proposal.",
                    "parameters": {"step": "final_approval"}
                }
                
        # If waiting for final approval
        if state.current_step == TaskState.WAITING_FINAL_APPROVAL:
            from app.core import config

            if state.approval_action == ApprovalAction.APPROVE:
                return {
                    "next_action": "execute_outreach",
                    "reason": "Outreach proposal approved. Proceeding to final execution.",
                    "parameters": {}
                }
            elif state.approval_action in (ApprovalAction.REJECT, ApprovalAction.MODIFY_REQUEST):
                # User rejected final draft — always regenerate with feedback (no attempt limit)
                logger.info(f"🔄 User rejected final draft with feedback: {state.rejection_feedback}")
                state.regeneration_count = 0  # Reset so self-reflection quality loop runs fresh
                return {
                    "next_action": "draft_outreach",
                    "reason": f"User rejected draft. Regenerating with feedback: {state.rejection_feedback}",
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
            "- 'wait_for_human': Pause and request human input (params: 'step': 'vendor_selection', 'price_approval', 'final_approval').\n\n"
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
            if state.current_step == TaskState.WAITING_VENDOR_SELECTION:
                return {"next_action": "vendor_search", "reason": "Fallback: search again.", "parameters": {}}
            elif state.current_step == TaskState.WAITING_PRICE_APPROVAL:
                return {"next_action": "vendor_search", "reason": "Fallback: return to search.", "parameters": {}}
            else:
                return {"next_action": "draft_outreach", "reason": "Fallback: rewrite outreach.", "parameters": {}}

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

    async def run_reflection(self, state: WorkflowState) -> None:
        """
        Runs quality audits (reflection) and produces improved draft.
        """
        logger.info("Planner starting self reflection step")
        await reflection_tool(state)
        logger.info("Planner completed self reflection")

# Singleton instance
planner = AgentPlanner()
