import React, { useState, useRef, useEffect } from 'react';
import {
  StyleSheet,
  Text,
  View,
  TextInput,
  TouchableOpacity,
  FlatList,
  ActivityIndicator,
  ScrollView,
  Modal,
} from 'react-native';
import { useAgentStore } from '../store/agent-store';
import { connectAgentWS } from '../services/websocket-service';
import { Message, TaskHistoryItem } from '../types/websocket';

const DEFAULT_STEPS = [
  { id: 'SEARCHING_VENDORS', label: 'Vendor Search' },
  { id: 'ANALYZING_PRICING', label: 'Pricing Analysis' },
  { id: 'DRAFTING_OUTREACH', label: 'Draft Outreach' },
  { id: 'SELF_REFLECTION', label: 'Self Review' },
  { id: 'EXECUTING', label: 'Execute' },
];

export const AgentScreen = () => {
  const {
    hostUrl,
    setHostUrl,
    connectionStatus,
    taskState,
    currentAgentStep,
    agentMessages,
    isAwaitingApproval,
    timeoutCountdown,
    error,
    sendApprovalResponse,
    sendStop,
    resetStore,
    backendSteps,
    fetchMetadataEnums,
    taskId,
    cancellationReason,
    taskHistory,
    isRegenerating,
    vendorResults,
    currentPendingStep,
    draftMessage,
    reflectionMetadata,
    selectedVendor,
    confidenceScore,
    finalEmail,
  } = useAgentStore();

  const [promptInput, setPromptInput] = useState('Find reliable procurement vendors for custom server hardware.');
  const [isEditingHost, setIsEditingHost] = useState(false);
  const [tempHost, setTempHost] = useState(hostUrl);
  const [isHistoryVisible, setIsHistoryVisible] = useState(false);
  const [feedbackInput, setFeedbackInput] = useState(''); // Will be sent as user feedback on rejection

  const flatListRef = useRef<FlatList>(null);
  const scrollViewRef = useRef<ScrollView>(null);

  useEffect(() => {
    try {
      fetchMetadataEnums();
    } catch (err) {
      console.error('Error during app initialization:', err);
      // App can still work without metadata
    }
  }, []);

  useEffect(() => {
    if (flatListRef.current && agentMessages.length > 0) {
      setTimeout(() => {
        flatListRef.current?.scrollToEnd({ animated: true });
      }, 100);
    }
  }, [agentMessages]);

  useEffect(() => {
    if (isAwaitingApproval && scrollViewRef.current) {
      setTimeout(() => {
        scrollViewRef.current?.scrollToEnd({ animated: true });
      }, 200);
    }
  }, [isAwaitingApproval]);


  const handleStart = () => {
    if (!promptInput.trim()) return;
    connectAgentWS(promptInput.trim());
    // Show prompt in chat immediately
    setPromptInput('');
  };

  const isStartButtonEnabled = promptInput.trim().length > 0;

  const handleApprove = () => {
    const feedback = feedbackInput.trim();

    // Send feedback and vendor list to backend for LLM-based semantic understanding
    sendApprovalResponse('APPROVE', feedback || undefined, vendorResults);
    setFeedbackInput('');
  };

  const handleReject = () => {
    const feedback = feedbackInput.trim() || 'Not acceptable';
    sendApprovalResponse('REJECT', feedback, undefined);
    setFeedbackInput('');
  };

  const handleStop = () => {
    sendStop();
  };

  const getStatusColor = () => {
    if (taskState === 'FAILED') return '#EF4444';
    if (taskState === 'FAILED_RETRYING') return '#F59E0B';
    switch (connectionStatus) {
      case 'connected': return '#10B981';
      case 'connecting': return '#F59E0B';
      case 'error': return '#EF4444';
      default: return '#6B7280';
    }
  };

  const renderMessage = ({ item }: { item: Message }) => {
    const isUser = item.sender === 'user';
    if (item.sender === 'system') {
      return (
        <View style={styles.systemMessageContainer}>
          <Text style={styles.systemMessageText}>⚙️ {item.text}</Text>
        </View>
      );
    }

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
            {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </Text>
        </View>
      </View>
    );
  };

  const renderTaskHistoryItem = (item: TaskHistoryItem) => {
    const isCurrent = item.task_id === taskId;
    return (
      <TouchableOpacity
        key={item.task_id}
        style={[styles.historyCard, isCurrent && styles.historyCardCurrent]}
      >
        <View style={styles.historyCardHeader}>
          <Text style={styles.historyTime} numberOfLines={1}>
            {new Date(item.timestamp).toLocaleDateString()} {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </Text>
          <View style={[styles.statusBadge, item.status === 'SUCCESS' ? styles.statusBadgeSuccess : styles.statusBadgeFail]}>
            <Text style={styles.statusBadgeText}>{item.status}</Text>
          </View>
        </View>
        <Text style={styles.historyPrompt} numberOfLines={2}>"{item.prompt}"</Text>
      </TouchableOpacity>
    );
  };

  const isRunning = connectionStatus !== 'disconnected' || taskState === 'SCHEDULED';

  const getStepStyle = (stepId: string) => {
    const stepsToRender = backendSteps.length > 0 ? backendSteps : DEFAULT_STEPS.map(s => s.id);
    const activeIndex = stepsToRender.findIndex((s) => s === currentAgentStep);
    const pendingIndex = stepsToRender.findIndex((s) => s === currentPendingStep);
    const stepIndex = stepsToRender.findIndex((s) => s === stepId);

    if (taskState === 'SUCCESS') {
      return { container: styles.stepCompleted, text: styles.stepTextCompleted };
    }
    if (taskState === 'CANCELLED' || taskState === 'FAILED') {
      return { container: styles.stepInactive, text: styles.stepTextInactive };
    }
    
    // If waiting for human approval at a gate
    if (taskState && taskState.startsWith('WAITING_')) {
      let targetActiveStepIndex = pendingIndex;
      if (taskState === 'WAITING_VENDOR_SELECTION') {
        // We completed SEARCHING_VENDORS (index 0). The next pending stage is ANALYZING_PRICING (index 1).
        targetActiveStepIndex = stepsToRender.findIndex((s) => s === 'ANALYZING_PRICING');
      } else if (taskState === 'WAITING_PRICE_APPROVAL') {
        // We completed ANALYZING_PRICING. The next pending stage is DRAFTING_OUTREACH.
        targetActiveStepIndex = stepsToRender.findIndex((s) => s === 'DRAFTING_OUTREACH');
      } else if (taskState === 'WAITING_FINAL_APPROVAL') {
        // We completed SELF_REFLECTION. The next pending stage is EXECUTING.
        targetActiveStepIndex = stepsToRender.findIndex((s) => s === 'EXECUTING');
      }

      if (targetActiveStepIndex !== -1) {
        if (stepIndex < targetActiveStepIndex) {
          return { container: styles.stepCompleted, text: styles.stepTextCompleted };
        }
        if (stepIndex === targetActiveStepIndex) {
          return { container: styles.stepPending, text: styles.stepTextActive };
        }
      }
      return { container: styles.stepInactive, text: styles.stepTextInactive };
    }

    // Normal active execution steps
    if (activeIndex !== -1) {
      if (stepIndex < activeIndex) {
        return { container: styles.stepCompleted, text: styles.stepTextCompleted };
      }
      if (stepId === currentAgentStep) {
        return { container: styles.stepActive, text: styles.stepTextActive };
      }
    }
    
    return { container: styles.stepInactive, text: styles.stepTextInactive };
  };

  const stepsToRender = backendSteps.length > 0
    ? backendSteps.map((step) => ({ id: step, label: step.replace(/_/g, ' ') }))
    : DEFAULT_STEPS;

  return (
    <View style={styles.screen}>
      {/* Header */}
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={styles.headerTitle}>Trybo Agentic Bridge</Text>
          <View style={styles.statusContainer}>
            <View style={[styles.statusDot, { backgroundColor: getStatusColor() }]} />
            <Text style={styles.statusText}>
              {taskState === 'FAILED_RETRYING' ? 'RETRYING' : connectionStatus.toUpperCase()} {taskState !== 'IDLE' ? `(${taskState})` : ''}
            </Text>
          </View>
          {taskId && (
            <Text style={styles.idBadge}>Task: {taskId.slice(0, 18)}…</Text>
          )}
        </View>

        <View style={styles.headerRightActions}>
          <TouchableOpacity style={styles.historyButton} onPress={() => setIsHistoryVisible(true)}>
            <Text style={styles.historyButtonText}>📜 Runs ({taskHistory.length})</Text>
          </TouchableOpacity>

          {isEditingHost ? (
            <View style={styles.hostInputRow}>
              <TextInput
                style={styles.hostInput}
                value={tempHost}
                onChangeText={setTempHost}
                placeholder="localhost:8000"
                placeholderTextColor="#9CA3AF"
                onSubmitEditing={() => {
                  setHostUrl(tempHost.trim());
                  setIsEditingHost(false);
                }}
              />
              <TouchableOpacity
                style={styles.saveHostButton}
                onPress={() => {
                  setHostUrl(tempHost.trim());
                  setIsEditingHost(false);
                }}
              >
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
            </TouchableOpacity>
          )}
        </View>
      </View>

      {/* Stepper */}
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

      {/* Messages */}
      <ScrollView
        ref={scrollViewRef}
        style={styles.scrollArea}
        contentContainerStyle={styles.scrollContent}
        keyboardShouldPersistTaps="handled"
      >
        <FlatList
          ref={flatListRef}
          data={agentMessages}
          renderItem={renderMessage}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.chatListContent}
          scrollEnabled={false}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyTitle}>Welcome to Trybo Agent</Text>
              <Text style={styles.emptySubtitle}>
                Enter a procurement request and start the workflow.
              </Text>
            </View>
          }
        />

        {/* Vendor Results Display removed - only show in chat */}

        {/* Approval Panel - Vendor Selection */}
        {taskState === 'WAITING_VENDOR_SELECTION' && !isRegenerating && (
          <View style={styles.approvalPanel}>
            <View style={styles.waitingApprovalContainer}>
              <Text style={styles.waitingApprovalTitle}>⏳ STEP 1: VENDOR SELECTION</Text>
              <Text style={styles.waitingApprovalSubtitle}>
                Review the vendors in the chat and choose to approve, reject, or provide feedback.
              </Text>
            </View>

              {timeoutCountdown !== null && !isRegenerating && (
                <Text style={styles.timeoutText}>Auto-cancel in {timeoutCountdown}s</Text>
              )}

              {isRegenerating ? (
                <View style={styles.loaderRow}>
                  <ActivityIndicator size="small" color="#60A5FA" />
                  <Text style={styles.regeneratingText}>Re-searching with your feedback...</Text>
                </View>
              ) : (
                <>
                  <TextInput
                    style={styles.feedbackInput}
                    value={feedbackInput}
                    onChangeText={setFeedbackInput}
                    placeholder="e.g., 'use ByteEdge Systems' or 'find cheaper options'"
                    placeholderTextColor="#9CA3AF"
                    multiline
                    onKeyPress={(e: any) => {
                      if (e.nativeEvent.key === 'Enter' && !e.shiftKey) {
                        if (typeof e.preventDefault === 'function') {
                          e.preventDefault();
                        }
                        if (connectionStatus === 'connected') {
                          handleApprove();
                        }
                      }
                    }}
                  />
                  {connectionStatus !== 'connected' && (
                    <Text style={styles.reconnectingText}>
                      ⚡ Reconnecting… buttons will re-enable shortly.
                    </Text>
                  )}
                  <View style={styles.approvalActions}>
                    <TouchableOpacity
                      style={[styles.approveButton, connectionStatus !== 'connected' && styles.buttonDisconnected]}
                      onPress={handleApprove}
                      disabled={connectionStatus !== 'connected'}
                    >
                      <Text style={styles.actionButtonText}>✅ Approve</Text>
                    </TouchableOpacity>
                    <TouchableOpacity
                      style={[styles.rejectButton, connectionStatus !== 'connected' && styles.buttonDisconnected]}
                      onPress={handleReject}
                      disabled={connectionStatus !== 'connected'}
                    >
                      <Text style={styles.actionButtonText}>❌ Reject</Text>
                    </TouchableOpacity>
                    <TouchableOpacity style={styles.stopButton} onPress={handleStop}>
                      <Text style={styles.actionButtonText}>⏹ Stop</Text>
                    </TouchableOpacity>
                  </View>
                </>
              )}
            </View>
        )}

        {/* Final Email Card — shown after user accepts */}
        {taskState === 'SUCCESS' && finalEmail && (
          <View style={styles.finalEmailCard}>
            <View style={styles.finalEmailHeader}>
              <Text style={styles.finalEmailTitle}>✅ Email Approved & Sent</Text>
              {selectedVendor && (
                <Text style={styles.finalEmailVendor}>
                  To: {selectedVendor.vendor_name || selectedVendor.name}
                </Text>
              )}
            </View>
            <Text style={styles.finalEmailContent}>{finalEmail}</Text>
          </View>
        )}

        {/* Approval Panel - Final Approval with Draft Email */}
        {taskState === 'WAITING_FINAL_APPROVAL' && !isRegenerating && (
          <View style={styles.approvalPanel}>
            <View style={styles.waitingApprovalContainer}>
              <Text style={styles.waitingApprovalTitle}>🔔 STEP 2: FINAL APPROVAL</Text>
              <Text style={styles.waitingApprovalSubtitle}>
                Review the draft email and approve to send or reject to regenerate.
              </Text>
            </View>

            {/* Self-Reflection Metadata */}
            {reflectionMetadata && (
              <View style={styles.metadataBox}>
                <Text style={styles.metadataTitle}>📊 Self-Reflection Analysis:</Text>
                <Text style={styles.metadataText}>
                  • Tone: {reflectionMetadata.tone_check_passed ? '✅ Good' : '⚠️ Needs work'}
                </Text>
                <Text style={styles.metadataText}>
                  • Hallucination-free: {reflectionMetadata.hallucination_free ? '✅ Yes' : '⚠️ Detected'}
                </Text>
                <Text style={styles.metadataText}>
                  • Formatting: {reflectionMetadata.formatting_valid ? '✅ Valid' : '⚠️ Invalid'}
                </Text>
                {reflectionMetadata.critique && (
                  <Text style={styles.metadataText}>• Feedback: {reflectionMetadata.critique}</Text>
                )}
              </View>
            )}

            {/* Draft Email Display */}
            {draftMessage && (
              <View style={styles.draftBox}>
                <Text style={styles.draftTitle}>📧 Draft Email:</Text>
                {selectedVendor && (
                  <Text style={styles.draftVendor}>
                    To: {selectedVendor.vendor_name || selectedVendor.name}
                  </Text>
                )}
                <Text style={styles.draftContent}>{draftMessage}</Text>
              </View>
            )}

            {timeoutCountdown !== null && !isRegenerating && (
              <Text style={styles.timeoutText}>Auto-cancel in {timeoutCountdown}s</Text>
            )}

            {isRegenerating ? (
              <View style={styles.loaderRow}>
                <ActivityIndicator size="small" color="#60A5FA" />
                <Text style={styles.regeneratingText}>Regenerating draft based on feedback...</Text>
              </View>
            ) : (
              <>
                <TextInput
                  style={styles.feedbackInput}
                  value={feedbackInput}
                  onChangeText={setFeedbackInput}
                  placeholder="e.g., 'Make it more formal' or 'Add pricing details'"
                  placeholderTextColor="#9CA3AF"
                  multiline
                  onKeyPress={(e: any) => {
                    if (e.nativeEvent.key === 'Enter' && !e.shiftKey) {
                      if (typeof e.preventDefault === 'function') {
                        e.preventDefault();
                      }
                      if (connectionStatus === 'connected') {
                        handleApprove();
                      }
                    }
                  }}
                />
                {connectionStatus !== 'connected' && (
                  <Text style={styles.reconnectingText}>
                    ⚡ Reconnecting… buttons will re-enable shortly.
                  </Text>
                )}
                <View style={styles.approvalActions}>
                  <TouchableOpacity
                    style={[styles.approveButton, connectionStatus !== 'connected' && styles.buttonDisconnected]}
                    onPress={handleApprove}
                    disabled={connectionStatus !== 'connected'}
                  >
                    <Text style={styles.actionButtonText}>✅ Approve & Send</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[styles.rejectButton, connectionStatus !== 'connected' && styles.buttonDisconnected]}
                    onPress={handleReject}
                    disabled={connectionStatus !== 'connected'}
                  >
                    <Text style={styles.actionButtonText}>❌ Reject & Regenerate</Text>
                  </TouchableOpacity>
                  <TouchableOpacity style={styles.stopButton} onPress={handleStop}>
                    <Text style={styles.actionButtonText}>⏹ Stop</Text>
                  </TouchableOpacity>
                </View>
              </>
            )}
          </View>
        )}
      </ScrollView>

      {/* Footer */}
      <View style={styles.footer}>
        {taskState === 'FAILED' && error && (
          <View style={styles.errorBanner}>
            <Text style={styles.errorText}>⚠️ Error</Text>
            <Text style={styles.errorSubText}>{error}</Text>
          </View>
        )}

        {taskState === 'CANCELLED' && (
          <View style={styles.cancelledBanner}>
            <Text style={styles.cancelledText}>
              ⏹️ Task cancelled {cancellationReason === 'timeout' ? 'due to timeout' : 'by user'}
            </Text>
          </View>
        )}

        {!isRunning ? (
          <View style={styles.inputContainer}>
            <TextInput
              style={styles.input}
              value={promptInput}
              onChangeText={setPromptInput}
              placeholder="Enter procurement request..."
              placeholderTextColor="#9CA3AF"
              multiline
              onKeyPress={(e: any) => {
                if (e.nativeEvent.key === 'Enter' && !e.shiftKey) {
                  if (typeof e.preventDefault === 'function') {
                    e.preventDefault();
                  }
                  if (isStartButtonEnabled) {
                    handleStart();
                  }
                }
              }}
            />
            <TouchableOpacity
              style={[
                styles.startButton,
                isStartButtonEnabled ? styles.startButtonEnabled : styles.startButtonDisabled
              ]}
              onPress={handleStart}
              disabled={!isStartButtonEnabled}
            >
              <Text style={styles.startButtonText}>
                {isStartButtonEnabled ? '🚀 Start' : 'Enter text...'}
              </Text>
            </TouchableOpacity>
          </View>
        ) : (
          <View style={styles.runningContainer}>
            <View style={styles.loaderRow}>
              <ActivityIndicator size="small" color="#60A5FA" />
              <Text style={styles.runningText}>
                {currentAgentStep === 'SEARCHING_VENDORS' ? 'Searching vendors...' :
                 currentAgentStep === 'ANALYZING_PRICING' ? 'Analyzing pricing...' :
                 currentAgentStep === 'DRAFTING_OUTREACH' ? 'Drafting outreach...' :
                 currentAgentStep === 'SELF_REFLECTION' ? 'Self reviewing...' :
                 'Agent processing...'}
              </Text>
            </View>
            <TouchableOpacity
              style={styles.stopRunButton}
              onPress={sendStop}
              disabled={connectionStatus !== 'connected'}
            >
              <Text style={styles.stopRunButtonText}>⏹ Stop</Text>
            </TouchableOpacity>
          </View>
        )}

        {agentMessages.length > 0 && connectionStatus === 'disconnected' && (
          <TouchableOpacity style={styles.resetButton} onPress={() => resetStore(true)}>
            <Text style={styles.resetButtonText}>Clear Session</Text>
          </TouchableOpacity>
        )}
      </View>

      {/* History Modal */}
      <Modal
        visible={isHistoryVisible}
        animationType="slide"
        transparent={true}
        onRequestClose={() => setIsHistoryVisible(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Execution History</Text>
              <TouchableOpacity onPress={() => setIsHistoryVisible(false)}>
                <Text style={styles.closeModalButtonText}>✕</Text>
              </TouchableOpacity>
            </View>
            <FlatList
              data={taskHistory}
              renderItem={({ item }) => renderTaskHistoryItem(item)}
              keyExtractor={(item) => item.task_id}
              contentContainerStyle={styles.historyListContent}
              ListEmptyComponent={
                <View style={styles.emptyHistoryContainer}>
                  <Text style={styles.emptyHistoryText}>No prior runs.</Text>
                </View>
              }
            />
          </View>
        </View>
      </Modal>
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
  headerRightActions: {
    alignItems: 'flex-end',
    justifyContent: 'center',
  },
  historyButton: {
    backgroundColor: '#1E293B',
    borderColor: '#334155',
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 6,
    marginBottom: 6,
  },
  historyButtonText: {
    color: '#E2E8F0',
    fontSize: 11,
    fontWeight: '600',
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
    width: 100,
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
  stepPending: {
    backgroundColor: '#D97706',
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
  scrollArea: {
    flex: 1,
  },
  scrollContent: {
    flexGrow: 1,
  },
  chatListContent: {
    padding: 16,
    paddingBottom: 8,
  },
  emptyContainer: {
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
  searchCompleteMessageContainer: {
    alignSelf: 'center',
    backgroundColor: 'rgba(5, 150, 105, 0.2)',
    borderColor: '#059669',
    borderWidth: 1,
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 12,
    marginVertical: 10,
    marginHorizontal: 16,
  },
  searchCompleteMessage: {
    color: '#34D399',
    fontSize: 13,
    textAlign: 'center',
    fontWeight: '600',
  },
  vendorResultsContainer: {
    backgroundColor: '#1E293B',
    borderTopWidth: 2,
    borderTopColor: '#059669',
    padding: 16,
    marginHorizontal: 16,
    marginBottom: 16,
    borderRadius: 8,
  },
  vendorResultsTitle: {
    color: '#34D399',
    fontWeight: 'bold',
    fontSize: 13,
    marginBottom: 12,
  },
  vendorResultCard: {
    backgroundColor: '#0F172A',
    borderColor: '#334155',
    borderWidth: 1,
    borderRadius: 6,
    padding: 10,
    marginBottom: 8,
  },
  vendorResultName: {
    color: '#F8FAFC',
    fontSize: 12,
    fontWeight: '600',
  },
  vendorResultCategory: {
    color: '#93C5FD',
    fontSize: 10,
    marginTop: 4,
  },
  vendorResultLocation: {
    color: '#CBD5E1',
    fontSize: 10,
    marginTop: 2,
  },
  vendorResultRating: {
    color: '#FCD34D',
    fontSize: 10,
    marginTop: 2,
  },
  vendorResultCardTop: {
    borderColor: '#10B981',
    borderWidth: 2,
    backgroundColor: 'rgba(16, 185, 129, 0.05)',
  },
  topVendorBadge: {
    backgroundColor: '#10B981',
    color: '#FFFFFF',
    fontSize: 9,
    fontWeight: 'bold',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 4,
    alignSelf: 'flex-start',
    marginBottom: 8,
  },
  otherVendorsTitle: {
    color: '#94A3B8',
    fontSize: 11,
    fontWeight: 'bold',
    marginTop: 16,
    marginBottom: 8,
  },
  vendorConfidence: {
    color: '#60A5FA',
    fontSize: 10,
    marginTop: 4,
    fontWeight: '600',
  },
  approvalPanel: {
    backgroundColor: '#1E293B',
    borderTopWidth: 2,
    borderTopColor: '#D97706',
    padding: 16,
    marginHorizontal: 16,
    marginBottom: 16,
    borderRadius: 8,
  },
  loadingApprovalPanel: {
    backgroundColor: '#1E293B',
    borderTopWidth: 2,
    borderTopColor: '#2563EB',
    padding: 16,
    marginHorizontal: 16,
    marginBottom: 16,
    borderRadius: 8,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
  },
  loadingApprovalText: {
    color: '#60A5FA',
    fontSize: 13,
    marginLeft: 8,
    fontWeight: '600',
  },
  waitingApprovalContainer: {
    backgroundColor: 'rgba(245, 158, 11, 0.1)',
    borderColor: '#F59E0B',
    borderWidth: 1,
    borderRadius: 6,
    padding: 10,
    marginBottom: 12,
    alignItems: 'center',
  },
  waitingApprovalTitle: {
    color: '#F59E0B',
    fontWeight: 'bold',
    fontSize: 12,
  },
  waitingApprovalSubtitle: {
    color: '#FBBF24',
    fontSize: 11,
    marginTop: 2,
    textAlign: 'center',
  },
  timeoutText: {
    color: '#F59E0B',
    fontSize: 12,
    fontWeight: 'bold',
    marginBottom: 12,
    textAlign: 'center',
  },
  loaderRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  regeneratingText: {
    color: '#60A5FA',
    fontSize: 13,
    marginLeft: 8,
  },
  feedbackInput: {
    backgroundColor: '#0F172A',
    color: '#F8FAFC',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 13,
    minHeight: 50,
    marginBottom: 12,
    borderColor: '#334155',
    borderWidth: 1,
  },
  reconnectingText: {
    color: '#F59E0B',
    fontSize: 11,
    textAlign: 'center',
    marginBottom: 8,
  },
  buttonDisconnected: {
    opacity: 0.4,
  },
  approvalActions: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 8,
  },
  approveButton: {
    flex: 1,
    backgroundColor: '#059669',
    paddingVertical: 12,
    borderRadius: 6,
    alignItems: 'center',
  },
  rejectButton: {
    flex: 1,
    backgroundColor: '#DC2626',
    paddingVertical: 12,
    borderRadius: 6,
    alignItems: 'center',
  },
  stopButton: {
    flex: 1,
    backgroundColor: '#475569',
    paddingVertical: 12,
    borderRadius: 6,
    alignItems: 'center',
  },
  actionButtonText: {
    color: '#FFFFFF',
    fontWeight: 'bold',
    fontSize: 12,
  },
  metadataBox: {
    backgroundColor: 'rgba(59, 130, 246, 0.1)',
    borderColor: '#3B82F6',
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    marginBottom: 12,
  },
  metadataTitle: {
    color: '#60A5FA',
    fontWeight: 'bold',
    fontSize: 12,
    marginBottom: 8,
  },
  metadataText: {
    color: '#CBD5E1',
    fontSize: 11,
    lineHeight: 18,
    marginBottom: 4,
  },
  finalEmailCard: {
    backgroundColor: 'rgba(16, 185, 129, 0.12)',
    borderColor: '#10B981',
    borderWidth: 2,
    borderRadius: 10,
    padding: 16,
    marginHorizontal: 16,
    marginBottom: 16,
  },
  finalEmailHeader: {
    marginBottom: 12,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(16, 185, 129, 0.3)',
    paddingBottom: 8,
  },
  finalEmailTitle: {
    color: '#10B981',
    fontWeight: 'bold',
    fontSize: 14,
    marginBottom: 4,
  },
  finalEmailVendor: {
    color: '#6EE7B7',
    fontSize: 12,
    fontWeight: '600',
  },
  finalEmailContent: {
    color: '#E2E8F0',
    fontSize: 13,
    lineHeight: 20,
  },
  draftBox: {
    backgroundColor: 'rgba(34, 197, 94, 0.1)',
    borderColor: '#22C55E',
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    marginBottom: 12,
  },
  draftTitle: {
    color: '#4ADE80',
    fontWeight: 'bold',
    fontSize: 12,
    marginBottom: 8,
  },
  draftVendor: {
    color: '#86EFAC',
    fontSize: 11,
    fontWeight: '600',
    marginBottom: 8,
  },
  draftContent: {
    color: '#CBD5E1',
    fontSize: 12,
    lineHeight: 18,
  },
  footer: {
    backgroundColor: '#0F172A',
    borderTopWidth: 1,
    borderTopColor: '#1E293B',
    padding: 16,
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
  startButtonEnabled: {
    backgroundColor: '#10B981',
    shadowColor: '#10B981',
    shadowOpacity: 0.6,
    shadowRadius: 8,
  },
  startButtonDisabled: {
    backgroundColor: '#64748B',
    opacity: 0.5,
  },
  runningContainer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  runningText: {
    color: '#94A3B8',
    fontSize: 13,
    marginLeft: 8,
  },
  stopRunButton: {
    backgroundColor: '#EF4444',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 6,
  },
  stopRunButtonText: {
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
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(15, 23, 42, 0.8)',
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: '#1E293B',
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    maxHeight: '80%',
    padding: 16,
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingBottom: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#334155',
  },
  modalTitle: {
    color: '#F8FAFC',
    fontSize: 16,
    fontWeight: 'bold',
  },
  closeModalButtonText: {
    color: '#94A3B8',
    fontSize: 18,
    fontWeight: 'bold',
  },
  historyListContent: {
    paddingVertical: 12,
  },
  emptyHistoryContainer: {
    paddingVertical: 48,
    alignItems: 'center',
  },
  emptyHistoryText: {
    color: '#94A3B8',
    fontSize: 13,
  },
  historyCard: {
    backgroundColor: '#0F172A',
    borderColor: '#334155',
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    marginBottom: 10,
  },
  historyCardCurrent: {
    borderColor: '#60A5FA',
  },
  historyCardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
  },
  historyTime: {
    color: '#64748B',
    fontSize: 10,
    flex: 1,
  },
  statusBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  statusBadgeSuccess: {
    backgroundColor: 'rgba(5, 150, 105, 0.2)',
  },
  statusBadgeFail: {
    backgroundColor: 'rgba(239, 68, 68, 0.2)',
  },
  statusBadgeText: {
    color: '#CBD5E1',
    fontSize: 8,
    fontWeight: 'bold',
  },
  historyPrompt: {
    color: '#E2E8F0',
    fontSize: 12,
    fontWeight: '600',
  },
});
