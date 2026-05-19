export const WS_BASE_URL = process.env.EXPO_PUBLIC_WS_BASE_URL || 'ws://localhost:8000';
export const WS_AGENT_ENDPOINT = '/v1/agent/connect';

export const getCleanHost = () => {
  return WS_BASE_URL.replace(/^(ws:\/\/|wss:\/\/|http:\/\/|https:\/\/)/, '');
};

export const getHttpBaseUrl = () => {
  const cleanHost = getCleanHost();
  const protocol = WS_BASE_URL.startsWith('wss://') ? 'https' : 'http';
  return `${protocol}://${cleanHost}`;
};

export const HTTP_BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL || getHttpBaseUrl();
