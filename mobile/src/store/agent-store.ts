import { create } from 'zustand';
import { ConnectionStatus, TaskState, AgentStep, Message, ApprovalAction, VendorResult, PricingAnalysis, ReflectionMetadata, TaskHistoryItem } from '../types/websocket';
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
  currentPendingStep: AgentStep | null;
  currentStepData: string | null;

  // New states
  taskHistory: TaskHistoryItem[];
  vendorResults: VendorResult[];
  selectedVendor: VendorResult | null;
  pricingAnalysis: PricingAnalysis | null;
  reflectionMetadata: ReflectionMetadata | null;
  confidenceScore: number | null;
  lastActivityTime: number;

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
  setCurrentPendingStep: (step: AgentStep | null) => void;
  setCurrentStepData: (data: string | null) => void;

  // New actions
  setVendorResults: (vendors: VendorResult[]) => void;
  setSelectedVendor: (vendor: VendorResult | null) => void;
  setPricingAnalysis: (analysis: PricingAnalysis | null) => void;
  setReflectionMetadata: (metadata: ReflectionMetadata | null) => void;
  setConfidenceScore: (score: number | null) => void;
  updateLastActivity: () => void;
  addToHistory: (item: TaskHistoryItem) => void;
  updateHistoryItem: (taskId: string, updates: Partial<TaskHistoryItem>) => void;

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
  currentPendingStep: null,
  currentStepData: null,

  // Initial new states
  taskHistory: [],
  vendorResults: [],
  selectedVendor: null,
  pricingAnalysis: null,
  reflectionMetadata: null,
  confidenceScore: null,
  lastActivityTime: Date.now(),

  setCurrentPendingStep: (currentPendingStep) => set({ currentPendingStep }),
  setCurrentStepData: (currentStepData) => set({ currentStepData }),

  setHostUrl: (hostUrl) => set({ hostUrl }),
  setSocket: (socket) => set({ socket }),
  setConnectionStatus: (connectionStatus) => set({ connectionStatus }),
  
  updateTaskState: (taskState) => {
    set({ taskState });
    const { taskId } = get();
    if (taskId) {
      get().updateHistoryItem(taskId, { status: taskState });
    }
  },

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

  setIds: (taskId, correlationId) =>
    set((state) => {
      const prevTaskId = state.taskId;
      let newHistory = state.taskHistory;
      if (prevTaskId && prevTaskId.startsWith('task-opt-') && taskId) {
        newHistory = state.taskHistory.map((item) =>
          item.task_id === prevTaskId ? { ...item, task_id: taskId } : item
        );
      }
      return {
        taskId,
        correlationId,
        taskHistory: newHistory,
      };
    }),

  setCancellationReason: (cancellationReason) => set({ cancellationReason }),

  // New state setters
  setVendorResults: (vendorResults) => set({ vendorResults }),
  setSelectedVendor: (selectedVendor) => set({ selectedVendor }),
  setPricingAnalysis: (pricingAnalysis) => set({ pricingAnalysis }),
  setReflectionMetadata: (reflectionMetadata) => {
    set({ reflectionMetadata });
    if (reflectionMetadata && reflectionMetadata.confidence_score !== undefined) {
      set({ confidenceScore: reflectionMetadata.confidence_score });
    }
  },
  setConfidenceScore: (confidenceScore) => set({ confidenceScore }),
  updateLastActivity: () => set({ lastActivityTime: Date.now() }),

  addToHistory: (item) =>
    set((state) => {
      // Prevent duplicates in history
      const exists = state.taskHistory.some((h) => h.task_id === item.task_id);
      if (exists) return {};
      return {
        taskHistory: [item, ...state.taskHistory],
      };
    }),

  updateHistoryItem: (taskId, updates) =>
    set((state) => ({
      taskHistory: state.taskHistory.map((item) =>
        item.task_id === taskId ? { ...item, ...updates } : item
      ),
    })),

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

    // Reset previous session state but preserve hostUrl, backendSteps, and taskHistory
    const existingHistory = get().taskHistory;
    get().resetStore(true);
    set({ taskHistory: existingHistory });

    // Store the current prompt
    set({ currentPrompt: prompt });

    // Optimistic UI: immediately set SCHEDULED state and create task ID
    const tempTaskId = `task-opt-${Math.random().toString(36).slice(2, 11)}`;
    set({ taskState: 'SCHEDULED', taskId: tempTaskId });

    get().addToHistory({
      task_id: tempTaskId,
      prompt: prompt,
      status: 'SCHEDULED',
      timestamp: new Date(),
    });

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

    const feedbackMessage = feedback && feedback.trim() ? feedback.trim() : '';

    if (action === 'APPROVE') {
      get().appendMessage({
        sender: 'user',
        text: feedbackMessage
          ? `✅ Approved outreach proposal with feedback: "${feedbackMessage}"`
          : `✅ Approved outreach proposal. Proceeding.`,
      });
    } else if (action === 'REJECT') {
      get().appendMessage({
        sender: 'user',
        text: feedbackMessage
          ? `❌ Rejected outreach proposal. Feedback: "${feedbackMessage}"`
          : `❌ Rejected outreach proposal. Re-running.`,
      });
      set({ isRegenerating: true });
    }

    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(
        JSON.stringify({
          event_type: CLIENT_EVENTS.APPROVAL_RESPONSE,
          action,
          feedback: feedbackMessage || undefined,
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

    if (taskId) {
      get().updateHistoryItem(taskId, { status: 'CANCELLED' });
    }

    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(
        JSON.stringify({
          event_type: CLIENT_EVENTS.STOP,
          task_id: taskId,
          correlation_id: correlationId,
        })
      );
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
      cancellationReason: null,
      currentPendingStep: null,
      currentStepData: null,
      
      // Reset new states
      vendorResults: [],
      selectedVendor: null,
      pricingAnalysis: null,
      reflectionMetadata: null,
      confidenceScore: null,
    }));
  },
}));