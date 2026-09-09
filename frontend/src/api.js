const API_BASE = (import.meta.env?.VITE_API_URL || '/api/v1').replace(/\/$/, '');
const STORE_KEY = 'relaydesk.session';

const initialSession = () => {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
  } catch {
    return null;
  }
};

let session = initialSession();
let sessionGeneration = 0;
let refreshPromise = null;
let refreshGeneration = null;
const sessionListeners = new Set();

function saveSession(next, { preserveGeneration = false } = {}) {
  session = next;
  if (!preserveGeneration) sessionGeneration += 1;
  refreshPromise = null;
  refreshGeneration = null;
  if (next) localStorage.setItem(STORE_KEY, JSON.stringify(next));
  else localStorage.removeItem(STORE_KEY);
  sessionListeners.forEach((listener) => listener(session));
}

function refreshStillBelongsTo(snapshot, generation) {
  return sessionGeneration === generation && session?.refreshToken === snapshot?.refreshToken;
}

async function parse(response) {
  const isJson = response.headers.get('content-type')?.includes('application/json');
  const body = isJson ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(body?.error?.message || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}

async function refreshSession(expectedGeneration) {
  const snapshot = session;
  const refreshToken = snapshot?.refreshToken;
  if (!refreshToken || sessionGeneration !== expectedGeneration) return false;
  if (refreshPromise && refreshGeneration === expectedGeneration) return refreshPromise;

  const pendingRefresh = (async () => {
    try {
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${refreshToken}` },
      });
      if (!response.ok) {
        if (refreshStillBelongsTo(snapshot, expectedGeneration)) saveSession(null);
        return false;
      }
      const refreshed = await response.json();
      if (!refreshStillBelongsTo(snapshot, expectedGeneration)) return false;
      saveSession({ ...snapshot, ...refreshed }, { preserveGeneration: true });
      return true;
    } catch {
      if (refreshStillBelongsTo(snapshot, expectedGeneration)) saveSession(null);
      return false;
    } finally {
      if (refreshGeneration === expectedGeneration) {
        refreshPromise = null;
        refreshGeneration = null;
      }
    }
  })();
  refreshPromise = pendingRefresh;
  refreshGeneration = expectedGeneration;
  return pendingRefresh;
}

async function request(path, options = {}, retry = true) {
  const requestGeneration = sessionGeneration;
  const requestAccessToken = session?.accessToken || null;
  const headers = { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) };
  if (requestAccessToken) headers.Authorization = `Bearer ${requestAccessToken}`;
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (response.status === 401 && retry) {
    // A login or logout happened while this request was in flight. Never retry
    // account A's request with account B's credentials.
    if (sessionGeneration !== requestGeneration) return parse(response);
    // This response used an access token that another request has already
    // refreshed. Reuse that token instead of starting a serial second refresh.
    if (session?.accessToken && session.accessToken !== requestAccessToken) {
      return request(path, options, false);
    }
    const refreshed = await refreshSession(requestGeneration);
    if (
      refreshed
      && sessionGeneration === requestGeneration
      && session?.accessToken
      && session.accessToken !== requestAccessToken
    ) {
      return request(path, options, false);
    }
  }
  return parse(response);
}

export const api = {
  getSession: () => session,
  subscribeSession(listener) {
    sessionListeners.add(listener);
    return () => sessionListeners.delete(listener);
  },
  async login(email, password) {
    const response = await request('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }, false);
    saveSession(response);
    return response;
  },
  async logout() {
    const accessToken = session?.accessToken;
    saveSession(null);
    if (!accessToken) return;
    try {
      await fetch(`${API_BASE}/auth/logout`, { method: 'POST', headers: { Authorization: `Bearer ${accessToken}` } });
    } catch { /* Local logout remains safe when the server is unavailable. */ }
  },
  me: () => request('/auth/me'),
  tickets: (params = {}) => request(`/tickets?${new URLSearchParams(Object.entries(params).filter(([, value]) => value !== '' && value != null)).toString()}`),
  ticket: (id) => request(`/tickets/${id}`),
  createTicket: (payload) => request('/tickets', { method: 'POST', body: JSON.stringify(payload) }),
  updateTicket: (id, payload) => request(`/tickets/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  assignTicket: (id, assigneeId) => request(`/tickets/${id}/assignment`, { method: 'POST', body: JSON.stringify({ assigneeId }) }),
  reply: (id, payload) => request(`/tickets/${id}/reply`, { method: 'POST', body: JSON.stringify(payload) }),
  draft: (id, instruction = '') => request(`/tickets/${id}/draft`, { method: 'POST', body: JSON.stringify({ instruction }) }),
  categories: () => request('/categories'),
  templates: () => request('/templates'),
  template: (id) => request(`/templates/${id}`),
  createTemplate: (payload) => request('/templates', { method: 'POST', body: JSON.stringify(payload) }),
  updateTemplate: (id, payload) => request(`/templates/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  approveTemplate: (id, approved) => request(`/templates/${id}/approval`, { method: 'POST', body: JSON.stringify({ approved }) }),
  reports: (days = 30) => request(`/reports/overview?days=${days}`),
  volume: (days = 30) => request(`/reports/volume?days=${days}`),
  workload: () => request('/reports/workload'),
  users: () => request('/admin/users'),
  createUser: (payload) => request('/admin/users', { method: 'POST', body: JSON.stringify(payload) }),
  updateUser: (id, payload) => request(`/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  audit: (params = {}) => request(`/admin/audit?${new URLSearchParams(params).toString()}`),
  companyProfile: () => request('/admin/company-profile'),
  updateCompanyProfile: (payload) => request('/admin/company-profile', { method: 'PUT', body: JSON.stringify(payload) }),
  operations: () => request('/admin/operations'),
  pollMailbox: () => request('/admin/operations/poll-mailbox', { method: 'POST' }),
  deliverOutbox: () => request('/admin/operations/deliver-outbox', { method: 'POST' }),
  checkSla: () => request('/admin/operations/check-sla', { method: 'POST' }),
};
