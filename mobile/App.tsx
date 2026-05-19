import React, { useState, useRef, useEffect } from 'react';
import { StatusBar } from 'expo-status-bar';
import {
  StyleSheet,
  Text,
  View,
  TextInput,
  TouchableOpacity,
  FlatList,
  SafeAreaView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Dimensions,
} from 'react-native';
import { useAgentStore, Message } from './store/useAgentStore';

const { width } = Dimensions.get('window');

// Steps sequence in the orchestration workflow
const STEPS = [
  { id: 'SEARCHING_VENDORS', label: 'Searching Vendors' },
  { id: 'ANALYZING_PRICING', label: 'Analyzing Pricing' },
  { id: 'DRAFTING_OUTREACH', label: 'Drafting Outreach' },
  { id: 'SELF_REFLECTION', label: 'Self Reflection' },
  { id: 'WAITING_APPROVAL', label: 'Approval Gate' },
];

export default function App() {
  const {
    hostUrl,
    setHostUrl,
    connectionStatus,
    agentState,
    messages,
    draftMessage,
    timeoutCountdown,
    startAgent,
    sendApprove,
    sendStop,
    resetChat,
  } = useAgentStore();

  const [promptInput, setPromptInput] = useState('Find reliable procurement vendors for custom server hardware.');
  const [isEditingHost, setIsEditingHost] = useState(false);
  const [tempHost, setTempHost] = useState(hostUrl);

  const flatListRef = useRef<FlatList>(null);

  // Automatically scroll to end of chat when messages array updates
  useEffect(() => {
    if (flatListRef.current && messages.length > 0) {
      setTimeout(() => {
        flatListRef.current?.scrollToEnd({ animated: true });
      }, 100);
    }
  }, [messages]);

  const handleStart = () => {
    if (!promptInput.trim()) return;
    startAgent(promptInput.trim());
  };

  const handleSaveHost = () => {
    setHostUrl(tempHost.trim());
    setIsEditingHost(false);
  };

  // Helper to determine status color
  const getStatusColor = () => {
    switch (connectionStatus) {
      case 'connected':
        return '#10B981'; // Emerald
      case 'connecting':
        return '#F59E0B'; // Amber
      case 'error':
        return '#EF4444'; // Red
      default:
        return '#6B7280'; // Slate
    }
  };

  // Helper to check if a step is completed or active
  const getStepStyle = (stepId: string) => {
    const activeIndex = STEPS.findIndex((s) => s.id === agentState);
    const stepIndex = STEPS.findIndex((s) => s.id === stepId);

    if (agentState === 'SUCCESS') {
      return { container: styles.stepCompleted, text: styles.stepTextCompleted };
    }
    if (agentState === 'CANCELLED' || agentState === 'FAILED') {
      return { container: styles.stepInactive, text: styles.stepTextInactive };
    }

    if (stepId === agentState) {
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
          <Text style={styles.systemMessageText}>{item.text}</Text>
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
          {!isUser && item.step && (
            <Text style={styles.messageStepLabel}>{item.step.replace('_', ' ')}</Text>
          )}
          <Text style={styles.messageText}>{item.text}</Text>
          <Text style={styles.messageTime}>
            {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </Text>
        </View>
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar style="light" />
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={styles.container}
      >
        {/* Header Section */}
        <View style={styles.header}>
          <View>
            <Text style={styles.headerTitle}>Trybo Agentic Bridge</Text>
            <View style={styles.statusContainer}>
              <View style={[styles.statusDot, { backgroundColor: getStatusColor() }]} />
              <Text style={styles.statusText}>
                {connectionStatus.toUpperCase()} {agentState !== 'IDLE' ? `(${agentState})` : ''}
              </Text>
            </View>
          </View>

          {/* Editable Host URL */}
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

        {/* Stepper Workflow Visualizer */}
        {agentState !== 'IDLE' && (
          <View style={styles.stepperContainer}>
            {STEPS.map((step, index) => {
              const stylesStep = getStepStyle(step.id);
              return (
                <React.Fragment key={step.id}>
                  <View style={[styles.stepItem, stylesStep.container]}>
                    <Text style={[styles.stepLabel, stylesStep.text]}>{step.label}</Text>
                  </View>
                  {index < STEPS.length - 1 && (
                    <View style={styles.stepConnector} />
                  )}
                </React.Fragment>
              );
            })}
          </View>
        )}

        {/* Chat / Content Area */}
        <FlatList
          ref={flatListRef}
          data={messages}
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

        {/* WAITING_APPROVAL Gate Overlay Panel */}
        {agentState === 'WAITING_APPROVAL' && draftMessage && (
          <View style={styles.approvalPanel}>
            <View style={styles.approvalHeader}>
              <Text style={styles.approvalTitle}>Awaiting Outreach Approval</Text>
              {timeoutCountdown !== null && (
                <Text style={styles.timeoutCountdownText}>
                  Cancelling automatically in {timeoutCountdown}s
                </Text>
              )}
            </View>
            <View style={styles.draftCard}>
              <Text style={styles.draftLabel}>GENERATED OUTREACH DRAFT</Text>
              <Text style={styles.draftText}>{draftMessage}</Text>
            </View>
            <View style={styles.approvalActions}>
              <TouchableOpacity style={styles.approveButton} onPress={sendApprove}>
                <Text style={styles.actionButtonText}>Approve & Finalize</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.rejectButton} onPress={sendStop}>
                <Text style={styles.actionButtonText}>Stop Run</Text>
              </TouchableOpacity>
            </View>
          </View>
        )}

        {/* Control Footer */}
        <View style={styles.footer}>
          {connectionStatus === 'disconnected' ? (
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
            agentState !== 'WAITING_APPROVAL' && (
              <View style={styles.runningContainer}>
                <View style={styles.loaderRow}>
                  <ActivityIndicator size="small" color="#60A5FA" />
                  <Text style={styles.runningText}>Agent executing workflow steps...</Text>
                </View>
                <TouchableOpacity style={styles.stopButton} onPress={sendStop}>
                  <Text style={styles.stopButtonText}>Stop Agent</Text>
                </TouchableOpacity>
              </View>
            )
          )}

          {/* Reset / Reset State Button */}
          {messages.length > 0 && connectionStatus === 'disconnected' && (
            <TouchableOpacity style={styles.resetButton} onPress={resetChat}>
              <Text style={styles.resetButtonText}>Clear Session</Text>
            </TouchableOpacity>
          )}
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: '#0F172A', // Slate 900
  },
  container: {
    flex: 1,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#1E293B', // Slate 800
    backgroundColor: '#0F172A',
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#F8FAFC', // Slate 50
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
    color: '#94A3B8', // Slate 400
    fontWeight: '600',
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
    color: '#60A5FA', // Blue 400
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
    color: '#E2E8F0', // Slate 200
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
    backgroundColor: '#2563EB', // Blue 600
  },
  stepCompleted: {
    backgroundColor: '#059669', // Emerald 600
  },
  stepInactive: {
    backgroundColor: '#334155', // Slate 700
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
    backgroundColor: '#1D4ED8', // Indigo/Blue 700
    borderBottomRightRadius: 2,
  },
  messageAgentBubble: {
    backgroundColor: '#1E293B', // Slate 800
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
    marginRight: 8,
    alignItems: 'center',
  },
  rejectButton: {
    flex: 1,
    backgroundColor: '#EF4444',
    paddingVertical: 12,
    borderRadius: 8,
    marginLeft: 8,
    alignItems: 'center',
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
});
