import { useAgentStore } from '../store/agent-store';
import { SERVER_EVENTS, CLIENT_EVENTS } from '../constants/websocket-events';
import { ServerEvent, AgentStep } from '../types/websocket';

let timerId: any = null;

export const connectAgentWS = (prompt: string) => {
  const store = useAgentStore.getState();

  // Clean up any existing timers
  if (timerId) {
    clearInterval(timerId);
    timerId = null;
  }

  // Pre-initialize prompt in chat log and set optimistic SCHEDULED state
  store.connectWebSocket(prompt);

  const cleanHost = store.hostUrl.replace(/^(ws:\/\/|wss:\/\/|http:\/\/|https:\/\/)/, '');

  // Resolve protocol automatically (WSS for HTTPS/SSL, WS for HTTP/Local)
  let protocol = 'ws';
  if (typeof window !== 'undefined' && window.location) {
    protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  } else {
    // Non-browser or native fallback
    protocol = cleanHost.includes('localhost') || cleanHost.includes('127.0.0.1') ? 'ws' : 'wss';
  }

  const wsUrl = `${protocol}://${cleanHost}/v1/agent/connect`;

  store.appendMessage({
    sender: 'system',
    text: `Connecting to server at ${wsUrl}...`,
  });
  store.setConnectionStatus('connecting');

  try {
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      store.setConnectionStatus('connected');
      store.setSocket(ws);
      store.appendMessage({
        sender: 'system',
        text: 'Connected. Workflow initialized.',
      });

      // Send START_TASK event with the user prompt
      const startEvent: import('../types/websocket').ClientStartTaskEvent = {
        event_type: CLIENT_EVENTS.START_TASK,
        prompt: prompt,
      };
      ws.send(JSON.stringify(startEvent));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as ServerEvent;
        const { event_type, correlation_id, task_id, task_state, message } = data;

        if (task_id || correlation_id) {
          store.setIds(task_id ?? null, correlation_id);
        }

        switch (event_type) {
          case SERVER_EVENTS.STATUS_UPDATE:
            const statusData = data as import('../types/websocket').StatusUpdateEvent;
            store.updateTaskState(statusData.task_state);
            // Track the agent workflow step separately
            if ('agent_step' in statusData && statusData.agent_step) {
              store.setCurrentAgentStep(statusData.agent_step as AgentStep);
            }
            store.appendMessage({
              sender: 'agent',
              text: statusData.message,
              agent_step: statusData.agent_step,
            });
            break;

          case SERVER_EVENTS.APPROVAL_REQUIRED:
            const approvalData = data as import('../types/websocket').ApprovalRequiredEvent;
            store.updateTaskState('WAITING_APPROVAL');
            store.setCurrentAgentStep(null);
            store.setDraftMessage(approvalData.draft_message);
            store.setIsAwaitingApproval(true);
            store.setIsRegenerating(false);
            store.setRejectionFeedback('');
            store.setError(null);

            // Set dynamic timeout countdown (fallback to 10s if missing)
            const timeoutSecs = approvalData.approval_timeout_seconds ?? 10;
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
              sender: 'agent',
              text: `${approvalData.message}\nDraft: "${approvalData.draft_message}"`,
              // We can omit agent_step here or set it to null since it's not a real step
            });
            break;

          case SERVER_EVENTS.TASK_COMPLETED:
            if (timerId) { clearInterval(timerId); timerId = null; }
            store.updateTaskState('SUCCESS');
            store.setCurrentAgentStep(null);
            store.setIsAwaitingApproval(false);
            store.setIsRegenerating(false);
            store.setTimeoutCountdown(null);

            const completedData = data as import('../types/websocket').TaskCompletedEvent;
            if (completedData.final_response) {
              store.appendMessage({
                sender: 'agent',
                text: completedData.final_response,
              });
            }

            store.appendMessage({
              sender: 'system',
              text: `Success: ${message}`,
            });
            ws.close();
            break;

          case SERVER_EVENTS.TASK_CANCELLED:
            if (timerId) { clearInterval(timerId); timerId = null; }
            store.updateTaskState('CANCELLED');
            store.setCurrentAgentStep(null);
            store.setIsAwaitingApproval(false);
            store.setIsRegenerating(false);
            store.setTimeoutCountdown(null);
            store.setDraftMessage(null);
            store.appendMessage({
              sender: 'system',
              text: `Cancelled: ${message}`,
            });
            ws.close();
            break;

          case SERVER_EVENTS.ERROR:
            const errorData = data as import('../types/websocket').ErrorEvent;
            if (timerId) { clearInterval(timerId); timerId = null; }
            store.updateTaskState('FAILED');
            store.setCurrentAgentStep(null);
            store.setIsAwaitingApproval(false);
            store.setIsRegenerating(false);
            store.setTimeoutCountdown(null);
            store.setError(errorData.message);
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
      store.setConnectionStatus('error');
      store.setError('WebSocket connection error');
      store.setCurrentPrompt(null);
      store.appendMessage({
        sender: 'system',
        text: 'Connection error encountered.',
      });
    };

    ws.onclose = () => {
      store.setConnectionStatus('disconnected');
      store.setSocket(null);
      store.setCurrentPrompt(null);
      store.setTimeoutCountdown(null);
      store.setIsAwaitingApproval(false);
      store.setIsRegenerating(false);
      if (timerId) {
        clearInterval(timerId);
        timerId = null;
      }
    };
  } catch (error: any) {
    if (timerId) { clearInterval(timerId); timerId = null; }
    store.setConnectionStatus('error');
    store.setError(error.message || 'Failed connecting');
    store.appendMessage({
      sender: 'system',
      text: `WebSocket initiation failed: ${error.message}`,
    });
  }
};

export const disconnectAgentWS = () => {
  if (timerId) {
    clearInterval(timerId);
    timerId = null;
  }
  useAgentStore.getState().disconnectWebSocket();
};
