import { create } from 'zustand';
import { ConnectionStatus, TaskState, AgentStep, Message, ApprovalAction } from '../types/websocket';
import { getCleanHost, HTTP_BASE_URL } from '../constants/config';
import { CLIENT_EVENTS } from '../constants/websocket-events';

interface AgentState {
  // State variables
  hostUrl: string;
  socket: WebSocket | null;
  connectionStatus: ConnectionStatus;
  taskState: TaskState;
  currentAgentStep: AgentStep | null;
  currentPrompt: string | null;
  agentMessages: Message[];
  draftMessage: string | null;
  isAwaitingApproval: boolean;
  rejectionFeedback: string;
  isRegenerating: boolean;
  timeoutCountdown: number | null;
  error: string | null;
  taskId: string | null;
  correlationId: string | null;
  backendSteps: AgentStep[];
  cancellationReason: string | null;

  // Actions
  setHostUrl: (url: string) => void;
  setSocket: (socket: WebSocket | null) => void;
  setConnectionStatus: (status: ConnectionStatus) => void;
  updateTaskState: (state: TaskState) => void;
  setCurrentAgentStep: (step: AgentStep | null) => void;
  setCurrentPrompt: (prompt: string | null) => void;
  appendMessage: (message: Omit<Message, 'id' | 'timestamp'>) => void;
  setDraftMessage: (draft: string | null) => void;
  setIsAwaitingApproval: (isAwaiting: boolean) => void;
  setRejectionFeedback: (feedback: string) => void;
  setIsRegenerating: (isRegen: boolean) => void;
  setTimeoutCountdown: (countdown: number | null | ((prev: number | null) => number | null)) => void;
  setError: (error: string | null) => void;
  setIds: (taskId: string | null, correlationId: string | null) => void;
  setCancellationReason: (reason: string | null) => void;
  fetchMetadataEnums: () => Promise<void>;

  // Web socket controller actions
  connectWebSocket: (prompt: string) => void;
  disconnectWebSocket: () => void;
  sendApprovalResponse: (action: ApprovalAction, feedback?: string) => void;
  sendStop: () => void;
  resetStore: (clearMessages?: boolean) => void;
}

export const useAgentStore = create<AgentState>((set, get) => ({
  hostUrl: getCleanHost(),
  socket: null,
  connectionStatus: 'disconnected',
  taskState: 'IDLE',
  currentAgentStep: null,
  currentPrompt: null,
  agentMessages: [],
  draftMessage: null,
  isAwaitingApproval: false,
  rejectionFeedback: '',
  isRegenerating: false,
  timeoutCountdown: null,
  error: null,
  taskId: null,
  correlationId: null,
  backendSteps: [],
  cancellationReason: null,

  setHostUrl: (hostUrl) => set({ hostUrl }),
  setSocket: (socket) => set({ socket }),
  setConnectionStatus: (connectionStatus) => set({ connectionStatus }),
  updateTaskState: (taskState) => set({ taskState }),
  setCurrentAgentStep: (currentAgentStep) => set({ currentAgentStep }),
  setCurrentPrompt: (currentPrompt) => set({ currentPrompt }),
  appendMessage: (msg) =>
    set((state) => {
      // Prevent duplicate system/status messages (consecutive-only)
      if (msg.sender === 'system' || msg.sender === 'agent') {
        const lastMsg = state.agentMessages[state.agentMessages.length - 1];
        if (lastMsg && lastMsg.sender === msg.sender && lastMsg.text === msg.text) {
          return {};
        }
      }
      return {
        agentMessages: [
          ...state.agentMessages,
          {
            ...msg,
            id: Math.random().toString(36).slice(2, 11),
            timestamp: new Date(),
          },
        ],
      };
    }),
  setDraftMessage: (draftMessage) => set({ draftMessage }),
  setIsAwaitingApproval: (isAwaitingApproval) => set({ isAwaitingApproval }),
  setRejectionFeedback: (rejectionFeedback) => set({ rejectionFeedback }),
  setIsRegenerating: (isRegenerating) => set({ isRegenerating }),
  setTimeoutCountdown: (update) =>
    set((state) => ({
      timeoutCountdown: typeof update === 'function' ? update(state.timeoutCountdown) : update,
    })),
  setError: (error) => set({ error }),
  setIds: (taskId, correlationId) => set({ taskId, correlationId }),
  setCancellationReason: (cancellationReason) => set({ cancellationReason }),

  fetchMetadataEnums: async () => {
    const baseUrl = HTTP_BASE_URL;
    const url = `${baseUrl}/v1/metadata/enums`;
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
    const { socket } = get();

    // Duplicate connection guard
    if (socket && socket.readyState === WebSocket.OPEN) {
      console.warn('WebSocket already open — ignoring duplicate connect');
      return;
    }

    // Reset previous session state but preserve hostUrl and backendSteps
    get().resetStore(true);

    // Store the current prompt
    set({ currentPrompt: prompt });

    // Optimistic UI: immediately set SCHEDULED state
    set({ taskState: 'SCHEDULED' });

    get().appendMessage({
      sender: 'user',
      text: prompt,
    });
    get().appendMessage({
      sender: 'system',
      text: 'Task scheduled. Connecting to orchestration service...',
    });
  },

  disconnectWebSocket: () => {
    const { socket } = get();
    if (socket) {
      socket.close();
      set({ socket: null, connectionStatus: 'disconnected' });
    }
  },

  sendApprovalResponse: (action, feedback) => {
    const { socket, taskId, correlationId } = get();
    set({ isAwaitingApproval: false });
    
    if (action === 'APPROVE') {
      get().appendMessage({
        sender: 'system',
        text: 'You approved the draft.',
      });
    } else if (action === 'REJECT') {
      get().appendMessage({
        sender: 'system',
        text: 'You rejected the draft and requested regeneration.',
      });
      if (feedback && feedback.trim()) {
        get().appendMessage({
          sender: 'system',
          text: `Feedback: "${feedback.trim()}"`,
        });
      }
      set({ isRegenerating: true });
    }

    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(
        JSON.stringify({
          event_type: CLIENT_EVENTS.APPROVAL_RESPONSE,
          action,
          feedback,
          task_id: taskId,
          correlation_id: correlationId,
        })
      );
    }
  },

  sendStop: () => {
    const { socket, taskId, correlationId } = get();
    
    // Immediate optimistic cleanup
    set({
      taskState: 'CANCELLED',
      cancellationReason: 'user',
      isAwaitingApproval: false,
      isRegenerating: false,
      timeoutCountdown: null,
    });

    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(
        JSON.stringify({
          event_type: CLIENT_EVENTS.STOP,
          task_id: taskId,
          correlation_id: correlationId,
        })
      );
      // Prevent repeated STOP spam by closing socket immediately
      socket.close();
    }
  },

  resetStore: (clearMessages = false) => {
    const { socket } = get();
    if (socket) {
      socket.close();
    }
    set((state) => ({
      socket: null,
      connectionStatus: 'disconnected',
      taskState: 'IDLE',
      currentAgentStep: null,
      currentPrompt: null,
      agentMessages: clearMessages ? [] : state.agentMessages,
      draftMessage: null,
      isAwaitingApproval: false,
      rejectionFeedback: '',
      isRegenerating: false,
      timeoutCountdown: null,
      error: null,
      taskId: null,
      correlationId: null,
      backendSteps: [],
      cancellationReason: null,
    }));
  },
}));
