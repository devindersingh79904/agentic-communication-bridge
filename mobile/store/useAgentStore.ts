import { create } from 'zustand';

export interface Message {
  id: string;
  sender: 'user' | 'agent' | 'system';
  text: string;
  timestamp: Date;
  step?: string;
}

export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

export type AgentState =
  | 'IDLE'
  | 'SEARCHING_VENDORS'
  | 'ANALYZING_PRICING'
  | 'DRAFTING_OUTREACH'
  | 'SELF_REFLECTION'
  | 'WAITING_APPROVAL'
  | 'SUCCESS'
  | 'CANCELLED'
  | 'FAILED';

interface AgentStoreState {
  // Config
  hostUrl: string;
  setHostUrl: (url: string) => void;

  // Connection & Active Status
  connectionStatus: ConnectionStatus;
  agentState: AgentState;
  taskId: string | null;
  correlationId: string | null;

  // Active Data
  messages: Message[];
  draftMessage: string | null;
  timeoutCountdown: number | null;

  // Actions
  startAgent: (prompt: string) => void;
  sendApprove: () => void;
  sendStop: () => void;
  resetChat: () => void;
}

let ws: WebSocket | null = null;
let timerId: any = null;

export const useAgentStore = create<AgentStoreState>((set, get) => ({
  hostUrl: 'localhost:8000',
  setHostUrl: (hostUrl) => set({ hostUrl }),

  connectionStatus: 'disconnected',
  agentState: 'IDLE',
  taskId: null,
  correlationId: null,

  messages: [],
  draftMessage: null,
  timeoutCountdown: null,

  startAgent: (prompt) => {
    // Clean up any existing connection
    if (ws) {
      ws.close();
      ws = null;
    }
    if (timerId) {
      clearInterval(timerId);
      timerId = null;
    }

    const { hostUrl } = get();
    // Support ws:// or wss:// depending on url
    const cleanHost = hostUrl.replace(/^(ws:\/\/|wss:\/\/|http:\/\/|https:\/\/)/, '');
    const wsUrl = `ws://${cleanHost}/v1/agent/connect`;

    set({
      connectionStatus: 'connecting',
      agentState: 'IDLE',
      taskId: null,
      correlationId: null,
      draftMessage: null,
      timeoutCountdown: null,
      messages: [
        {
          id: Math.random().toString(36).substr(2, 9),
          sender: 'user',
          text: prompt,
          timestamp: new Date(),
        },
        {
          id: Math.random().toString(36).substr(2, 9),
          sender: 'system',
          text: `Connecting to agent server at ${wsUrl}...`,
          timestamp: new Date(),
        },
      ],
    });

    try {
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        set({ connectionStatus: 'connected' });
        set((state) => ({
          messages: [
            ...state.messages,
            {
              id: Math.random().toString(36).substr(2, 9),
              sender: 'system',
              text: 'Connection established. Orchestration started.',
              timestamp: new Date(),
            },
          ],
        }));
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          const { event_type, correlation_id, task_id, task_state, message, draft_message } = data;

          if (correlation_id) set({ correlationId: correlation_id });
          if (task_id) set({ taskId: task_id });

          switch (event_type) {
            case 'STATUS_UPDATE':
              set({ agentState: task_state });
              set((state) => ({
                messages: [
                  ...state.messages,
                  {
                    id: Math.random().toString(36).substr(2, 9),
                    sender: 'agent',
                    text: message,
                    timestamp: new Date(),
                    step: task_state,
                  },
                ],
              }));
              break;

            case 'APPROVAL_REQUIRED':
              set({
                agentState: 'WAITING_APPROVAL',
                draftMessage: draft_message || null,
                timeoutCountdown: 10, // Default fallback
              });

              // Start countdown timer locally
              if (timerId) clearInterval(timerId);
              timerId = setInterval(() => {
                const current = get().timeoutCountdown;
                if (current !== null && current > 0) {
                  set({ timeoutCountdown: current - 1 });
                } else {
                  clearInterval(timerId);
                  timerId = null;
                }
              }, 1000);

              set((state) => ({
                messages: [
                  ...state.messages,
                  {
                    id: Math.random().toString(36).substr(2, 9),
                    sender: 'agent',
                    text: `${message}\nDraft: "${draft_message}"`,
                    timestamp: new Date(),
                    step: 'WAITING_APPROVAL',
                  },
                ],
              }));
              break;

            case 'TASK_COMPLETED':
              if (timerId) {
                clearInterval(timerId);
                timerId = null;
              }
              set({ agentState: 'SUCCESS', timeoutCountdown: null });
              set((state) => ({
                messages: [
                  ...state.messages,
                  {
                    id: Math.random().toString(36).substr(2, 9),
                    sender: 'system',
                    text: `Task Completed: ${message}`,
                    timestamp: new Date(),
                  },
                ],
              }));
              if (ws) ws.close();
              break;

            case 'TASK_CANCELLED':
              if (timerId) {
                clearInterval(timerId);
                timerId = null;
              }
              set({ agentState: 'CANCELLED', timeoutCountdown: null, draftMessage: null });
              set((state) => ({
                messages: [
                  ...state.messages,
                  {
                    id: Math.random().toString(36).substr(2, 9),
                    sender: 'system',
                    text: `Task Cancelled: ${message}`,
                    timestamp: new Date(),
                  },
                ],
              }));
              if (ws) ws.close();
              break;

            case 'ERROR':
              if (timerId) {
                clearInterval(timerId);
                timerId = null;
              }
              set({ agentState: 'FAILED', timeoutCountdown: null });
              set((state) => ({
                messages: [
                  ...state.messages,
                  {
                    id: Math.random().toString(36).substr(2, 9),
                    sender: 'system',
                    text: `Error: ${message}`,
                    timestamp: new Date(),
                  },
                ],
              }));
              if (ws) ws.close();
              break;

            default:
              console.log('Unhandled event:', data);
          }
        } catch (err) {
          console.error('Failed to parse WebSocket message', err);
        }
      };

      ws.onerror = () => {
        set({ connectionStatus: 'error' });
        set((state) => ({
          messages: [
            ...state.messages,
            {
              id: Math.random().toString(36).substr(2, 9),
              sender: 'system',
              text: 'WebSocket error encountered.',
              timestamp: new Date(),
            },
          ],
        }));
      };

      ws.onclose = () => {
        set({ connectionStatus: 'disconnected' });
        if (timerId) {
          clearInterval(timerId);
          timerId = null;
        }
        ws = null;
      };
    } catch (error: any) {
      set({ connectionStatus: 'error' });
      set((state) => ({
        messages: [
          ...state.messages,
          {
            id: Math.random().toString(36).substr(2, 9),
            sender: 'system',
            text: `Failed to initiate WebSocket connection: ${error.message}`,
            timestamp: new Date(),
          },
        ],
      }));
    }
  },

  sendApprove: () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ event_type: 'APPROVED' }));
    }
  },

  sendStop: () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ event_type: 'STOP' }));
    }
  },

  resetChat: () => {
    if (ws) {
      ws.close();
      ws = null;
    }
    if (timerId) {
      clearInterval(timerId);
      timerId = null;
    }
    set({
      connectionStatus: 'disconnected',
      agentState: 'IDLE',
      taskId: null,
      correlationId: null,
      messages: [],
      draftMessage: null,
      timeoutCountdown: null,
    });
  },
}));
