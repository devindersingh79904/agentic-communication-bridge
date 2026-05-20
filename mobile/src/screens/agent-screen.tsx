import React, { useState, useRef, useEffect } from 'react';
import {
  StyleSheet,
  Text,
  View,
  TextInput,
  TouchableOpacity,
  FlatList,
  ActivityIndicator,
} from 'react-native';
import { useAgentStore } from '../store/agent-store';
import { connectAgentWS, disconnectAgentWS } from '../services/websocket-service';
import { Message } from '../types/websocket';

const DEFAULT_STEPS = [
  { id: 'SEARCHING_VENDORS', label: 'Searching Vendors' },
  { id: 'ANALYZING_PRICING', label: 'Analyzing Pricing' },
  { id: 'DRAFTING_OUTREACH', label: 'Drafting Outreach' },
  { id: 'SELF_REFLECTION', label: 'Self Reflection' },
  { id: 'EXECUTING', label: 'Executing' },
];

export const AgentScreen = () => {
  const {
    hostUrl,
    setHostUrl,
    connectionStatus,
    taskState,
    currentAgentStep,
    currentPrompt,
    agentMessages,
    draftMessage,
    isAwaitingApproval,
    timeoutCountdown,
    error,
    sendApprovalResponse,
    sendStop,
    resetStore,
    rejectionFeedback,
    setRejectionFeedback,
    isRegenerating,
    setIsRegenerating,
    backendSteps,
    fetchMetadataEnums,
    taskId,
    correlationId,
    cancellationReason,
  } = useAgentStore();

  const [promptInput, setPromptInput] = useState('Find reliable procurement vendors for custom server hardware.');
  const [isEditingHost, setIsEditingHost] = useState(false);
  const [tempHost, setTempHost] = useState(hostUrl);
  const [isTaskIdExpanded, setIsTaskIdExpanded] = useState(false);
  const [isCorrelationIdExpanded, setIsCorrelationIdExpanded] = useState(false);

  const flatListRef = useRef<FlatList>(null);

  // Fetch dynamic metadata enums from the backend on mount
  useEffect(() => {
    fetchMetadataEnums();
  }, []);

  useEffect(() => {
    if (flatListRef.current && agentMessages.length > 0) {
      setTimeout(() => {
        flatListRef.current?.scrollToEnd({ animated: true });
      }, 100);
    }
  }, [agentMessages]);

  const handleStart = () => {
    if (!promptInput.trim()) return;
    connectAgentWS(promptInput.trim());
  };

  const handleApprove = () => {
    sendApprovalResponse('APPROVE');
  };

  const handleReject = () => {
    const feedback = rejectionFeedback.trim();
    setIsRegenerating(true);
    sendApprovalResponse('REJECT', feedback);
  };

  const handleSaveHost = () => {
    setHostUrl(tempHost.trim());
    setIsEditingHost(false);
    fetchMetadataEnums(); // Refetch enums for the new host
  };

  const getStatusColor = () => {
    if (taskState === 'FAILED') {
      return '#EF4444';
    }
    switch (connectionStatus) {
      case 'connected':
        return '#10B981';
      case 'connecting':
        return '#F59E0B';
      case 'error':
        return '#EF4444';
      default:
        return '#6B7280';
    }
  };

  const formatStepLabel = (step: string) => {
    return step
      .toLowerCase()
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');
  };

  // Build steps list dynamically using backend metadata, fallback to static defaults if not loaded yet
  const stepsToRender = backendSteps.length > 0
    ? backendSteps.map((step) => ({ id: step, label: formatStepLabel(step) }))
    : DEFAULT_STEPS;

  const getStepStyle = (stepId: string) => {
    // Use currentAgentStep (not taskState) to determine stepper progress
    const activeIndex = stepsToRender.findIndex((s) => s.id === currentAgentStep);
    const stepIndex = stepsToRender.findIndex((s) => s.id === stepId);

    if (taskState === 'SUCCESS') {
      return { container: styles.stepCompleted, text: styles.stepTextCompleted };
    }
    if (taskState === 'CANCELLED' || taskState === 'FAILED') {
      return { container: styles.stepInactive, text: styles.stepTextInactive };
    }
    if (taskState === 'WAITING_APPROVAL' || taskState === 'EXECUTING') {
      // All steps completed when waiting for approval or executing
      return { container: styles.stepCompleted, text: styles.stepTextCompleted };
    }

    if (stepId === currentAgentStep) {
      return { container: styles.stepActive, text: styles.stepTextActive };
    } else if (stepIndex < activeIndex && activeIndex !== -1) {
      return { container: styles.stepCompleted, text: styles.stepTextCompleted };
    } else {
      return { container: styles.stepInactive, text: styles.stepTextInactive };
    }
  };

  const renderMessage = ({ item }: { item: Message }) => {
    if (item.sender === 'system') {
      return (
        <View style={styles.systemMessageContainer}>
          <Text style={styles.systemMessageText}>⚙️ {item.text}</Text>
        </View>
      );
    }

    const isUser = item.sender === 'user';
    return (
      <View
        style={[
          styles.messageBubbleContainer,
          isUser ? styles.messageUserContainer : styles.messageAgentContainer,
        ]}
      >
        <View
          style={[
            styles.messageBubble,
            isUser ? styles.messageUserBubble : styles.messageAgentBubble,
          ]}
        >
          <Text style={isUser ? styles.messageRoleLabelUser : styles.messageRoleLabelAgent}>
            {isUser ? '👤 YOU' : '🤖 AGENT'}
          </Text>
          {!isUser && item.agent_step && (
            <Text style={styles.messageStepLabel}>{String(item.agent_step).replace(/_/g, ' ')}</Text>
          )}
          <Text style={styles.messageText}>{item.text}</Text>
          <Text style={styles.messageTime}>
            {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </Text>
        </View>
      </View>
    );
  };

  const isRunning = connectionStatus !== 'disconnected' || taskState === 'SCHEDULED';

  return (
    <View style={styles.screen}>
      {/* Header */}
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={styles.headerTitle}>Trybo Agentic Bridge</Text>
          <View style={styles.statusContainer}>
            <View style={[styles.statusDot, { backgroundColor: getStatusColor() }]} />
            <Text style={styles.statusText}>
              {connectionStatus.toUpperCase()} {taskState !== 'IDLE' ? `(${taskState})` : ''}
            </Text>
          </View>
          {/* Show task and correlation IDs when available */}
          {taskId && (
            <View style={styles.idBadgeCol}>
              <TouchableOpacity onPress={() => setIsTaskIdExpanded(!isTaskIdExpanded)}>
                <Text style={styles.idBadge} numberOfLines={isTaskIdExpanded ? undefined : 1}>
                  Task: {isTaskIdExpanded ? taskId : `${taskId.slice(0, 8)}…`}
                </Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => setIsCorrelationIdExpanded(!isCorrelationIdExpanded)}>
                <Text style={styles.idBadge} numberOfLines={isCorrelationIdExpanded ? undefined : 1}>
                  Corr: {correlationId ? (isCorrelationIdExpanded ? correlationId : `${correlationId.slice(0, 8)}…`) : '–'}
                </Text>
              </TouchableOpacity>
            </View>
          )}
          {/* Show active prompt */}
          {currentPrompt && taskState !== 'IDLE' && (
            <Text style={styles.promptSubtitle} numberOfLines={1}>
              "{currentPrompt}"
            </Text>
          )}
        </View>

        <View style={styles.hostConfigContainer}>
          {isEditingHost ? (
            <View style={styles.hostInputRow}>
              <TextInput
                style={styles.hostInput}
                value={tempHost}
                onChangeText={setTempHost}
                placeholder="localhost:8000"
                placeholderTextColor="#9CA3AF"
                autoCapitalize="none"
                autoCorrect={false}
              />
              <TouchableOpacity style={styles.saveHostButton} onPress={handleSaveHost}>
                <Text style={styles.saveHostText}>Save</Text>
              </TouchableOpacity>
            </View>
          ) : (
            <TouchableOpacity
              onPress={() => {
                if (connectionStatus === 'disconnected') {
                  setIsEditingHost(true);
                }
              }}
              disabled={connectionStatus !== 'disconnected'}
              style={styles.hostBadge}
            >
              <Text style={styles.hostBadgeText}>{hostUrl}</Text>
              {connectionStatus === 'disconnected' && (
                <Text style={styles.hostEditHint}> ✎</Text>
              )}
            </TouchableOpacity>
          )}
        </View>
      </View>

      {/* Stepper progress */}
      {taskState !== 'IDLE' && (
        <View style={styles.stepperContainer}>
          {stepsToRender.map((step, index) => {
            const stylesStep = getStepStyle(step.id);
            return (
              <React.Fragment key={step.id}>
                <View style={[styles.stepItem, stylesStep.container]}>
                  <Text style={[styles.stepLabel, stylesStep.text]}>{step.label}</Text>
                </View>
                {index < stepsToRender.length - 1 && (
                  <View style={styles.stepConnector} />
                )}
              </React.Fragment>
            );
          })}
        </View>
      )}

      {/* Message Flatlist */}
      <FlatList
        ref={flatListRef}
        data={agentMessages}
        renderItem={renderMessage}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.chatListContent}
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyTitle}>Welcome to Trybo Agent</Text>
            <Text style={styles.emptySubtitle}>
              Initialize a task and see the human-in-the-loop agent run live.
            </Text>
          </View>
        }
      />

      {/* Awaiting Approval panel */}
      {isAwaitingApproval && draftMessage && taskState !== 'EXECUTING' && (
        <View style={styles.approvalPanel}>
          <View style={styles.approvalHeader}>
            <Text style={styles.approvalTitle}>Awaiting Outreach Approval</Text>
            {timeoutCountdown !== null && !isRegenerating && (
              <Text style={styles.timeoutCountdownText}>
                Cancelling automatically in {timeoutCountdown}s
              </Text>
            )}
          </View>
          <View style={styles.draftCard}>
            <Text style={styles.draftLabel}>GENERATED OUTREACH DRAFT</Text>
            <Text style={styles.draftText}>{draftMessage}</Text>
          </View>

          {isRegenerating ? (
            <View style={styles.loaderRow}>
              <ActivityIndicator size="small" color="#60A5FA" />
              <Text style={styles.runningText}>Regenerating draft using your feedback...</Text>
            </View>
          ) : (
            <>
              <TextInput
                style={styles.feedbackInput}
                value={rejectionFeedback}
                onChangeText={setRejectionFeedback}
                placeholder="Optional feedback for regeneration..."
                placeholderTextColor="#9CA3AF"
                multiline
              />
              <View style={styles.approvalActions}>
                <TouchableOpacity style={styles.approveButton} onPress={handleApprove}>
                  <Text style={styles.actionButtonText}>Approve</Text>
                </TouchableOpacity>
                <TouchableOpacity style={styles.rejectButton} onPress={handleReject}>
                  <Text style={styles.actionButtonText}>Reject</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.stopApprovalButton}
                  onPress={sendStop}
                  disabled={taskState === 'CANCELLED'}
                >
                  <Text style={styles.actionButtonText}>Stop</Text>
                </TouchableOpacity>
              </View>
            </>
          )}
        </View>
      )}

      {/* Footer controls */}
      <View style={styles.footer}>
        {taskState === 'FAILED' && error && (
          <View style={styles.errorBanner}>
            <Text style={styles.errorText}>⚠️ AI Execution Failed</Text>
            <Text style={styles.errorSubText}>{error}</Text>
            <Text style={styles.retryHintText}>Please verify configuration and try starting a new run.</Text>
          </View>
        )}

        {taskState === 'CANCELLED' && (
          <View style={styles.cancelledBanner}>
            <Text style={styles.cancelledText}>
              ⏹️ {cancellationReason === 'timeout'
                ? 'Task cancelled automatically due to approval timeout.'
                : 'Task cancelled by user.'}
            </Text>
            <Text style={styles.retryHintText}>You can initialize a new run below.</Text>
          </View>
        )}

        {!isRunning ? (
          <View style={styles.inputContainer}>
            <TextInput
              style={styles.input}
              value={promptInput}
              onChangeText={setPromptInput}
              placeholder="Enter procurement instructions..."
              placeholderTextColor="#9CA3AF"
              multiline
            />
            <TouchableOpacity style={styles.startButton} onPress={handleStart}>
              <Text style={styles.startButtonText}>Start Run</Text>
            </TouchableOpacity>
          </View>
        ) : (
          !isAwaitingApproval && taskState !== 'SUCCESS' && taskState !== 'CANCELLED' && taskState !== 'FAILED' && (
            <View style={styles.runningContainer}>
              <View style={styles.loaderRow}>
                <ActivityIndicator size="small" color="#60A5FA" />
                <Text style={styles.runningText}>
                  {taskState === 'EXECUTING' ? 'Executing approved workflow...' : 'Agent executing workflow steps...'}
                </Text>
              </View>
              <TouchableOpacity
                style={styles.stopButton}
                onPress={sendStop}
                disabled={connectionStatus !== 'connected'}
              >
                <Text style={styles.stopButtonText}>Stop Agent</Text>
              </TouchableOpacity>
            </View>
          )
        )}

        {agentMessages.length > 0 && connectionStatus === 'disconnected' && (
          <TouchableOpacity style={styles.resetButton} onPress={() => resetStore(true)}>
            <Text style={styles.resetButtonText}>Clear Session</Text>
          </TouchableOpacity>
        )}
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: '#0F172A',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#1E293B',
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#F8FAFC',
  },
  statusContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 4,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 6,
  },
  statusText: {
    fontSize: 11,
    color: '#94A3B8',
    fontWeight: '600',
  },
  idBadge: {
    fontSize: 9,
    color: '#64748B',
    marginTop: 2,
    fontFamily: 'monospace',
  },
  promptSubtitle: {
    fontSize: 11,
    color: '#60A5FA',
    marginTop: 2,
    fontStyle: 'italic',
  },
  hostConfigContainer: {
    justifyContent: 'center',
  },
  hostInputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#1E293B',
    borderRadius: 6,
    paddingHorizontal: 8,
  },
  hostInput: {
    color: '#F8FAFC',
    fontSize: 12,
    paddingVertical: 6,
    width: 110,
  },
  saveHostButton: {
    paddingHorizontal: 8,
    paddingVertical: 6,
  },
  saveHostText: {
    color: '#60A5FA',
    fontSize: 12,
    fontWeight: 'bold',
  },
  hostBadge: {
    flexDirection: 'row',
    backgroundColor: '#1E293B',
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 12,
    alignItems: 'center',
  },
  hostBadgeText: {
    color: '#E2E8F0',
    fontSize: 12,
  },
  hostEditHint: {
    color: '#94A3B8',
    fontSize: 10,
  },
  stepperContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#1E293B',
    paddingHorizontal: 10,
    paddingVertical: 8,
  },
  stepItem: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 4,
    borderRadius: 4,
  },
  stepActive: {
    backgroundColor: '#2563EB',
  },
  stepCompleted: {
    backgroundColor: '#059669',
  },
  stepInactive: {
    backgroundColor: '#334155',
  },
  stepLabel: {
    fontSize: 9,
    fontWeight: 'bold',
    textAlign: 'center',
  },
  stepTextActive: {
    color: '#FFFFFF',
  },
  stepTextCompleted: {
    color: '#E2E8F0',
  },
  stepTextInactive: {
    color: '#94A3B8',
  },
  stepConnector: {
    width: 4,
    height: 1,
    backgroundColor: '#475569',
  },
  chatListContent: {
    flexGrow: 1,
    padding: 16,
  },
  emptyContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingVertical: 80,
  },
  emptyTitle: {
    fontSize: 20,
    fontWeight: 'bold',
    color: '#F8FAFC',
    marginBottom: 8,
  },
  emptySubtitle: {
    fontSize: 14,
    color: '#94A3B8',
    textAlign: 'center',
    paddingHorizontal: 32,
  },
  messageBubbleContainer: {
    flexDirection: 'row',
    marginBottom: 12,
    width: '100%',
  },
  messageUserContainer: {
    justifyContent: 'flex-end',
  },
  messageAgentContainer: {
    justifyContent: 'flex-start',
  },
  messageBubble: {
    maxWidth: '85%',
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 16,
  },
  messageUserBubble: {
    backgroundColor: '#1D4ED8',
    borderBottomRightRadius: 2,
  },
  messageAgentBubble: {
    backgroundColor: '#1E293B',
    borderBottomLeftRadius: 2,
  },
  messageStepLabel: {
    fontSize: 9,
    color: '#60A5FA',
    fontWeight: 'bold',
    marginBottom: 4,
    textTransform: 'uppercase',
  },
  messageText: {
    color: '#F8FAFC',
    fontSize: 14,
    lineHeight: 20,
  },
  messageTime: {
    fontSize: 9,
    color: '#94A3B8',
    alignSelf: 'flex-end',
    marginTop: 4,
  },
  systemMessageContainer: {
    alignSelf: 'center',
    backgroundColor: '#334155',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 12,
    marginVertical: 10,
  },
  systemMessageText: {
    color: '#CBD5E1',
    fontSize: 12,
    textAlign: 'center',
  },
  approvalPanel: {
    backgroundColor: '#1E293B',
    borderTopWidth: 2,
    borderTopColor: '#2563EB',
    padding: 16,
  },
  approvalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  approvalTitle: {
    color: '#F8FAFC',
    fontSize: 16,
    fontWeight: 'bold',
  },
  timeoutCountdownText: {
    color: '#F59E0B',
    fontSize: 12,
    fontWeight: 'bold',
  },
  draftCard: {
    backgroundColor: '#0F172A',
    borderWidth: 1,
    borderColor: '#334155',
    borderRadius: 8,
    padding: 12,
    marginBottom: 16,
  },
  draftLabel: {
    fontSize: 10,
    color: '#60A5FA',
    fontWeight: 'bold',
    marginBottom: 6,
  },
  draftText: {
    color: '#E2E8F0',
    fontSize: 13,
    lineHeight: 18,
  },
  approvalActions: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  approveButton: {
    flex: 1,
    backgroundColor: '#10B981',
    paddingVertical: 12,
    borderRadius: 8,
    marginRight: 4,
    alignItems: 'center',
  },
  rejectButton: {
    flex: 1,
    backgroundColor: '#EF4444',
    paddingVertical: 12,
    borderRadius: 8,
    marginHorizontal: 4,
    alignItems: 'center',
  },
  stopApprovalButton: {
    flex: 1,
    backgroundColor: '#475569',
    paddingVertical: 12,
    borderRadius: 8,
    marginLeft: 4,
    alignItems: 'center',
  },
  feedbackInput: {
    backgroundColor: '#0F172A',
    color: '#F8FAFC',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 13,
    minHeight: 60,
    marginBottom: 12,
    borderColor: '#334155',
    borderWidth: 1,
  },
  actionButtonText: {
    color: '#FFFFFF',
    fontWeight: 'bold',
    fontSize: 14,
  },
  footer: {
    backgroundColor: '#0F172A',
    borderTopWidth: 1,
    borderTopColor: '#1E293B',
    padding: 16,
  },
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  input: {
    flex: 1,
    backgroundColor: '#1E293B',
    color: '#F8FAFC',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    marginRight: 8,
    maxHeight: 80,
  },
  startButton: {
    backgroundColor: '#2563EB',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 8,
    justifyContent: 'center',
  },
  startButtonText: {
    color: '#FFFFFF',
    fontWeight: 'bold',
    fontSize: 14,
  },
  runningContainer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 4,
  },
  loaderRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  runningText: {
    color: '#94A3B8',
    fontSize: 13,
    marginLeft: 8,
  },
  stopButton: {
    backgroundColor: '#EF4444',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 6,
  },
  stopButtonText: {
    color: '#FFFFFF',
    fontWeight: '600',
    fontSize: 12,
  },
  resetButton: {
    alignSelf: 'center',
    marginTop: 10,
    padding: 6,
  },
  resetButtonText: {
    color: '#64748B',
    fontSize: 12,
    fontWeight: '500',
  },
  errorBanner: {
    backgroundColor: 'rgba(239, 68, 68, 0.15)',
    borderColor: '#EF4444',
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    marginBottom: 12,
  },
  errorText: {
    color: '#F87171',
    fontSize: 13,
    fontWeight: 'bold',
  },
  errorSubText: {
    color: '#FCA5A5',
    fontSize: 12,
    marginTop: 2,
    lineHeight: 16,
  },
  retryHintText: {
    color: '#94A3B8',
    fontSize: 11,
    marginTop: 4,
    fontWeight: '600',
  },
  idBadgeCol: {
    flexDirection: 'column',
    marginTop: 2,
    alignItems: 'flex-start',
  },
  cancelledBanner: {
    backgroundColor: 'rgba(245, 158, 11, 0.15)',
    borderColor: '#F59E0B',
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    marginBottom: 12,
  },
  cancelledText: {
    color: '#F59E0B',
    fontSize: 13,
    fontWeight: 'bold',
  },
  messageRoleLabelUser: {
    fontSize: 9,
    color: '#93C5FD',
    fontWeight: 'bold',
    marginBottom: 2,
    textTransform: 'uppercase',
  },
  messageRoleLabelAgent: {
    fontSize: 9,
    color: '#34D399',
    fontWeight: 'bold',
    marginBottom: 2,
    textTransform: 'uppercase',
  },
});
