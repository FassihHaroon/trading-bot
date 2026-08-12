const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export { API }

export function getToken() {
  return localStorage.getItem('auth_token')
}

export function setToken(token) {
  localStorage.setItem('auth_token', token)
}

export function clearToken() {
  localStorage.removeItem('auth_token')
  window.dispatchEvent(new Event('auth:logout'))
}

/**
 * Drop-in replacement for fetch() that adds the Authorization header.
 * On 401, clears the token and fires 'auth:logout' so App can redirect.
 */
export async function authFetch(path, options = {}) {
  const token = getToken()
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }
  const res = await fetch(`${API}${path}`, { ...options, headers })
  if (res.status === 401) {
    clearToken()
    throw new Error('session_expired')
  }
  return res
}
