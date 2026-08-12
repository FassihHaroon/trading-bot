import { useState } from 'react'
import { API, setToken } from '../api.js'

export default function LoginPage({ onLogin }) {
  const [mode, setMode] = useState('login') // 'login' | 'register'
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)

    const endpoint = mode === 'login' ? '/api/auth/login' : '/api/auth/register'
    try {
      const res = await fetch(`${API}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail || 'Something went wrong')
        return
      }
      if (mode === 'register') {
        // Switch to login after successful registration
        setMode('login')
        setPassword('')
        setError('')
        setUsername(username.trim())
        return
      }
      setToken(data.token)
      onLogin(data.username)
    } catch {
      setError('Cannot reach the server. Is the backend running?')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={styles.wrapper}>
      <div style={styles.card}>
        <div style={styles.brand}>
          <span style={styles.brandIcon}>📈</span>
          <span style={styles.brandName}>TRADING BOT</span>
        </div>

        <div style={styles.tabs}>
          <button
            style={{ ...styles.tab, ...(mode === 'login' ? styles.tabActive : {}) }}
            onClick={() => { setMode('login'); setError('') }}
          >
            Login
          </button>
          <button
            style={{ ...styles.tab, ...(mode === 'register' ? styles.tabActive : {}) }}
            onClick={() => { setMode('register'); setError('') }}
          >
            Register
          </button>
        </div>

        <form onSubmit={handleSubmit} style={styles.form}>
          <label style={styles.label}>Username</label>
          <input
            style={styles.input}
            type="text"
            value={username}
            onChange={e => setUsername(e.target.value)}
            placeholder="your_username"
            autoComplete="username"
            required
            minLength={3}
          />

          <label style={styles.label}>Password</label>
          <input
            style={styles.input}
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="••••••••"
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            required
            minLength={6}
          />

          {error && <p style={styles.error}>{error}</p>}

          <button style={styles.submit} type="submit" disabled={loading}>
            {loading ? 'Please wait…' : mode === 'login' ? 'Login' : 'Create Account'}
          </button>
        </form>

        {mode === 'register' && (
          <p style={styles.hint}>
            Already have an account?{' '}
            <span style={styles.link} onClick={() => setMode('login')}>Login here</span>
          </p>
        )}
        {mode === 'login' && (
          <p style={styles.hint}>
            No account yet?{' '}
            <span style={styles.link} onClick={() => setMode('register')}>Register</span>
          </p>
        )}
      </div>
    </div>
  )
}

const styles = {
  wrapper: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: '#0d1117',
  },
  card: {
    width: 360,
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: 12,
    padding: '32px 28px',
    boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
  },
  brand: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    marginBottom: 24,
    justifyContent: 'center',
  },
  brandIcon: { fontSize: 28 },
  brandName: {
    fontSize: 18,
    fontWeight: 700,
    letterSpacing: 2,
    color: '#58a6ff',
    fontFamily: 'monospace',
  },
  tabs: {
    display: 'flex',
    marginBottom: 24,
    borderBottom: '1px solid #30363d',
  },
  tab: {
    flex: 1,
    background: 'none',
    border: 'none',
    color: '#8b949e',
    cursor: 'pointer',
    paddingBottom: 10,
    fontSize: 14,
    fontWeight: 500,
    borderBottom: '2px solid transparent',
    transition: 'color 0.2s',
  },
  tabActive: {
    color: '#58a6ff',
    borderBottom: '2px solid #58a6ff',
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  label: {
    fontSize: 12,
    color: '#8b949e',
    fontWeight: 600,
    letterSpacing: 0.5,
    marginBottom: -4,
  },
  input: {
    background: '#0d1117',
    border: '1px solid #30363d',
    borderRadius: 6,
    padding: '10px 12px',
    color: '#e6edf3',
    fontSize: 14,
    outline: 'none',
    transition: 'border-color 0.2s',
  },
  error: {
    color: '#f85149',
    fontSize: 13,
    margin: '4px 0',
    background: 'rgba(248,81,73,0.1)',
    border: '1px solid rgba(248,81,73,0.3)',
    borderRadius: 6,
    padding: '8px 12px',
  },
  submit: {
    marginTop: 6,
    background: '#238636',
    color: '#fff',
    border: 'none',
    borderRadius: 6,
    padding: '11px 16px',
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'background 0.2s',
  },
  hint: {
    marginTop: 16,
    textAlign: 'center',
    fontSize: 13,
    color: '#8b949e',
  },
  link: {
    color: '#58a6ff',
    cursor: 'pointer',
    textDecoration: 'underline',
  },
}
