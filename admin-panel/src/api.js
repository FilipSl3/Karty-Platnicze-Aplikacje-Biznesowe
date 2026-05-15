import axios from 'axios'

const api = axios.create({
  baseURL: '/api/v1',
  headers: {
    'Content-Type': 'application/json',
    'X-Admin-Key': 'admin-secret-key-2026'
  }
})

// X-API-Key tylko dla operacji bankowych (issue)
api.interceptors.request.use(config => {
  const apiKey = localStorage.getItem('apiKey')
  if (apiKey && config.url.includes('/issue')) {
    config.headers['X-API-Key'] = apiKey
  }
  return config
})

export const cardAPI = {
  list: () => api.get('/cards'),
  get: (token) => api.get(`/cards/${token}`),
  issue: (data) => api.post('/cards/issue', data),
  block: (token, reason) =>
    api.patch(`/cards/${token}/status`, { status: 'BLOCKED', reason }),
  unblock: (token) =>
    api.patch(`/cards/${token}/status`, { status: 'ACTIVE', reason: '' }),
  lifecycle: (token, newStatus) =>
    api.patch(`/cards/${token}/lifecycle`, {
      new_status: newStatus,
      changed_by: 'admin_panel'
    }),
  activate: (token) =>
    api.post(`/cards/${token}/activate`, { activated_by: 'admin_panel' }),
  topup: (token, amount) =>
    api.post(`/cards/${token}/topup`, { amount, currency: 'PLN' }),
}

export default api