import { useAgentStore } from '../store/agent-store';
import { SERVER_EVENTS, CLIENT_EVENTS } from '../constants/websocket-events';
import { ServerEvent, AgentStep } from '../types/websocket';
import { WS_BASE_URL, WS_AGENT_ENDPOINT } from '../constants/config';

let timerId: any = null;
let heartbeatTimerId: any = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 5;
let currentPromptRef = '';

export const connectAgentWS = (prompt: string) => {
  currentPromptRef = prompt;
  const store = useAgentStore.getState();

  // Clean up any existing timers
  if (timerId) {
    clearInterval(timerId);
    timerId = null;
  }
  if (heartbeatTimerId) {
    clearInterval(heartbeatTimerId);
    heartbeatTimerId = null;
  }

  // Pre-initialize prompt in chat log and set optimistic SCHEDULED state
  store.connectWebSocket(prompt);

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
      const existingTaskId = store.taskId;
      const isRealTaskId = existingTaskId && !existingTaskId.startsWith('task-opt-');
      const startEvent = {
        event_type: CLIENT_EVENTS.START_TASK,
        prompt: prompt,
        task_id: isRealTaskId ? existingTaskId : undefined,
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
        if ('pricing_analysis' in data && data.pricing_analysis) {
          store.setPricingAnalysis(data.pricing_analysis);
        }
        if ('reflection_metadata' in data && data.reflection_metadata) {
          store.setReflectionMetadata(data.reflection_metadata);
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

            store.appendMessage({
              sender: 'system',
              text: `📋 ${stepLabel} completed. Action required: authorize step to proceed.`,
            });

            store.appendMessage({
              sender: 'agent',
              text: approvalData.step_data ?? approvalData.draft_message,
              agent_step: approvalData.agent_step,
            });
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

      // Reconnect strategy if closed unexpectedly during run states
      const stateBeforeClose = store.taskState;
      const isIntermediate = [
        'SCHEDULED',
        'RUNNING',
        'EXTERNAL_SEARCHING',
        'FAILED_RETRYING',
        'WAITING_APPROVAL'
      ].includes(stateBeforeClose);

      if (isIntermediate && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
        reconnectAttempts++;
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 10000);
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
  useAgentStore.getState().disconnectWebSocket();
};