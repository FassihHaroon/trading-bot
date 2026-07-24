/**
 * MarketScanner — GET /api/scanner?symbols=... → sorted opportunities
 * Shows a ranked table of signals across multiple symbols with auto-refresh.
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import SignalCard from './SignalCard.jsx'

const API = 'http://localhost:8000'

const DEFAULT_SYMBOLS = [
  'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT',
  'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'DOTUSDT', 'MATICUSDT',
  'LINKUSDT', 'UNIUSDT', 'ATOMUSDT', 'LTCUSDT', 'NEARUSDT',
]

const REFRESH_OPTIONS = [
  { label: 'Off',   value: 0 },
  { label: '30s',   value: 30 },
  { label: '1m',    value: 60 },
  { label: '5m',    value: 300 },
]

function fmt(n, d = 2) {
  if (n == null) return '—'
  return Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })
}

function DirectionCell({ direction }) {
  const map = {
    long:     { cls: 'badge-long',     label: '▲ LONG' },
    short:    { cls: 'badge-short',    label: '▼ SHORT' },
    no_trade: { cls: 'badge-no-trade', label: '— WAIT' },
  }
  const { cls, label } = map[direction] ?? map.no_trade
  return <span className={`badge ${cls}`}>{label}</span>
}

function MiniConfBar({ value }) {
  const pct = Math.round((value ?? 0) * 100)
  const cls = pct >= 70 ? 'conf-high' : pct >= 45 ? 'conf-medium' : 'conf-low'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div className="rr-bar-track" style={{ width: 60 }}>
        <div className={`confidence-bar-fill ${cls}`} style={{ width: `${pct}%`, height: '100%', borderRadius: 3 }} />
      </div>
      <span className="font-mono" style={{ fontSize: 11, color: 'var(--text-muted)', minWidth: 32 }}>{pct}%</span>
    </div>
  )
}

function RRBar({ value, maxRR = 5 }) {
  const pct = Math.min(100, ((value ?? 0) / maxRR) * 100)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div className="rr-bar-track">
        <div className="rr-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono" style={{ fontSize: 11, color: 'var(--text-muted)', minWidth: 28 }}>
        {value != null ? value.toFixed(1) + 'R' : '—'}
      </span>
    </div>
  )
}

export default function MarketScanner() {
  const [selectedSymbols, setSelectedSymbols] = useState(DEFAULT_SYMBOLS.slice(0, 10))
  const [availSymbols, setAvailSymbols]       = useState(DEFAULT_SYMBOLS)
  const [results, setResults]                 = useState([])
  const [loading, setLoading]                 = useState(false)
  const [error, setError]                     = useState(null)
  const [lastScan, setLastScan]               = useState(null)
  const [refreshInterval, setRefreshInterval] = useState(0)
  const [expandedRow, setExpandedRow]         = useState(null)
  const [filterDir, setFilterDir]             = useState('all')
  const [filterActionable, setFilterActionable] = useState(false)
  const timerRef = useRef(null)

  // Load available symbols
  useEffect(() => {
    fetch(`${API}/api/symbols`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        const list = Array.isArray(data) ? data : data?.symbols
        if (Array.isArray(list) && list.length) setAvailSymbols(list)
      })
      .catch(() => {})
  }, [])

  const runScan = useCallback(async () => {
    if (selectedSymbols.length === 0) return
    setLoading(true)
    setError(null)

    try {
      const params = new URLSearchParams({ symbols: selectedSymbols.join(',') })
      const res = await fetch(`${API}/api/scanner?${params}`)
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? `HTTP ${res.status}`)
      }
      const data = await res.json()
      // Support both array and {results: [...]} shapes
      setResults(Array.isArray(data) ? data : (data.results ?? []))
      setLastScan(new Date())
    } catch (err) {
      setError(err.message || 'Scan failed')
    } finally {
      setLoading(false)
    }
  }, [selectedSymbols])

  // Auto-refresh
  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current)
    if (refreshInterval > 0) {
      timerRef.current = setInterval(runScan, refreshInterval * 1000)
    }
    return () => clearInterval(timerRef.current)
  }, [refreshInterval, runScan])

  const toggleSymbol = (sym) => {
    setSelectedSymbols(prev =>
      prev.includes(sym) ? prev.filter(s => s !== sym) : [...prev, sym]
    )
  }

  const selectAll = () => setSelectedSymbols([...availSymbols])
  const clearAll  = () => setSelectedSymbols([])

  // Filter results
  const filtered = results.filter(r => {
    if (filterDir !== 'all' && r.direction !== filterDir) return false
    if (filterActionable && !r.is_actionable) return false
    return true
  })

  // Summary counts
  const longs    = results.filter(r => r.direction === 'long').length
  const shorts   = results.filter(r => r.direction === 'short').length
  const noTrades = results.filter(r => r.direction === 'no_trade').length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* ── Control Panel ── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">Market Scanner</span>
          {lastScan && (
            <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              Last scan: {lastScan.toLocaleTimeString()}
            </span>
          )}
        </div>
        <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Symbol Toggles */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <span className="section-label">Symbols ({selectedSymbols.length} selected)</span>
              <div style={{ display: 'flex', gap: 6 }}>
                <button className="btn btn-secondary btn-sm" onClick={selectAll}>All</button>
                <button className="btn btn-secondary btn-sm" onClick={clearAll}>None</button>
              </div>
            </div>
            <div className="scanner-symbol-list">
              {availSymbols.map(sym => (
                <button
                  key={sym}
                  className={`scanner-sym-toggle${selectedSymbols.includes(sym) ? ' selected' : ''}`}
                  onClick={() => toggleSymbol(sym)}
                >
                  {sym.replace('USDT', '')}
                </button>
              ))}
            </div>
          </div>

          {/* Controls Row */}
          <div className="scanner-header-bar">
            <button
              className="btn btn-primary"
              onClick={runScan}
              disabled={loading || selectedSymbols.length === 0}
            >
              {loading
                ? <><span className="spinner" style={{ width: 14, height: 14 }} /> Scanning…</>
                : '📡 Run Scan'
              }
            </button>

            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Auto-refresh:</span>
              <select
                className="select-field"
                value={refreshInterval}
                onChange={e => setRefreshInterval(Number(e.target.value))}
              >
                {REFRESH_OPTIONS.map(o => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Direction:</span>
              <select
                className="select-field"
                value={filterDir}
                onChange={e => setFilterDir(e.target.value)}
              >
                <option value="all">All</option>
                <option value="long">Long only</option>
                <option value="short">Short only</option>
                <option value="no_trade">No trade</option>
              </select>
            </div>

            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-muted)', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={filterActionable}
                onChange={e => setFilterActionable(e.target.checked)}
                style={{ accentColor: 'var(--accent)' }}
              />
              Actionable only
            </label>
          </div>
        </div>
      </div>

      {/* ── Error ── */}
      {error && (
        <div className="error-box fade-in">
          <span>⚠</span>
          <div>
            <strong>Scan Failed</strong>
            <div style={{ marginTop: 4, fontSize: 12 }}>{error}</div>
          </div>
        </div>
      )}

      {/* ── Summary Row ── */}
      {results.length > 0 && (
        <div className="stat-grid fade-in">
          <div className="stat-tile">
            <span className="stat-label">Total Scanned</span>
            <span className="stat-value blue">{results.length}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-label">Long Signals</span>
            <span className="stat-value green">{longs}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-label">Short Signals</span>
            <span className="stat-value red">{shorts}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-label">No Trade</span>
            <span className="stat-value muted">{noTrades}</span>
          </div>
          {filtered.length !== results.length && (
            <div className="stat-tile">
              <span className="stat-label">Showing</span>
              <span className="stat-value">{filtered.length}</span>
            </div>
          )}
        </div>
      )}

      {/* ── Results Table ── */}
      {filtered.length > 0 && (
        <div className="card fade-in">
          <div className="card-header">
            <span className="card-title">Ranked Opportunities</span>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Sorted by confidence × risk/reward
            </span>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Symbol</th>
                  <th>Direction</th>
                  <th>Confidence</th>
                  <th>R/R</th>
                  <th>Entry</th>
                  <th>Stop</th>
                  <th>Regime</th>
                  <th>Actionable</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((row, i) => (
                  <>
                    <tr key={row.symbol} onClick={() => setExpandedRow(expandedRow === row.symbol ? null : row.symbol)}>
                      <td>
                        <span className={`rank-num${i < 3 ? ' top' : ''}`}>{i + 1}</span>
                      </td>
                      <td>
                        <span className="font-mono" style={{ fontWeight: 700, fontSize: 13 }}>
                          {row.symbol}
                        </span>
                      </td>
                      <td><DirectionCell direction={row.direction} /></td>
                      <td><MiniConfBar value={row.confidence} /></td>
                      <td><RRBar value={row.risk_reward} /></td>
                      <td>
                        <span className="font-mono" style={{ fontSize: 12 }}>
                          {row.entry_price ? '$' + fmt(row.entry_price, row.entry_price > 100 ? 2 : 4) : '—'}
                        </span>
                      </td>
                      <td>
                        <span className="font-mono text-red" style={{ fontSize: 12 }}>
                          {row.stop_price ? '$' + fmt(row.stop_price, row.stop_price > 100 ? 2 : 4) : '—'}
                        </span>
                      </td>
                      <td>
                        <span className="text-dim" style={{ fontSize: 12, textTransform: 'capitalize' }}>
                          {row.regime ? row.regime.replace(/_/g, ' ') : '—'}
                        </span>
                      </td>
                      <td>
                        {row.is_actionable
                          ? <span className="badge badge-long" style={{ fontSize: 10 }}>Yes</span>
                          : <span className="badge badge-no-trade" style={{ fontSize: 10 }}>No</span>
                        }
                      </td>
                      <td>
                        <button className="btn btn-secondary btn-sm">
                          {expandedRow === row.symbol ? '▲' : '▼'}
                        </button>
                      </td>
                    </tr>
                    {expandedRow === row.symbol && (
                      <tr key={`${row.symbol}-detail`}>
                        <td colSpan={10} style={{ padding: '0 16px 16px', background: 'var(--bg-primary)' }}>
                          <div style={{ paddingTop: 12 }}>
                            <SignalCard signal={row} />
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Empty / Loading State ── */}
      {!loading && results.length === 0 && !error && (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state-icon">📡</div>
            <div className="empty-state-title">No scan results yet</div>
            <div className="empty-state-desc">
              Select the symbols you want to monitor and click Run Scan to analyse them all at once.
              Results are sorted by opportunity score.
            </div>
          </div>
        </div>
      )}

      {loading && results.length === 0 && (
        <div className="card">
          <div className="loading-overlay">
            <div className="spinner" style={{ width: 36, height: 36, borderWidth: 3 }} />
            <div>
              <div style={{ fontWeight: 600, color: 'var(--text-dim)', marginBottom: 4 }}>
                Scanning {selectedSymbols.length} symbols…
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                This may take a moment while strategies are evaluated for each coin.
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
