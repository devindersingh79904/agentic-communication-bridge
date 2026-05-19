import { useAgentStore } from '../store/agent-store';
import { SERVER_EVENTS } from '../constants/websocket-events';
import { ServerEvent } from '../types/websocket';

let timerId: any = null;

export const connectAgentWS = (prompt: string) => {
  const store = useAgentStore.getState();
  
  // Clean up any existing timers
  if (timerId) {
    clearInterval(timerId);
    timerId = null;
  }

  // Pre-initialize prompt in chat log
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
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as ServerEvent;
        const { event_type, correlation_id, task_id, task_state, message } = data;

        if (task_id || correlation_id) {
          store.setIds(task_id, correlation_id);
        }

        switch (event_type) {
          case SERVER_EVENTS.STATUS_UPDATE:
            store.updateTaskState(task_state);
            store.appendMessage({
              sender: 'agent',
              text: message,
              agent_step: data.agent_step,
            });
            break;

          case SERVER_EVENTS.APPROVAL_REQUIRED:
            store.updateTaskState('WAITING_APPROVAL');
            store.setDraftMessage(data.draft_message);
            store.setIsAwaitingApproval(true);
            store.setError(null);
            
            // Set dynamic timeout countdown (fallback to 10s if missing)
            const timeoutSecs = data.approval_timeout_seconds ?? 10;
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
              text: `${message}\nDraft: "${data.draft_message}"`,
              agent_step: 'WAITING_APPROVAL',
            });
            break;

          case SERVER_EVENTS.TASK_COMPLETED:
            if (timerId) clearInterval(timerId);
            store.updateTaskState('SUCCESS');
            store.setIsAwaitingApproval(false);
            store.setTimeoutCountdown(null);
            store.appendMessage({
              sender: 'system',
              text: `Success: ${message}`,
            });
            ws.close();
            break;

          case SERVER_EVENTS.TASK_CANCELLED:
            if (timerId) clearInterval(timerId);
            store.updateTaskState('CANCELLED');
            store.setIsAwaitingApproval(false);
            store.setTimeoutCountdown(null);
            store.setDraftMessage(null);
            store.appendMessage({
              sender: 'system',
              text: `Cancelled: ${message}`,
            });
            ws.close();
            break;

          case SERVER_EVENTS.ERROR:
            if (timerId) clearInterval(timerId);
            store.updateTaskState('FAILED');
            store.setIsAwaitingApproval(false);
            store.setTimeoutCountdown(null);
            store.setError(message);
            store.appendMessage({
              sender: 'system',
              text: `Error (${data.error_code}): ${message}`,
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
      store.setConnectionStatus('error');
      store.setError('WebSocket connection error');
      store.appendMessage({
        sender: 'system',
        text: 'Connection error encountered.',
      });
    };

    ws.onclose = () => {
      store.setConnectionStatus('disconnected');
      store.setSocket(null);
      if (timerId) {
        clearInterval(timerId);
        timerId = null;
      }
    };
  } catch (error: any) {
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
