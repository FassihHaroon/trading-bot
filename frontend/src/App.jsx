import { useState, useEffect } from 'react'
import CoinAnalyzer from './components/CoinAnalyzer.jsx'
import MarketScanner from './components/MarketScanner.jsx'
import SignalHistory from './components/SignalHistory.jsx'
import PaperTrading from './components/PaperTrading.jsx'

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
