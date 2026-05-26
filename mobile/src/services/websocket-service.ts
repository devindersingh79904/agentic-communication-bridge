import { useAgentStore } from '../store/agent-store';
import { SERVER_EVENTS, CLIENT_EVENTS } from '../constants/websocket-events';
import { ServerEvent, AgentStep, VendorResult } from '../types/websocket';
import { HTTP_BASE_URL, WS_BASE_URL, WS_AGENT_ENDPOINT } from '../constants/config';

let timerId: any = null;
let heartbeatTimerId: any = null;
let pingTimerId: any = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 5;
let currentPromptRef = '';

const getVendorName = (vendor: VendorResult, index: number) =>
  vendor.vendor_name || vendor.name || `Vendor ${index + 1}`;

const buildVendorSummaryMessage = (vendors?: VendorResult[]) => {
  if (!vendors || vendors.length === 0) return null;

  const lines = vendors.slice(0, 5).map((vendor, index) => {
    const location = vendor.location || 'location n/a';
    const rating = vendor.rating ? `rating ${vendor.rating}` : 'rating n/a';
    const delivery = vendor.delivery_days ? `${vendor.delivery_days}d delivery` : 'delivery n/a';
    return `${index + 1}. ${getVendorName(vendor, index)} - ${location} - ${rating} - ${delivery}`;
  });

  return `Candidate vendors:\n${lines.join('\n')}`;
};

export const connectAgentWS = (prompt: string) => {
  currentPromptRef = prompt;
  const store = useAgentStore.getState();
  const existingTaskId = store.taskId;
  const resumeTaskId = existingTaskId && !existingTaskId.startsWith('task-opt-')
    ? existingTaskId
    : null;

  // Clean up any existing timers
  if (timerId) {
    clearInterval(timerId);
    timerId = null;
  }
  if (heartbeatTimerId) {
    clearInterval(heartbeatTimerId);
    heartbeatTimerId = null;
  }
  if (pingTimerId) {
    clearInterval(pingTimerId);
    pingTimerId = null;
  }

  if (resumeTaskId) {
    store.setCurrentPrompt(prompt);
    store.setConnectionStatus('connecting');
    store.appendMessage({
      sender: 'system',
      text: `Restoring workflow ${resumeTaskId.slice(0, 8)}...`,
    });
  } else {
    // Pre-initialize prompt in chat log and set optimistic SCHEDULED state
    store.connectWebSocket(prompt);
  }

  // Trigger REST session restoration before opening connection
  if (resumeTaskId) {
    (async () => {
      try {
        const url = `${HTTP_BASE_URL}/v1/workflow/${resumeTaskId}`;
        const response = await fetch(url);
        if (response.ok) {
          const d = await response.json();
          if (d && d.task_id) {
            store.setWorkflowVersion(d.workflow_version || 1);
            if (d.state) store.updateTaskState(d.state);
            
            if (d.messages && d.messages.length > 0) {
              const parsedMessages = d.messages.map((m: any) => ({
                ...m,
                timestamp: m.timestamp ? new Date(m.timestamp) : new Date()
              }));
              useAgentStore.setState({ agentMessages: parsedMessages });
            }
            
            const payload = d.approval_payload;
            if (payload) {
              if (payload.vendors) store.setVendorResults(payload.vendors);
              if (payload.selected_vendor) store.setSelectedVendor(payload.selected_vendor);
              if (payload.selected_vendors) store.setSelectedVendors(payload.selected_vendors);
              if (payload.pricing_analysis) store.setPricingAnalysis(payload.pricing_analysis);
              if (payload.reflection_metadata) store.setReflectionMetadata(payload.reflection_metadata);
              if (payload.draft_message) store.setDraftMessage(payload.draft_message);
              if (payload.agent_step) {
                store.setCurrentPendingStep(payload.agent_step);
                store.setIsAwaitingApproval(true);
              }
            }
          }
        }
      } catch (err) {
        console.warn('Failed restoring workflow session:', err);
      }
    })();
  }

  let finalWsBaseUrl = WS_BASE_URL;
  if (typeof window !== 'undefined' && window.location && window.location.protocol === 'https:') {
    finalWsBaseUrl = WS_BASE_URL.replace(/^ws:\/\//, 'wss://');
  }
  const wsUrl = `${finalWsBaseUrl}${WS_AGENT_ENDPOINT}`;

  store.appendMessage({
    sender: 'system',
    text: `Connecting to server at ${wsUrl}...`,
  });
  store.setConnectionStatus('connecting');

  try {
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      reconnectAttempts = 0; // Reset reconnect counter on success
      store.setConnectionStatus('connected');
      store.setSocket(ws);
      store.updateLastActivity();
      
      store.appendMessage({
        sender: 'system',
        text: 'Connected. Workflow initialized.',
      });

      // Send START_TASK event with the user prompt (and existing task_id if resuming)
      const startEvent = {
        event_type: CLIENT_EVENTS.START_TASK,
        prompt: prompt,
        task_id: resumeTaskId ?? undefined,
      };
      ws.send(JSON.stringify(startEvent));

      // Start client heartbeat monitor
      heartbeatTimerId = setInterval(() => {
        const lastActivity = useAgentStore.getState().lastActivityTime;
        const now = Date.now();
        // If no activity for 30 seconds, socket is zombie, close it
        if (now - lastActivity > 30000) {
          console.warn('WebSocket heartbeat timeout. Closing socket...');
          ws.close();
        }
      }, 10000);

      // Start client-to-server PING heartbeat loop
      pingTimerId = setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(
            JSON.stringify({
              event_type: CLIENT_EVENTS.PING,
              correlation_id: useAgentStore.getState().correlationId || '',
              task_id: useAgentStore.getState().taskId || '',
            })
          );
        }
      }, 20000);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as ServerEvent;
        const { event_type, correlation_id, task_id } = data;

        // Update active context timestamps on message
        store.updateLastActivity();

        if (task_id || correlation_id) {
          store.setIds(task_id ?? null, correlation_id);
        }

        // Extract metadata if available in payload
        if ('vendors' in data && data.vendors) {
          store.setVendorResults(data.vendors);
        }
        if ('selected_vendor' in data && data.selected_vendor) {
          store.setSelectedVendor(data.selected_vendor);
        }
        if ('selected_vendors' in data && data.selected_vendors) {
          store.setSelectedVendors(data.selected_vendors);
        }
        if ('pricing_analysis' in data && data.pricing_analysis) {
          store.setPricingAnalysis(data.pricing_analysis);
          if (data.pricing_analysis.selected_vendor) {
            store.setSelectedVendor(data.pricing_analysis.selected_vendor);
          }
          if (data.pricing_analysis.selected_vendors) {
            store.setSelectedVendors(data.pricing_analysis.selected_vendors);
          }
        }
        if ('reflection_metadata' in data && data.reflection_metadata) {
          store.setReflectionMetadata(data.reflection_metadata);
        }
        if ('workflow_version' in data && data.workflow_version) {
          store.setWorkflowVersion(data.workflow_version as number);
        }
        if ('reasoning_traces' in data && data.reasoning_traces) {
          store.setReasoningTraces(data.reasoning_traces as any[]);
        }

        switch (event_type) {
          case SERVER_EVENTS.PING:
            // Respond with PONG immediately
            if (ws && ws.readyState === WebSocket.OPEN) {
              ws.send(
                JSON.stringify({
                  event_type: CLIENT_EVENTS.PONG,
                  correlation_id: correlation_id,
                  task_id: task_id || store.taskId,
                })
              );
            }
            break;

          case SERVER_EVENTS.PONG:
            // Heartbeat check acknowledged
            break;

          case SERVER_EVENTS.STATUS_UPDATE:
            const statusData = data as import('../types/websocket').StatusUpdateEvent;
            store.updateTaskState(statusData.task_state);
            
            if ('agent_step' in statusData && statusData.agent_step) {
              store.setCurrentAgentStep(statusData.agent_step as AgentStep);
            }
            
            let messageText = statusData.message;
            if (statusData.agent_step === 'DRAFTING_OUTREACH' && store.isRegenerating) {
              messageText = 'Regenerating outreach draft...';
            } else if (statusData.agent_step === 'SELF_REFLECTION' && store.isRegenerating) {
              messageText = 'Performing self-reflection audit...';
            }

            store.appendMessage({
              sender: 'agent',
              text: messageText,
              agent_step: statusData.agent_step,
            });
            break;

          case SERVER_EVENTS.APPROVAL_REQUIRED:
            const approvalData = data as import('../types/websocket').ApprovalRequiredEvent;
            const stepLabel = approvalData.agent_step
              ? approvalData.agent_step.replace(/_/g, ' ').toLowerCase()
              : 'step';

            store.updateTaskState(approvalData.task_state);
            store.setCurrentAgentStep(null);
            store.setDraftMessage(approvalData.draft_message);
            store.setCurrentPendingStep(approvalData.agent_step);
            store.setCurrentStepData(approvalData.step_data ?? approvalData.draft_message);
            store.setIsAwaitingApproval(true);
            store.setIsRegenerating(false);
            store.setRejectionFeedback('');
            store.setError(null);

            // Ensure vendor results are populated from approval data
            if (approvalData.vendors && approvalData.vendors.length > 0) {
              store.setVendorResults(approvalData.vendors);
            }
            if (approvalData.selected_vendor) {
              store.setSelectedVendor(approvalData.selected_vendor);
            }
            if (approvalData.selected_vendors) {
              store.setSelectedVendors(approvalData.selected_vendors);
            }
            if (approvalData.pricing_analysis) {
              store.setPricingAnalysis(approvalData.pricing_analysis);
              if (approvalData.pricing_analysis.selected_vendor) {
                store.setSelectedVendor(approvalData.pricing_analysis.selected_vendor);
              }
              if (approvalData.pricing_analysis.selected_vendors) {
                store.setSelectedVendors(approvalData.pricing_analysis.selected_vendors);
              }
            }

            // Set dynamic timeout countdown
            const timeoutSecs = approvalData.approval_timeout_seconds ?? 60;
            store.setTimeoutCountdown(timeoutSecs);

            if (timerId) clearInterval(timerId);
            timerId = setInterval(() => {
              store.setTimeoutCountdown((prev) => {
                if (prev !== null && prev > 0) {
                  return prev - 1;
                } else {
                  clearInterval(timerId);
                  timerId = null;
                  return null;
                }
              });
            }, 1000);

            const isFinalApproval = approvalData.task_state === 'WAITING_FINAL_APPROVAL';
            const vendorSummary = approvalData.task_state === 'WAITING_VENDOR_SELECTION'
              ? buildVendorSummaryMessage(approvalData.vendors)
              : null;
            store.appendMessage({
              sender: 'system',
              text: isFinalApproval
                ? `📧 Draft generated — review below and approve or reject with feedback.`
                : `📋 ${stepLabel} completed. Action required: authorize step to proceed.`,
            });

            if (isFinalApproval && (approvalData.draft_message || approvalData.step_data)) {
              if (approvalData.pricing_analysis?.summary) {
                store.appendMessage({
                  sender: 'agent',
                  text: `Vendor comparison:\n\n${approvalData.pricing_analysis.summary}`,
                  agent_step: 'ANALYZING_PRICING' as AgentStep,
                });
              }

              // Show the LLM-generated draft email in chat on every iteration
              store.appendMessage({
                sender: 'agent',
                text: approvalData.draft_message ?? approvalData.step_data ?? '',
                agent_step: 'DRAFTING_OUTREACH' as AgentStep,
              });
            } else if (!isFinalApproval) {
              store.appendMessage({
                sender: 'agent',
                text: vendorSummary ?? approvalData.step_data ?? approvalData.draft_message,
                agent_step: approvalData.agent_step,
              });
            }
            break;

          case SERVER_EVENTS.TASK_COMPLETED:
            if (timerId) { clearInterval(timerId); timerId = null; }
            if (heartbeatTimerId) { clearInterval(heartbeatTimerId); heartbeatTimerId = null; }
            
            store.updateTaskState('SUCCESS');
            store.setCurrentAgentStep(null);
            store.setIsAwaitingApproval(false);
            store.setIsRegenerating(false);
            store.setTimeoutCountdown(null);
            store.setCurrentPendingStep(null);
            store.setCurrentStepData(null);

            const completedData = data as import('../types/websocket').TaskCompletedEvent;
            if (completedData.final_response) {
              store.setFinalEmail(completedData.final_response);
            }
            if (completedData.selected_vendor) {
              store.setSelectedVendor(completedData.selected_vendor);
            }
            if (completedData.selected_vendors) {
              store.setSelectedVendors(completedData.selected_vendors);
            }
            if (completedData.pricing_analysis) {
              store.setPricingAnalysis(completedData.pricing_analysis);
            }
            
            // Add final outcome to history item
            if (store.taskId) {
              store.updateHistoryItem(store.taskId, {
                status: 'SUCCESS',
                selected_vendor: store.selectedVendor ?? undefined,
                final_response: completedData.final_response ?? store.draftMessage ?? undefined,
              });
            }

            if (completedData.final_response) {
              store.appendMessage({
                sender: 'agent',
                text: `Refined Outreach Proposal:\n\n${completedData.final_response}`,
              });
            }

            store.appendMessage({
              sender: 'system',
              text: `Success: ${completedData.message}`,
            });
            ws.close();
            break;

          case SERVER_EVENTS.TASK_CANCELLED:
            if (timerId) { clearInterval(timerId); timerId = null; }
            if (heartbeatTimerId) { clearInterval(heartbeatTimerId); heartbeatTimerId = null; }
            
            store.updateTaskState('CANCELLED');
            store.setCurrentAgentStep(null);
            store.setIsAwaitingApproval(false);
            store.setIsRegenerating(false);
            store.setTimeoutCountdown(null);
            store.setDraftMessage(null);
            store.setCurrentPendingStep(null);
            store.setCurrentStepData(null);
            
            const cancelData = data as import('../types/websocket').TaskCancelledEvent;
            const isTimeout = cancelData.message.toLowerCase().includes('timeout');
            store.setCancellationReason(isTimeout ? 'timeout' : 'user');

            if (store.taskId) {
              store.updateHistoryItem(store.taskId, {
                status: 'CANCELLED',
              });
            }

            store.appendMessage({
              sender: 'system',
              text: `Cancelled: ${cancelData.message}`,
            });
            ws.close();
            break;

          case SERVER_EVENTS.ERROR:
            const errorData = data as import('../types/websocket').ErrorEvent;
            if (timerId) { clearInterval(timerId); timerId = null; }
            if (heartbeatTimerId) { clearInterval(heartbeatTimerId); heartbeatTimerId = null; }
            
            store.updateTaskState('FAILED');
            store.setCurrentAgentStep(null);
            store.setIsAwaitingApproval(false);
            store.setIsRegenerating(false);
            store.setTimeoutCountdown(null);
            store.setCurrentPendingStep(null);
            store.setCurrentStepData(null);
            store.setError(errorData.message);

            if (store.taskId) {
              store.updateHistoryItem(store.taskId, {
                status: 'FAILED',
              });
            }

            store.appendMessage({
              sender: 'system',
              text: `Error (${errorData.error_code}): ${errorData.message}`,
            });
            ws.close();
            break;

          default:
            console.warn('Unknown event type received:', event_type);
        }
      } catch (err: any) {
        console.error('Failed parsing WS message:', err);
      }
    };

    ws.onerror = () => {
      if (timerId) { clearInterval(timerId); timerId = null; }
      if (heartbeatTimerId) { clearInterval(heartbeatTimerId); heartbeatTimerId = null; }
      if (pingTimerId) { clearInterval(pingTimerId); pingTimerId = null; }
      
      store.setConnectionStatus('error');
      store.setError('WebSocket connection error');
      store.appendMessage({
        sender: 'system',
        text: 'Connection error encountered.',
      });
    };

    ws.onclose = (event) => {
      store.setConnectionStatus('disconnected');
      store.setSocket(null);
      store.setTimeoutCountdown(null);
      store.setIsAwaitingApproval(false);
      store.setIsRegenerating(false);
      
      if (timerId) { clearInterval(timerId); timerId = null; }
      if (heartbeatTimerId) { clearInterval(heartbeatTimerId); heartbeatTimerId = null; }
      if (pingTimerId) { clearInterval(pingTimerId); pingTimerId = null; }

      // Reconnect strategy if closed unexpectedly during run states
      const stateBeforeClose = store.taskState;
      const isIntermediate = [
        'SCHEDULED',
        'RUNNING',
        'SEARCHING_VENDORS',
        'EXTERNAL_SEARCHING',
        'ANALYZING_PRICING',
        'DRAFTING_OUTREACH',
        'SELF_REFLECTION',
        'FAILED_RETRYING',
        'WAITING_VENDOR_SELECTION',
        'WAITING_PRICE_APPROVAL',
        'WAITING_FINAL_APPROVAL',
      ].includes(stateBeforeClose);

      if (isIntermediate && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
        reconnectAttempts++;
        const retryDelays = [1000, 2000, 5000, 10000];
        const delay = retryDelays[reconnectAttempts - 1] || 10000;
        store.appendMessage({
          sender: 'system',
          text: `Disconnected unexpectedly. Reconnecting in ${delay / 1000}s (Attempt ${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})...`,
        });
        setTimeout(() => {
          connectAgentWS(currentPromptRef);
        }, delay);
      } else if (isIntermediate) {
        store.updateTaskState('FAILED');
        store.setError('WebSocket disconnected and maximum reconnection attempts exceeded.');
        store.appendMessage({
          sender: 'system',
          text: 'WebSocket disconnected. Connection could not be restored.',
        });
      }
    };
  } catch (error: any) {
    if (timerId) { clearInterval(timerId); timerId = null; }
    if (heartbeatTimerId) { clearInterval(heartbeatTimerId); heartbeatTimerId = null; }
    if (pingTimerId) { clearInterval(pingTimerId); pingTimerId = null; }
    
    store.setConnectionStatus('error');
    store.setError(error.message || 'Failed connecting');
    store.appendMessage({
      sender: 'system',
      text: `WebSocket initiation failed: ${error.message}`,
    });
  }
};

export const disconnectAgentWS = () => {
  reconnectAttempts = MAX_RECONNECT_ATTEMPTS; // prevent auto-reconnect
  if (timerId) {
    clearInterval(timerId);
    timerId = null;
  }
  if (heartbeatTimerId) {
    clearInterval(heartbeatTimerId);
    heartbeatTimerId = null;
  }
  if (pingTimerId) {
    clearInterval(pingTimerId);
    pingTimerId = null;
  }
  useAgentStore.getState().disconnectWebSocket();
};
