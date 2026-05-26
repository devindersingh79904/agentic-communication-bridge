export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

export type TaskState =
  | 'IDLE'
  | 'SCHEDULED'
  | 'RUNNING'
  | 'SEARCHING_VENDORS'
  | 'EXTERNAL_SEARCHING'
  | 'WAITING_VENDOR_SELECTION'
  | 'ANALYZING_PRICING'
  | 'WAITING_PRICE_APPROVAL'
  | 'DRAFTING_OUTREACH'
  | 'SELF_REFLECTION'
  | 'WAITING_FINAL_APPROVAL'
  | 'COMPLETED'
  | 'SUCCESS'
  | 'FAILED'
  | 'CANCELLED'
  | 'FAILED_RETRYING';

export type AgentStep =
  | 'SEARCHING_VENDORS'
  | 'ANALYZING_PRICING'
  | 'DRAFTING_OUTREACH'
  | 'SELF_REFLECTION'
  | 'EXECUTING';

export interface BaseWebSocketEvent {
  event_type: string;
  correlation_id: string;
  task_id?: string;
  timestamp?: string;
}

export interface VendorResult {
  vendor_name?: string;
  name?: string;
  category?: string;
  rating?: number | string;
  delivery_days?: number;
  location?: string;
  confidence_score?: number;
  score?: number;
  source_type?: 'internal' | 'external' | string;
  catalog?: Record<string, string | number>;
  items?: Record<string, string | number>;
  catalog_items?: any;
}

export interface PricingAnalysis {
  summary?: string;
  selected_vendor?: VendorResult;
  reasoning?: string[];
  confidence?: number;
}

export interface ReflectionMetadata {
  tone_check_passed: boolean;
  hallucination_free: boolean;
  formatting_valid: boolean;
  confidence_score: number;
  critique: string;
}

export interface StatusUpdateEvent extends BaseWebSocketEvent {
  event_type: 'STATUS_UPDATE';
  task_state: TaskState;
  agent_step: AgentStep;
  message: string;
  vendors?: VendorResult[];
  selected_vendor?: VendorResult;
  pricing_analysis?: PricingAnalysis;
}

export interface ApprovalRequiredEvent extends BaseWebSocketEvent {
  event_type: 'APPROVAL_REQUIRED';
  task_state: TaskState;
  agent_step: AgentStep;
  draft_message: string;
  step_data?: string;
  message: string;
  approval_timeout_seconds?: number;
  reflection_metadata?: ReflectionMetadata;
  vendors?: VendorResult[];
  selected_vendor?: VendorResult;
  pricing_analysis?: PricingAnalysis;
}

export interface TaskCompletedEvent extends BaseWebSocketEvent {
  event_type: 'TASK_COMPLETED';
  task_state: 'SUCCESS';
  message: string;
  final_response?: string;
  vendors?: VendorResult[];
  selected_vendor?: VendorResult;
  pricing_analysis?: PricingAnalysis;
}

export interface TaskCancelledEvent extends BaseWebSocketEvent {
  event_type: 'TASK_CANCELLED';
  task_state: 'CANCELLED';
  message: string;
}

export interface ErrorEvent extends BaseWebSocketEvent {
  event_type: 'ERROR';
  task_state: 'FAILED';
  error_code: string;
  message: string;
}

export interface PingEvent extends BaseWebSocketEvent {
  event_type: 'PING';
}

export interface PongEvent extends BaseWebSocketEvent {
  event_type: 'PONG';
}

export type ServerEvent =
  | StatusUpdateEvent
  | ApprovalRequiredEvent
  | TaskCompletedEvent
  | TaskCancelledEvent
  | ErrorEvent
  | PingEvent
  | PongEvent;

export type ApprovalAction = 'APPROVE' | 'REJECT' | 'MODIFY_REQUEST';

export interface ClientStartTaskEvent {
  event_type: 'START_TASK';
  prompt: string;
}

export interface ClientApprovalResponseEvent {
  event_type: 'APPROVAL_RESPONSE';
  action: ApprovalAction;
  feedback?: string;
  correlation_id: string;
  task_id: string;
}

export interface ClientStopEvent {
  event_type: 'STOP';
  correlation_id: string;
  task_id: string;
}

export interface ClientPongEvent {
  event_type: 'PONG';
  correlation_id: string;
  task_id: string;
}

export type ClientEvent =
  | ClientStartTaskEvent
  | ClientApprovalResponseEvent
  | ClientStopEvent
  | ClientPongEvent;

export interface Message {
  id: string;
  sender: 'user' | 'agent' | 'system';
  text: string;
  timestamp: Date;
  agent_step?: AgentStep;
}

export interface TaskHistoryItem {
  task_id: string;
  prompt: string;
  status: TaskState;
  timestamp: Date;
  selected_vendor?: VendorResult;
  final_response?: string;
}
