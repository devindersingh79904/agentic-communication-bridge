import { create } from 'zustand';
import { ConnectionStatus, TaskState, Message } from '../types/websocket';

interface AgentState {
  // State variables
  hostUrl: string;
  socket: WebSocket | null;
  connectionStatus: ConnectionStatus;
  taskState: TaskState;
  agentMessages: Message[];
  draftMessage: string | null;
  isAwaitingApproval: boolean;
  timeoutCountdown: number | null;
  error: string | null;
  taskId: string | null;
  correlationId: string | null;
  backendSteps: string[];

  // Actions
  setHostUrl: (url: string) => void;
  setSocket: (socket: WebSocket | null) => void;
  setConnectionStatus: (status: ConnectionStatus) => void;
  updateTaskState: (state: TaskState) => void;
  appendMessage: (message: Omit<Message, 'id' | 'timestamp'>) => void;
  setDraftMessage: (draft: string | null) => void;
  setIsAwaitingApproval: (isAwaiting: boolean) => void;
  setTimeoutCountdown: (countdown: number | null | ((prev: number | null) => number | null)) => void;
  setError: (error: string | null) => void;
  setIds: (taskId: string | null, correlationId: string | null) => void;
  fetchMetadataEnums: () => Promise<void>;

  // Web socket controller actions
  connectWebSocket: (prompt: string) => void;
  disconnectWebSocket: () => void;
  sendApprove: () => void;
  sendStop: () => void;
  resetStore: () => void;
}

export const useAgentStore = create<AgentState>((set, get) => ({
  hostUrl: 'localhost:8000',
  socket: null,
  connectionStatus: 'disconnected',
  taskState: 'IDLE',
  agentMessages: [],
  draftMessage: null,
  isAwaitingApproval: false,
  timeoutCountdown: null,
  error: null,
  taskId: null,
  correlationId: null,
  backendSteps: [],

  setHostUrl: (hostUrl) => set({ hostUrl }),
  setSocket: (socket) => set({ socket }),
  setConnectionStatus: (connectionStatus) => set({ connectionStatus }),
  updateTaskState: (taskState) => set({ taskState }),
  appendMessage: (msg) =>
    set((state) => ({
      agentMessages: [
        ...state.agentMessages,
        {
          ...msg,
          id: Math.random().toString(36).slice(2, 11),
          timestamp: new Date(),
        },
      ],
    })),
  setDraftMessage: (draftMessage) => set({ draftMessage }),
  setIsAwaitingApproval: (isAwaitingApproval) => set({ isAwaitingApproval }),
  setTimeoutCountdown: (update) =>
    set((state) => ({
      timeoutCountdown: typeof update === 'function' ? update(state.timeoutCountdown) : update,
    })),
  setError: (error) => set({ error }),
  setIds: (taskId, correlationId) => set({ taskId, correlationId }),

  fetchMetadataEnums: async () => {
    const { hostUrl } = get();
    const cleanHost = hostUrl.replace(/^(ws:\/\/|wss:\/\/|http:\/\/|https:\/\/)/, '');
    let protocol = 'http';
    if (typeof window !== 'undefined' && window.location) {
      protocol = window.location.protocol === 'https:' ? 'https' : 'http';
    } else {
      protocol = cleanHost.includes('localhost') || cleanHost.includes('127.0.0.1') ? 'http' : 'https';
    }
    const url = `${protocol}://${cleanHost}/v1/metadata/enums`;
    try {
      const response = await fetch(url);
      const json = await response.json();
      if (json && json.data && json.data.agent_steps) {
        set({ backendSteps: json.data.agent_steps });
      }
    } catch (err) {
      console.warn('Failed to fetch backend enum metadata:', err);
    }
  },

  connectWebSocket: (prompt) => {
    // We defer the creation logic to the WebSocketService, which calls get().setSocket(...)
    // and registers listeners. But we initialize state here first.
    get().resetStore();
    get().appendMessage({
      sender: 'user',
      text: prompt,
    });
  },

  disconnectWebSocket: () => {
    const { socket } = get();
    if (socket) {
      socket.close();
      set({ socket: null, connectionStatus: 'disconnected' });
    }
  },

  sendApprove: () => {
    const { socket, taskId, correlationId } = get();
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(
        JSON.stringify({
          event_type: 'APPROVED',
          task_id: taskId,
          correlation_id: correlationId,
        })
      );
    }
  },

  sendStop: () => {
    const { socket, taskId, correlationId } = get();
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(
        JSON.stringify({
          event_type: 'STOP',
          task_id: taskId,
          correlation_id: correlationId,
        })
      );
    }
  },

  resetStore: () => {
    const { socket } = get();
    if (socket) {
      socket.close();
    }
    set({
      socket: null,
      connectionStatus: 'disconnected',
      taskState: 'IDLE',
      agentMessages: [],
      draftMessage: null,
      isAwaitingApproval: false,
      timeoutCountdown: null,
      error: null,
      taskId: null,
      correlationId: null,
    });
  },
}));
