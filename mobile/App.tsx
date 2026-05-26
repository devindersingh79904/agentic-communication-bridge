import React, { useEffect } from 'react';
import { AppState, AppStateStatus, SafeAreaView, StyleSheet } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { AgentScreen } from './src/screens/agent-screen';
import { disconnectAgentWS, connectAgentWS } from './src/services/websocket-service';
import { useAgentStore } from './src/store/agent-store';

export default function App() {
  // Handle App Lifecycle state cleanup to avoid WebSocket leaks
  useEffect(() => {
    const handleAppStateChange = (nextAppState: AppStateStatus) => {
      if (nextAppState === 'background' || nextAppState === 'inactive') {
        disconnectAgentWS();
      } else if (nextAppState === 'active') {
        // Automatically reconnect if we have a running task and no active connection
        const store = useAgentStore.getState();
        const activeStates = [
          'SCHEDULED',
          'RUNNING',
          'SEARCHING_VENDORS',
          'EXTERNAL_SEARCHING',
          'ANALYZING_PRICING',
          'DRAFTING_OUTREACH',
          'SELF_REFLECTION',
          'FAILED_RETRYING',
          'WAITING_VENDOR_SELECTION',
          'WAITING_PRICE_APPROVAL',
          'WAITING_FINAL_APPROVAL',
        ];
        
        if (
          store.taskId &&
          activeStates.includes(store.taskState) &&
          store.connectionStatus !== 'connected' &&
          store.connectionStatus !== 'connecting'
        ) {
          console.log('App active - automatically reconnecting/resuming task:', store.taskId);
          connectAgentWS(store.currentPrompt || '');
        }
      }
    };

    const subscription = AppState.addEventListener('change', handleAppStateChange);

    return () => {
      subscription.remove();
      disconnectAgentWS(); // Cleanup on unmount
    };
  }, []);

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar style="light" />
      <AgentScreen />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: '#0F172A', // Match dashboard background
  },
});
