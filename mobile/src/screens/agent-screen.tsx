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
import { connectAgentWS, disconnectAgentWS } from '../services/websocket-service';
import { Message, AgentStep, VendorResult, TaskHistoryItem } from '../types/websocket';

const DEFAULT_STEPS = [
  { id: 'SEARCHING_VENDORS', label: 'Vendor Research' },
  { id: 'ANALYZING_PRICING', label: 'Pricing Analysis' },
  { id: 'DRAFTING_OUTREACH', label: 'Outreach Draft' },
  { id: 'SELF_REFLECTION', label: 'Self Reflection' },
  { id: 'EXECUTING', label: 'Execution' },
];

const STEP_LABELS: Record<string, string> = {
  SEARCHING_VENDORS: 'Vendor Research',
  ANALYZING_PRICING: 'Pricing Analysis',
  DRAFTING_OUTREACH: 'Outreach Draft',
  SELF_REFLECTION: 'Self Reflection',
  EXECUTING: 'Execution',
};

type TabType = 'logs' | 'vendors' | 'audit';

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
    backendSteps,
    fetchMetadataEnums,
    taskId,
    correlationId,
    cancellationReason,
    currentPendingStep,
    currentStepData,
    
    // Upgraded states
    taskHistory,
    vendorResults,
    selectedVendor,
    pricingAnalysis,
    reflectionMetadata,
    confidenceScore,
  } = useAgentStore();

  const [promptInput, setPromptInput] = useState('Find reliable procurement vendors for custom server hardware.');
  const [isEditingHost, setIsEditingHost] = useState(false);
  const [tempHost, setTempHost] = useState(hostUrl);
  const [isHistoryVisible, setIsHistoryVisible] = useState(false);
  const [activeTab, setActiveTab] = useState<TabType>('logs');
  
  const [selectedCards, setSelectedCards] = useState<VendorResult[]>([]);

  const handleToggleCard = (vendor: VendorResult) => {
    // Read-only in simplified HITL flow
  };

  const flatListRef = useRef<FlatList>(null);
  const scrollViewRef = useRef<ScrollView>(null);

  // Fetch dynamic metadata enums from the backend on mount
  useEffect(() => {
    fetchMetadataEnums();
  }, []);

  // Sync selected cards with active HITL step state
  useEffect(() => {
    setSelectedCards(selectedVendor ? [selectedVendor] : []);
  }, [selectedVendor]);

  // Auto-switch tabs based on workflow progression to wow the user
  useEffect(() => {
    if (currentAgentStep === 'SEARCHING_VENDORS' || currentAgentStep === 'ANALYZING_PRICING') {
      setActiveTab('vendors');
    } else if (currentAgentStep === 'SELF_REFLECTION') {
      setActiveTab('audit');
    } else if (taskState && taskState.startsWith('WAITING_')) {
      setActiveTab('audit');
    } else if (taskState === 'COMPLETED' || currentAgentStep === 'EXECUTING') {
      setActiveTab('logs');
    }
  }, [currentAgentStep, taskState]);

  useEffect(() => {
    if (flatListRef.current && agentMessages.length > 0) {
      setTimeout(() => {
        flatListRef.current?.scrollToEnd({ animated: true });
      }, 100);
    }
  }, [agentMessages]);

  // Auto-scroll to bottom when approval panel appears
  useEffect(() => {
    if (isAwaitingApproval && scrollViewRef.current) {
      setTimeout(() => {
        scrollViewRef.current?.scrollToEnd({ animated: true });
      }, 200);
    }
  }, [isAwaitingApproval, currentStepData]);

  const handleStart = () => {
    if (!promptInput.trim()) return;
    connectAgentWS(promptInput.trim());
  };

  const handleApprove = () => {
    sendApprovalResponse('APPROVE');
  };

  const handleReject = () => {
    const feedback = rejectionFeedback.trim();
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
    if (taskState === 'FAILED_RETRYING') {
      return '#F59E0B';
    }
    if (taskState === 'EXTERNAL_SEARCHING') {
      return '#60A5FA';
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
    const activeIndex = stepsToRender.findIndex((s) => s.id === currentAgentStep);
    const stepIndex = stepsToRender.findIndex((s) => s.id === stepId);

    if (taskState === 'COMPLETED') {
      return { container: styles.stepCompleted, text: styles.stepTextCompleted };
    }
    if (taskState === 'CANCELLED' || taskState === 'FAILED') {
      return { container: styles.stepInactive, text: styles.stepTextInactive };
    }
    // If waiting for approval, highlight the pending step
    if (taskState && (taskState === 'WAITING_FINAL_APPROVAL' || taskState.startsWith('WAITING_'))) {
      const pendingStep = currentPendingStep || (
        taskState === 'WAITING_FINAL_APPROVAL' ? 'SELF_REFLECTION' : null
      );
      if (stepId === pendingStep) {
        return { container: styles.stepPending, text: styles.stepTextActive };
      }
      const pendingIndex = stepsToRender.findIndex((s) => s.id === pendingStep);
      if (stepIndex < pendingIndex && pendingIndex !== -1) {
        return { container: styles.stepCompleted, text: styles.stepTextCompleted };
      }
      return { container: styles.stepInactive, text: styles.stepTextInactive };
    }
    if (currentAgentStep === 'EXECUTING') {
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

  const renderVendorCatalog = (vendor: VendorResult) => {
    const catalog = vendor.catalog || vendor.items || vendor.catalog_items;
    if (!catalog) return null;
    
    if (typeof catalog === 'object' && !Array.isArray(catalog)) {
      return Object.entries(catalog).map(([item, price]) => (
        <View key={item} style={styles.catalogItemRow}>
          <Text style={styles.catalogItemText}>• {item}</Text>
          <Text style={styles.catalogPriceText}>
            {typeof price === 'number' ? `₹${(price as number).toLocaleString()}` : String(price)}
          </Text>
        </View>
      ));
    }
    
    if (Array.isArray(catalog)) {
      return catalog.map((item: any, idx: number) => (
        <View key={idx} style={styles.catalogItemRow}>
          <Text style={styles.catalogItemText}>
            • {item.name || item.item || JSON.stringify(item)}
          </Text>
          <Text style={styles.catalogPriceText}>
            {item.price ? `₹${item.price.toLocaleString()}` : ''}
          </Text>
        </View>
      ));
    }
    
    return null;
  };

  const getSourceBadgeDetails = (source?: string) => {
    if (source?.toLowerCase() === 'internal') {
      return { bg: 'rgba(5, 150, 105, 0.2)', border: '#059669', text: '#34D399', label: 'RAG DB' };
    }
    return { bg: 'rgba(37, 99, 235, 0.2)', border: '#2563EB', text: '#60A5FA', label: 'Web Search' };
  };

  const renderVendorCard = (vendor: VendorResult, isSelected = false) => {
    const sourceDetails = getSourceBadgeDetails(vendor.source_type);
    const isInteractive = false;
    return (
      <TouchableOpacity
        key={vendor.vendor_name || vendor.name}
        onPress={() => handleToggleCard(vendor)}
        disabled={!isInteractive}
        activeOpacity={isInteractive ? 0.7 : 1}
        style={[styles.vendorCard, isSelected && styles.vendorCardSelected]}
      >
        <View style={styles.vendorCardHeader}>
          <View style={{ flex: 1 }}>
            <Text style={styles.vendorName}>{vendor.vendor_name || vendor.name}</Text>
            {vendor.category && (
              <Text style={styles.vendorCategory}>{vendor.category.toUpperCase()}</Text>
            )}
          </View>
          <View style={[styles.sourceBadge, { backgroundColor: sourceDetails.bg, borderColor: sourceDetails.border }]}>
            <Text style={[styles.sourceBadgeText, { color: sourceDetails.text }]}>{sourceDetails.label}</Text>
          </View>
        </View>
        
        <View style={styles.vendorMetaRow}>
          <Text style={styles.vendorMetaText}>⭐ {vendor.rating || 'N/A'}</Text>
          <Text style={styles.vendorMetaText}>📍 {vendor.location || 'N/A'}</Text>
          <Text style={styles.vendorMetaText}>🚚 {vendor.delivery_days ? `${vendor.delivery_days} days` : 'N/A'}</Text>
        </View>

        {vendor.confidence_score !== undefined && (
          <View style={styles.confidenceScoreRow}>
            <Text style={styles.confidenceLabel}>RAG Confidence Score:</Text>
            <Text style={styles.confidenceValue}>{(vendor.confidence_score * 100).toFixed(0)}%</Text>
          </View>
        )}

        <View style={styles.catalogDivider} />
        <Text style={styles.catalogHeader}>Catalog Items</Text>
        {renderVendorCatalog(vendor)}
      </TouchableOpacity>
    );
  };

  const renderTaskHistoryItem = (item: TaskHistoryItem) => {
    const isCurrent = item.task_id === taskId;
    return (
      <TouchableOpacity
        key={item.task_id}
        style={[styles.historyCard, isCurrent && styles.historyCardCurrent]}
        onPress={() => {
          setIsHistoryVisible(false);
          // Set prompt in store if they want to re-run, or just view it
          setPromptInput(item.prompt);
        }}
      >
        <View style={styles.historyCardHeader}>
          <Text style={styles.historyTime} numberOfLines={1}>
            {new Date(item.timestamp).toLocaleDateString()} {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </Text>
          <View style={[styles.statusBadge, item.status === 'COMPLETED' ? styles.statusBadgeSuccess : styles.statusBadgeFail]}>
            <Text style={styles.statusBadgeText}>{item.status}</Text>
          </View>
        </View>
        <Text style={styles.historyPrompt} numberOfLines={2}>"{item.prompt}"</Text>
        {item.selected_vendor && (
          <Text style={styles.historyVendor}>🏢 Selected: {item.selected_vendor.vendor_name || item.selected_vendor.name}</Text>
        )}
      </TouchableOpacity>
    );
  };

  const isRunning = connectionStatus !== 'disconnected' || taskState === 'SCHEDULED';

  const getStepHeaderLabel = () => {
    if (!currentPendingStep) return 'Awaiting Approval';
    return STEP_LABELS[currentPendingStep] || formatStepLabel(currentPendingStep);
  };

  const getStepHeaderIcon = () => {
    if (!currentPendingStep) return '📋';
    switch (currentPendingStep) {
      case 'SEARCHING_VENDORS': return '🔍';
      case 'ANALYZING_PRICING': return '📊';
      case 'DRAFTING_OUTREACH': return '✉️';
      case 'SELF_REFLECTION': return '🛡️';
      case 'EXECUTING': return '🚀';
      default: return '📋';
    }
  };

  return (
    <View style={styles.screen}>
      {/* Header */}
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={styles.headerTitle}>Trybo Agentic Bridge</Text>
          <View style={styles.statusContainer}>
            <View style={[styles.statusDot, { backgroundColor: getStatusColor() }]} />
            <Text style={styles.statusText}>
              {taskState === 'FAILED_RETRYING' ? 'RETRYING' : taskState === 'EXTERNAL_SEARCHING' ? 'WEB SEARCHING' : connectionStatus.toUpperCase()} {taskState !== 'IDLE' ? `(${taskState})` : ''}
            </Text>
          </View>
          {/* Show task and correlation IDs when available */}
          {taskId && (
            <View style={styles.idBadgeCol}>
              <Text style={styles.idBadge}>Task: {taskId.slice(0, 18)}…</Text>
              {correlationId && (
                <Text style={styles.idBadge}>Corr: {correlationId.slice(0, 18)}…</Text>
              )}
            </View>
          )}
        </View>

        <View style={styles.headerRightActions}>
          <TouchableOpacity style={styles.historyButton} onPress={() => setIsHistoryVisible(true)}>
            <Text style={styles.historyButtonText}>📜 Runs ({taskHistory.length})</Text>
          </TouchableOpacity>

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

      {/* Dashboard Tab Selector */}
      {taskState !== 'IDLE' && (
        <View style={styles.tabBar}>
          <TouchableOpacity
            style={[styles.tabButton, activeTab === 'logs' && styles.tabButtonActive]}
            onPress={() => setActiveTab('logs')}
          >
            <Text style={[styles.tabButtonText, activeTab === 'logs' && styles.tabButtonTextActive]}>
              💬 Process Logs
            </Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.tabButton, activeTab === 'vendors' && styles.tabButtonActive]}
            onPress={() => setActiveTab('vendors')}
          >
            <Text style={[styles.tabButtonText, activeTab === 'vendors' && styles.tabButtonTextActive]}>
              🏢 Vendors ({vendorResults.length})
            </Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.tabButton, activeTab === 'audit' && styles.tabButtonActive]}
            onPress={() => setActiveTab('audit')}
          >
            <Text style={[styles.tabButtonText, activeTab === 'audit' && styles.tabButtonTextActive]}>
              🛡️ Audit & Draft
            </Text>
          </TouchableOpacity>
        </View>
      )}

      {/* Scrollable content area */}
      <ScrollView
        ref={scrollViewRef}
        style={styles.scrollArea}
        contentContainerStyle={styles.scrollContent}
        keyboardShouldPersistTaps="handled"
      >
        {/* Tab 1: Execution Chat / Process Logs */}
        {activeTab === 'logs' && (
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
                  Initialize a task and see the human-in-the-loop agent run live.
                </Text>
              </View>
            }
          />
        )}

        {/* Tab 2: Vendor Intelligence & Pricing Analysis */}
        {activeTab === 'vendors' && (
          <View style={styles.dashboardContainer}>
            {pricingAnalysis && (
              <View style={styles.pricingAnalysisContainer}>
                <Text style={styles.dashboardSectionHeader}>📊 Pricing & Sourcing Evaluation</Text>
                <View style={styles.pricingCard}>
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
                    {selectedCards.length > 0 ? (
                      <View style={styles.recommendationBadge}>
                        <Text style={styles.recommendationBadgeText}>
                          ⭐️ Selected: {selectedCards.map(c => c.vendor_name || c.name).join(', ')}
                        </Text>
                      </View>
                    ) : selectedVendor ? (
                      <View style={styles.recommendationBadge}>
                        <Text style={styles.recommendationBadgeText}>
                          ⭐️ Recommended Vendor: {selectedVendor.vendor_name || selectedVendor.name}
                        </Text>
                      </View>
                    ) : null}
                    {pricingAnalysis.confidence !== undefined && (
                      <View style={styles.confidenceScoreBadge}>
                        <Text style={styles.confidenceScoreBadgeText}>
                          Confidence: {(pricingAnalysis.confidence * 100).toFixed(0)}%
                        </Text>
                      </View>
                    )}
                  </View>
                  <Text style={styles.analysisSummaryText}>{pricingAnalysis.summary}</Text>
                  
                  {pricingAnalysis.reasoning && pricingAnalysis.reasoning.length > 0 && (
                    <View style={styles.reasoningContainer}>
                      <Text style={styles.reasoningHeader}>Selection Justifications:</Text>
                      {pricingAnalysis.reasoning.map((reason, idx) => (
                        <Text key={idx} style={styles.reasoningBullet}>• {reason}</Text>
                      ))}
                    </View>
                  )}
                </View>
              </View>
            )}

            <Text style={styles.dashboardSectionHeader}>🔍 Retrieved Suppliers</Text>
            {vendorResults.length === 0 ? (
              <View style={styles.noVendorsContainer}>
                <Text style={styles.noVendorsText}>Searching local database embedding indexes...</Text>
                <ActivityIndicator size="small" color="#60A5FA" style={{ marginTop: 8 }} />
              </View>
            ) : (
              <View style={styles.vendorsList}>
                {vendorResults.map((v) => {
                  const isSelected = selectedCards.length > 0
                    ? selectedCards.some((sc) => (sc.vendor_name || sc.name) === (v.vendor_name || v.name))
                    : (selectedVendor && (selectedVendor.vendor_name === v.vendor_name || selectedVendor.name === v.name));
                  return renderVendorCard(v, !!isSelected);
                })}
              </View>
            )}
          </View>
        )}

        {/* Tab 3: Reflection Metadata & Audit Checks */}
        {activeTab === 'audit' && (
          <View style={styles.dashboardContainer}>
            {reflectionMetadata ? (
              <View style={styles.auditContainer}>
                <Text style={styles.dashboardSectionHeader}>🛡️ Self-Reflection Quality Audit</Text>
                <View style={styles.auditCard}>
                  <View style={styles.auditCardHeader}>
                    <Text style={styles.auditCardTitle}>LLM Verification Checklist</Text>
                    {confidenceScore !== null && (
                      <View style={styles.confidenceScoreBadge}>
                        <Text style={styles.confidenceScoreBadgeText}>
                          Confidence: {(confidenceScore * 100).toFixed(0)}%
                        </Text>
                      </View>
                    )}
                  </View>

                  <View style={styles.auditRow}>
                    <Text style={styles.auditLabel}>Tone Check (Professional/Formal):</Text>
                    <View style={[styles.auditBadge, reflectionMetadata.tone_check_passed ? styles.auditBadgePass : styles.auditBadgeFail]}>
                      <Text style={reflectionMetadata.tone_check_passed ? styles.auditBadgeTextPass : styles.auditBadgeTextFail}>
                        {reflectionMetadata.tone_check_passed ? '✓ PASSED' : '✗ FAILED'}
                      </Text>
                    </View>
                  </View>

                  <View style={styles.auditRow}>
                    <Text style={styles.auditLabel}>Hallucination/Accuracy Audit:</Text>
                    <View style={[styles.auditBadge, reflectionMetadata.hallucination_free ? styles.auditBadgePass : styles.auditBadgeFail]}>
                      <Text style={reflectionMetadata.hallucination_free ? styles.auditBadgeTextPass : styles.auditBadgeTextFail}>
                        {reflectionMetadata.hallucination_free ? '✓ PASSED' : '✗ FAILED'}
                      </Text>
                    </View>
                  </View>

                  <View style={styles.auditRow}>
                    <Text style={styles.auditLabel}>Layout Formatting Check:</Text>
                    <View style={[styles.auditBadge, reflectionMetadata.formatting_valid ? styles.auditBadgePass : styles.auditBadgeFail]}>
                      <Text style={reflectionMetadata.formatting_valid ? styles.auditBadgeTextPass : styles.auditBadgeTextFail}>
                        {reflectionMetadata.formatting_valid ? '✓ PASSED' : '✗ FAILED'}
                      </Text>
                    </View>
                  </View>

                  {reflectionMetadata.critique && (
                    <View style={styles.critiqueContainer}>
                      <Text style={styles.critiqueHeader}>Critic Critique Feedback:</Text>
                      <Text style={styles.critiqueText}>{reflectionMetadata.critique}</Text>
                    </View>
                  )}
                </View>
              </View>
            ) : (
              <View style={styles.auditContainer}>
                <Text style={styles.dashboardSectionHeader}>🛡️ Self-Reflection Audit</Text>
                <View style={styles.noAuditCard}>
                  <Text style={styles.noAuditText}>Waiting for final outreach draft and critique checks...</Text>
                </View>
              </View>
            )}

            {/* Render Outreach Draft */}
            {(draftMessage || currentStepData) && (
              <View style={{ marginTop: 16 }}>
                <Text style={styles.dashboardSectionHeader}>✉️ Generated Outreach Message</Text>
                <View style={styles.draftDisplayCard}>
                  <Text style={styles.draftDisplayText}>{currentStepData || draftMessage}</Text>
                </View>
              </View>
            )}
          </View>
        )}

        {/* Awaiting Approval panel */}
        {isAwaitingApproval && (
          <View style={styles.approvalPanel}>
            <View style={styles.waitingApprovalContainer}>
              <Text style={styles.waitingApprovalTitle}>
                {getStepHeaderIcon()} WAITING FOR APPROVAL — {getStepHeaderLabel().toUpperCase()}
              </Text>
              <Text style={styles.waitingApprovalSubtitle}>
                Review audit scores and generated draft in the Audit & Draft tab, then approve:
              </Text>
            </View>
            
            <View style={styles.approvalHeader}>
              <Text style={styles.approvalTitle}>Human Authorization Required</Text>
            </View>

            {isRegenerating ? (
              <View style={styles.loaderRow}>
                <ActivityIndicator size="small" color="#60A5FA" />
                <Text style={styles.runningText}>
                  Re-running orchestration step based on feedback...
                </Text>
              </View>
            ) : (
              <>
                <TextInput
                  style={styles.feedbackInput}
                  value={rejectionFeedback}
                  onChangeText={setRejectionFeedback}
                  placeholder="Optional rejection feedback..."
                  placeholderTextColor="#9CA3AF"
                  multiline
                />
                <View style={styles.approvalActions}>
                  <TouchableOpacity style={styles.approveButton} onPress={handleApprove}>
                    <Text style={styles.actionButtonText}>✅ Approve</Text>
                  </TouchableOpacity>
                  <TouchableOpacity style={styles.rejectButton} onPress={handleReject}>
                    <Text style={styles.actionButtonText}>❌ Reject</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={styles.stopApprovalButton}
                    onPress={sendStop}
                    disabled={taskState === 'CANCELLED'}
                  >
                    <Text style={styles.actionButtonText}>⏹ Stop</Text>
                  </TouchableOpacity>
                </View>
              </>
            )}
          </View>
        )}
      </ScrollView>

      {/* Footer controls */}
      <View style={styles.footer}>
        {taskState === 'FAILED' && error && (
          <View style={styles.errorBanner}>
            <Text style={styles.errorText}>⚠️ AI Execution Failed</Text>
            <Text style={styles.errorSubText}>{error}</Text>
            <Text style={styles.retryHintText}>Please verify configuration and try starting a new run.</Text>
          </View>
        )}

        {taskState === 'FAILED_RETRYING' && (
          <View style={styles.retryingBanner}>
            <ActivityIndicator size="small" color="#F59E0B" />
            <Text style={styles.retryingText}>
              Connection issue encountered. Retrying execution with backoff...
            </Text>
          </View>
        )}

        {taskState === 'EXTERNAL_SEARCHING' && (
          <View style={styles.webSearchBanner}>
            <ActivityIndicator size="small" color="#60A5FA" />
            <Text style={styles.webSearchText}>
              RAG confidence score low. Launching Tavily external web search...
            </Text>
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
          !isAwaitingApproval && taskState !== 'COMPLETED' && taskState !== 'CANCELLED' && taskState !== 'FAILED' && (
            <View style={styles.runningContainer}>
              <View style={styles.loaderRow}>
                <ActivityIndicator size="small" color="#60A5FA" />
                <Text style={styles.runningText}>
                  {currentAgentStep === 'EXECUTING' ? 'Executing approved outreach...' : 'Agent processing steps...'}
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

      {/* Task History Modal */}
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
              <TouchableOpacity style={styles.closeModalButton} onPress={() => setIsHistoryVisible(false)}>
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
                  <Text style={styles.emptyHistoryText}>No prior runs in current session.</Text>
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
  idBadgeCol: {
    flexDirection: 'column',
    marginTop: 2,
    alignItems: 'flex-start',
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
  tabBar: {
    flexDirection: 'row',
    backgroundColor: '#0F172A',
    borderBottomWidth: 1,
    borderBottomColor: '#1E293B',
  },
  tabButton: {
    flex: 1,
    alignItems: 'center',
    paddingVertical: 12,
    borderBottomWidth: 2,
    borderBottomColor: 'transparent',
  },
  tabButtonActive: {
    borderBottomColor: '#60A5FA',
  },
  tabButtonText: {
    color: '#94A3B8',
    fontSize: 12,
    fontWeight: 'bold',
  },
  tabButtonTextActive: {
    color: '#60A5FA',
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
  
  // Dashboard UI Styles
  dashboardContainer: {
    padding: 16,
  },
  dashboardSectionHeader: {
    color: '#F8FAFC',
    fontSize: 14,
    fontWeight: 'bold',
    marginBottom: 12,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  noVendorsContainer: {
    backgroundColor: '#1E293B',
    borderWidth: 1,
    borderColor: '#334155',
    borderRadius: 8,
    padding: 24,
    alignItems: 'center',
    justifyContent: 'center',
  },
  noVendorsText: {
    color: '#94A3B8',
    fontSize: 13,
  },
  vendorsList: {
    flexDirection: 'column',
  },
  vendorCard: {
    backgroundColor: '#1E293B',
    borderWidth: 1,
    borderColor: '#334155',
    borderRadius: 8,
    padding: 16,
    marginBottom: 12,
  },
  vendorCardSelected: {
    borderColor: '#059669',
    borderWidth: 2,
    backgroundColor: '#111E2E',
  },
  vendorCardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 8,
  },
  vendorName: {
    color: '#F8FAFC',
    fontSize: 15,
    fontWeight: 'bold',
  },
  vendorCategory: {
    color: '#94A3B8',
    fontSize: 9,
    fontWeight: '700',
    marginTop: 2,
  },
  sourceBadge: {
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 4,
  },
  sourceBadgeText: {
    fontSize: 9,
    fontWeight: 'bold',
  },
  vendorMetaRow: {
    flexDirection: 'row',
    marginBottom: 10,
  },
  vendorMetaText: {
    color: '#CBD5E1',
    fontSize: 12,
    marginRight: 16,
  },
  confidenceScoreRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    backgroundColor: '#0F172A',
    padding: 8,
    borderRadius: 6,
    marginVertical: 4,
  },
  confidenceLabel: {
    color: '#94A3B8',
    fontSize: 11,
  },
  confidenceValue: {
    color: '#60A5FA',
    fontSize: 11,
    fontWeight: 'bold',
  },
  catalogDivider: {
    height: 1,
    backgroundColor: '#334155',
    marginVertical: 10,
  },
  catalogHeader: {
    color: '#F8FAFC',
    fontSize: 12,
    fontWeight: 'bold',
    marginBottom: 6,
  },
  catalogItemRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 3,
  },
  catalogItemText: {
    color: '#CBD5E1',
    fontSize: 12,
    flex: 1,
  },
  catalogPriceText: {
    color: '#F8FAFC',
    fontSize: 12,
    fontWeight: '600',
  },

  // Pricing analysis component styles
  pricingAnalysisContainer: {
    marginBottom: 20,
  },
  pricingCard: {
    backgroundColor: '#111E2E',
    borderWidth: 1,
    borderColor: '#2563EB',
    borderRadius: 8,
    padding: 16,
  },
  recommendationBadge: {
    backgroundColor: 'rgba(5, 150, 105, 0.15)',
    borderColor: '#059669',
    borderWidth: 1,
    borderRadius: 6,
    paddingVertical: 6,
    paddingHorizontal: 12,
    marginBottom: 12,
    alignSelf: 'flex-start',
  },
  recommendationBadgeText: {
    color: '#34D399',
    fontSize: 12,
    fontWeight: 'bold',
  },
  analysisSummaryText: {
    color: '#E2E8F0',
    fontSize: 13,
    lineHeight: 18,
  },
  reasoningContainer: {
    marginTop: 12,
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: 'rgba(37, 99, 235, 0.2)',
  },
  reasoningHeader: {
    color: '#93C5FD',
    fontSize: 12,
    fontWeight: 'bold',
    marginBottom: 6,
  },
  reasoningBullet: {
    color: '#CBD5E1',
    fontSize: 12,
    lineHeight: 16,
    marginBottom: 4,
  },

  // Self-reflection audit component styles
  auditContainer: {
    marginBottom: 8,
  },
  noAuditCard: {
    backgroundColor: '#1E293B',
    borderWidth: 1,
    borderColor: '#334155',
    borderRadius: 8,
    padding: 24,
    alignItems: 'center',
    justifyContent: 'center',
  },
  noAuditText: {
    color: '#94A3B8',
    fontSize: 13,
    textAlign: 'center',
  },
  auditCard: {
    backgroundColor: '#1E293B',
    borderColor: '#334155',
    borderWidth: 1,
    borderRadius: 8,
    padding: 16,
  },
  auditCardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 14,
  },
  auditCardTitle: {
    color: '#F8FAFC',
    fontSize: 14,
    fontWeight: 'bold',
  },
  confidenceScoreBadge: {
    backgroundColor: 'rgba(96, 165, 250, 0.15)',
    borderColor: '#60A5FA',
    borderWidth: 1,
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  confidenceScoreBadgeText: {
    color: '#60A5FA',
    fontSize: 11,
    fontWeight: 'bold',
  },
  auditRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#334155',
  },
  auditLabel: {
    color: '#CBD5E1',
    fontSize: 13,
  },
  auditBadge: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
    alignItems: 'center',
    minWidth: 70,
  },
  auditBadgePass: {
    backgroundColor: 'rgba(5, 150, 105, 0.15)',
  },
  auditBadgeFail: {
    backgroundColor: 'rgba(239, 68, 68, 0.15)',
  },
  auditBadgeTextPass: {
    color: '#34D399',
    fontSize: 10,
    fontWeight: 'bold',
  },
  auditBadgeTextFail: {
    color: '#F87171',
    fontSize: 10,
    fontWeight: 'bold',
  },
  critiqueContainer: {
    marginTop: 14,
    backgroundColor: '#0F172A',
    borderRadius: 6,
    padding: 12,
    borderLeftWidth: 3,
    borderLeftColor: '#F59E0B',
  },
  critiqueHeader: {
    color: '#F59E0B',
    fontSize: 12,
    fontWeight: 'bold',
    marginBottom: 4,
  },
  critiqueText: {
    color: '#E2E8F0',
    fontSize: 12,
    lineHeight: 16,
  },
  draftDisplayCard: {
    backgroundColor: '#1E293B',
    borderColor: '#334155',
    borderWidth: 1,
    borderRadius: 8,
    padding: 16,
  },
  draftDisplayText: {
    color: '#E2E8F0',
    fontSize: 13,
    lineHeight: 18,
    fontFamily: 'monospace',
  },

  // Modify draft flow container
  modifyFeedbackContainer: {
    backgroundColor: '#0F172A',
    borderColor: '#334155',
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    marginBottom: 12,
  },
  modifyTextarea: {
    color: '#F8FAFC',
    fontSize: 13,
    minHeight: 80,
    textAlignVertical: 'top',
    marginBottom: 12,
  },
  modifyActionsRow: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
  },
  submitModifyButton: {
    backgroundColor: '#2563EB',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 6,
    marginRight: 8,
  },
  cancelModifyButton: {
    backgroundColor: '#475569',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 6,
  },
  modifyBtnText: {
    color: '#FFFFFF',
    fontWeight: 'bold',
    fontSize: 12,
  },

  // Awaiting Approval UI
  approvalPanel: {
    backgroundColor: '#1E293B',
    borderTopWidth: 2,
    borderTopColor: '#D97706',
    padding: 16,
    marginHorizontal: 16,
    marginBottom: 16,
    borderRadius: 8,
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
  },
  approvalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  approvalTitle: {
    color: '#F8FAFC',
    fontSize: 15,
    fontWeight: 'bold',
  },
  timeoutCountdownText: {
    color: '#F59E0B',
    fontSize: 12,
    fontWeight: 'bold',
  },
  approvalActions: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  approveButton: {
    flex: 1.2,
    backgroundColor: '#059669',
    paddingVertical: 14,
    borderRadius: 8,
    marginRight: 4,
    alignItems: 'center',
  },
  modifyButton: {
    flex: 1,
    backgroundColor: '#D97706',
    paddingVertical: 14,
    borderRadius: 8,
    marginHorizontal: 4,
    alignItems: 'center',
  },
  rejectButton: {
    flex: 1,
    backgroundColor: '#DC2626',
    paddingVertical: 14,
    borderRadius: 8,
    marginHorizontal: 4,
    alignItems: 'center',
  },
  stopApprovalButton: {
    flex: 1,
    backgroundColor: '#475569',
    paddingVertical: 14,
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
    minHeight: 50,
    marginBottom: 12,
    borderColor: '#334155',
    borderWidth: 1,
  },
  actionButtonText: {
    color: '#FFFFFF',
    fontWeight: 'bold',
    fontSize: 12,
  },

  // Footer UI Elements
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
    lineHeight: 16,
  },
  retryHintText: {
    color: '#94A3B8',
    fontSize: 11,
    marginTop: 4,
    fontWeight: '600',
  },
  retryingBanner: {
    flexDirection: 'row',
    backgroundColor: 'rgba(245, 158, 11, 0.15)',
    borderColor: '#F59E0B',
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    marginBottom: 12,
    alignItems: 'center',
  },
  retryingText: {
    color: '#FBBF24',
    fontSize: 12,
    fontWeight: 'bold',
    marginLeft: 8,
  },
  webSearchBanner: {
    flexDirection: 'row',
    backgroundColor: 'rgba(96, 165, 250, 0.15)',
    borderColor: '#60A5FA',
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    marginBottom: 12,
    alignItems: 'center',
  },
  webSearchText: {
    color: '#60A5FA',
    fontSize: 12,
    fontWeight: 'bold',
    marginLeft: 8,
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

  // Modal UI Styles
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
  closeModalButton: {
    padding: 4,
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
    fontStyle: 'italic',
  },
  historyVendor: {
    color: '#60A5FA',
    fontSize: 10,
    marginTop: 6,
  },
});