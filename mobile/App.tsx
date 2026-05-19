import React, { useEffect } from 'react';
import { AppState, AppStateStatus, SafeAreaView, StyleSheet } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { AgentScreen } from './src/screens/agent-screen';
import { disconnectAgentWS } from './src/services/websocket-service';

export default function App() {
  // Handle App Lifecycle state cleanup to avoid WebSocket leaks
  useEffect(() => {
    const handleAppStateChange = (nextAppState: AppStateStatus) => {
      if (nextAppState === 'background' || nextAppState === 'inactive') {
        disconnectAgentWS();
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
