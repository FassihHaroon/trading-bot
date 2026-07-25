/**
 * CoinAnalyzer — POST /api/analyze for a specific symbol
 * Shows full SignalCard result plus quick-select buttons.
 */
import { useState, useEffect, useCallback } from 'react'
import SignalCard from './SignalCard.jsx'

const API = 'http://localhost:8000'

const DEFAULT_SYMBOLS = [
  'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT',
  'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'DOTUSDT', 'MATICUSDT',
  'LINKUSDT', 'UNIUSDT', 'ATOMUSDT', 'LTCUSDT', 'NEARUSDT',
]

export default function CoinAnalyzer({ onPaperTradeOpened }) {
  const [symbol, setSymbol]       = useState('BTCUSDT')
  const [input, setInput]         = useState('BTCUSDT')
  const [signal, setSignal]       = useState(null)
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState(null)
  const [availSymbols, setAvailSymbols] = useState(DEFAULT_SYMBOLS)

  // Load available symbols from backend
  useEffect(() => {
    fetch(`${API}/api/symbols`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        const list = Array.isArray(data) ? data : data?.symbols
        if (Array.isArray(list) && list.length) setAvailSymbols(list)
      })
      .catch(() => {}) // silently fall back to defaults
  }, [])

  const analyze = useCallback(async (sym) => {
    const target = (sym ?? input).trim().toUpperCase()
    if (!target) return
    setSymbol(target)
    setInput(target)
    setLoading(true)
    setError(null)
    setSignal(null)

    try {
      const res = await fetch(`${API}/api/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: target }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? `HTTP ${res.status}`)
      }
      const data = await res.json()
      setSignal(data)
    } catch (err) {
      setError(err.message || 'Request failed')
    } finally {
      setLoading(false)
    }
  }, [input])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') analyze()
  }

  return (
    <div className="analyzer-layout">
      {/* ── Search Bar ── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">Coin Analyzer</span>
          {signal && !loading && (
            <span className="badge badge-accent">
              {signal.symbol}
            </span>
          )}
        </div>
        <div className="card-body">
          <div className="analyzer-search-bar">
            <input
              className="input-field"
              type="text"
              placeholder="e.g. BTCUSDT"
              value={input}
              onChange={e => setInput(e.target.value.toUpperCase())}
              onKeyDown={handleKeyDown}
              disabled={loading}
              autoFocus
            />
            <button
              className="btn btn-primary"
              onClick={() => analyze()}
              disabled={loading || !input.trim()}
            >
              {loading
                ? <><span className="spinner" style={{ width: 14, height: 14 }} /> Analyzing…</>
                : '⚡ Analyze'
              }
            </button>
            {signal && (
              <button
                className="btn btn-secondary"
                onClick={() => analyze(symbol)}
                disabled={loading}
              >
                ↻ Refresh
              </button>
            )}
          </div>

          {/* Quick-select symbols */}
          <div className="quick-symbols">
            {availSymbols.map(s => (
              <button
                key={s}
                className="quick-sym-btn"
                onClick={() => analyze(s)}
                disabled={loading}
              >
                {s.replace('USDT', '')}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* ── Error ── */}
      {error && (
        <div className="error-box fade-in">
          <span>⚠</span>
          <div>
            <strong>Analysis Failed</strong>
            <div style={{ marginTop: 4, fontSize: 12 }}>{error}</div>
          </div>
        </div>
      )}

      {/* ── Loading skeleton ── */}
      {loading && (
        <div className="card">
          <div className="card-body">
            <div className="loading-overlay">
              <div className="spinner" style={{ width: 32, height: 32, borderWidth: 3 }} />
              <div>
                <div style={{ fontWeight: 600, color: 'var(--text-dim)', marginBottom: 4 }}>
                  Analyzing {symbol}…
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  Running strategies, computing confidence, generating AI explanation
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Signal Card ── */}
      {signal && !loading && (
        <SignalCard signal={signal} onPaperTradeOpened={onPaperTradeOpened} />
      )}

      {/* ── Placeholder ── */}
      {!signal && !loading && !error && (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state-icon">🔍</div>
            <div className="empty-state-title">Select a coin to analyze</div>
            <div className="empty-state-desc">
              Enter a symbol above or click one of the quick-select buttons.
              The analyzer runs all strategies and produces a signal with entry,
              stop, and target levels.
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
