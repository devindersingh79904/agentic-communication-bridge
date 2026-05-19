export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

export type TaskState =
  | 'IDLE'
  | 'SEARCHING_VENDORS'
  | 'ANALYZING_PRICING'
  | 'DRAFTING_OUTREACH'
  | 'SELF_REFLECTION'
  | 'WAITING_APPROVAL'
  | 'SUCCESS'
  | 'CANCELLED'
  | 'FAILED';

export interface BaseWebSocketEvent {
  event_type: string;
  correlation_id: string;
  task_id: string;
}

export interface StatusUpdateEvent extends BaseWebSocketEvent {
  event_type: 'STATUS_UPDATE';
  task_state: TaskState;
  agent_step: string;
  message: string;
}

export interface ApprovalRequiredEvent extends BaseWebSocketEvent {
  event_type: 'APPROVAL_REQUIRED';
  task_state: 'WAITING_APPROVAL';
  draft_message: string;
  message: string;
  approval_timeout_seconds?: number;
}

export interface TaskCompletedEvent extends BaseWebSocketEvent {
  event_type: 'TASK_COMPLETED';
  task_state: 'SUCCESS';
  message: string;
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

export type ServerEvent =
  | StatusUpdateEvent
  | ApprovalRequiredEvent
  | TaskCompletedEvent
  | TaskCancelledEvent
  | ErrorEvent;

export interface ClientApproveEvent {
  event_type: 'APPROVED';
  correlation_id: string;
  task_id: string;
}

export interface ClientStopEvent {
  event_type: 'STOP';
  correlation_id: string;
  task_id: string;
}

export type ClientEvent = ClientApproveEvent | ClientStopEvent;

export interface Message {
  id: string;
  sender: 'user' | 'agent' | 'system';
  text: string;
  timestamp: Date;
  agent_step?: string;
}
