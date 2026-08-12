import { useState, useEffect } from 'react'
import CoinAnalyzer from './components/CoinAnalyzer.jsx'
import MarketScanner from './components/MarketScanner.jsx'
import SignalHistory from './components/SignalHistory.jsx'
import PaperTrading from './components/PaperTrading.jsx'
import LoginPage from './components/LoginPage.jsx'
import { getToken, clearToken, authFetch } from './api.js'

const TABS = [
  { id: 'analyzer', label: 'Coin Analyzer',  icon: '🔍' },
  { id: 'scanner',  label: 'Market Scanner', icon: '📡' },
  { id: 'history',  label: 'Signal History', icon: '📋' },
  { id: 'paper',    label: 'Paper Trading',  icon: '📄' },
]

function HeaderClock() {
  const [time, setTime] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <span className="header-time">
      {time.toUTCString().replace('GMT', 'UTC').slice(0, -4)}
    </span>
  )
}

export default function App() {
  const [activeTab, setActiveTab] = useState('analyzer')
  const [user, setUser] = useState(null)       // null = not logged in
  const [authReady, setAuthReady] = useState(false)

  // On mount: validate stored token
  useEffect(() => {
    const token = getToken()
    if (!token) {
      setAuthReady(true)
      return
    }
    authFetch('/api/auth/me')
      .then(r => r.json())
      .then(data => {
        if (data.username) setUser(data.username)
        else clearToken()
      })
      .catch(() => clearToken())
      .finally(() => setAuthReady(true))
  }, [])

  // Listen for auth:logout events (fired by authFetch on 401)
  useEffect(() => {
    const handler = () => setUser(null)
    window.addEventListener('auth:logout', handler)
    return () => window.removeEventListener('auth:logout', handler)
  }, [])

  function handleLogin(username) {
    setUser(username)
  }

  function handleLogout() {
    clearToken()
    setUser(null)
  }

  if (!authReady) return null   // brief flash prevention

  if (!user) {
    return <LoginPage onLogin={handleLogin} />
  }

  return (
    <div className="app-wrapper">
      {/* ── Header ── */}
      <header className="app-header">
        <div className="header-brand">
          <div className="brand-icon">📈</div>
          <span className="brand-name">TRADING BOT</span>
          <span className="brand-tag">LIVE</span>
        </div>
        <div className="header-status">
          <div className="status-dot" />
          <span className="status-text">API Connected</span>
          <HeaderClock />
          <span style={{ marginLeft: 16, color: '#8b949e', fontSize: 13 }}>
            {user}
          </span>
          <button
            onClick={handleLogout}
            style={{
              marginLeft: 10,
              background: 'none',
              border: '1px solid #30363d',
              borderRadius: 4,
              color: '#8b949e',
              cursor: 'pointer',
              fontSize: 12,
              padding: '3px 8px',
            }}
          >
            Logout
          </button>
        </div>
      </header>

      {/* ── Nav Tabs ── */}
      <nav className="nav-tabs">
        {TABS.map(tab => (
          <button
            key={tab.id}
            className={`nav-tab${activeTab === tab.id ? ' active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            <span className="tab-icon">{tab.icon}</span>
            {tab.label}
          </button>
        ))}
      </nav>

      {/* ── Page Content ── */}
      <main className="main-content">
        {activeTab === 'analyzer' && (
          <CoinAnalyzer onPaperTradeOpened={() => setActiveTab('paper')} />
        )}
        {activeTab === 'scanner'  && <MarketScanner />}
        {activeTab === 'history'  && <SignalHistory />}
        {activeTab === 'paper'    && <PaperTrading />}
      </main>
    </div>
  )
}
