/**
 * SignalHistory — GET /api/signals/recent → journal decisions log
 * Shows a filterable table of all decisions (LONG / SHORT / NO_TRADE).
 * Includes a mini confidence chart using recharts.
 */
import React, { useState, useEffect, useCallback } from 'react'
import {
  ResponsiveContainer, BarChart, Bar,
  XAxis, YAxis, Tooltip, Cell,
} from 'recharts'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function DirectionBadge({ direction }) {
  const map = {
    long:     { cls: 'badge-long',     label: '▲ LONG' },
    short:    { cls: 'badge-short',    label: '▼ SHORT' },
    no_trade: { cls: 'badge-no-trade', label: '— WAIT' },
  }
  const { cls, label } = map[direction] ?? map.no_trade
  return <span className={`badge ${cls}`}>{label}</span>
}

function ConfDot({ value }) {
  const pct = Math.round((value ?? 0) * 100)
  const color = pct >= 70 ? 'var(--long)' : pct >= 45 ? 'var(--warning)' : 'var(--short)'
  return (
    <span
      className="font-mono"
      style={{ fontSize: 12, color, minWidth: 38, display: 'inline-block' }}
    >
      {pct > 0 ? `${pct}%` : '—'}
    </span>
  )
}

function MiniChart({ signals }) {
  // Take last 30 decisions for the chart
  const recent = signals.slice(-30)
  const data = recent.map((s, i) => ({
    name: s.symbol ?? '?',
    confidence: Math.round((s.confidence ?? 0) * 100),
    dir: s.direction,
  }))

  const colors = { long: '#00E676', short: '#FF4560', no_trade: '#546E7A' }

  if (data.length === 0) return null
  return (
    <div className="card" style={{ padding: '0 0 4px' }}>
      <div className="card-header">
        <span className="card-title">Confidence History (last {data.length} signals)</span>
      </div>
      <div style={{ padding: '16px 20px 8px' }}>
        <ResponsiveContainer width="100%" height={120}>
          <BarChart data={data} margin={{ top: 0, right: 0, bottom: 0, left: -24 }}>
            <XAxis
              dataKey="name"
              tick={{ fontSize: 9, fill: '#546E7A' }}
              interval={Math.max(0, Math.floor(data.length / 8) - 1)}
            />
            <YAxis
              domain={[0, 100]}
              tick={{ fontSize: 9, fill: '#546E7A' }}
              tickFormatter={v => `${v}%`}
            />
            <Tooltip
              contentStyle={{
                background: '#1A1F2E',
                border: '1px solid #1E2433',
                borderRadius: 8,
                fontSize: 12,
              }}
              formatter={(v, name) => [`${v}%`, 'Confidence']}
              labelStyle={{ color: '#94A3B8', marginBottom: 4 }}
            />
            <Bar dataKey="confidence" radius={[3, 3, 0, 0]}>
              {data.map((entry, i) => (
                <Cell key={i} fill={colors[entry.dir] ?? colors.no_trade} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function SummaryStats({ signals }) {
  if (signals.length === 0) return null
  const longs    = signals.filter(s => s.direction === 'long').length
  const shorts   = signals.filter(s => s.direction === 'short').length
  const noTrades = signals.filter(s => s.direction === 'no_trade').length
  const actionable = signals.filter(s => s.direction !== 'no_trade').length
  const avgConf = signals.length > 0
    ? (signals.reduce((a, s) => a + (s.confidence ?? 0), 0) / signals.length * 100).toFixed(1)
    : '—'

  return (
    <div className="stat-grid fade-in">
      <div className="stat-tile">
        <span className="stat-label">Total Signals</span>
        <span className="stat-value">{signals.length}</span>
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
      <div className="stat-tile">
        <span className="stat-label">Actionable</span>
        <span className="stat-value blue">{actionable}</span>
      </div>
      <div className="stat-tile">
        <span className="stat-label">Avg Confidence</span>
        <span className="stat-value">{avgConf}%</span>
      </div>
    </div>
  )
}

export default function SignalHistory() {
  const [signals, setSignals]       = useState([])
  const [loading, setLoading]       = useState(false)
  const [error, setError]           = useState(null)
  const [filterDir, setFilterDir]   = useState('all')
  const [filterSym, setFilterSym]   = useState('')
  const [limit, setLimit]           = useState(100)
  const [expandedRow, setExpandedRow] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API}/api/signals/recent?limit=${limit}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      const arr = Array.isArray(data) ? data : (data.signals ?? [])
      // Newest first
      setSignals([...arr].reverse())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [limit])

  useEffect(() => { load() }, [load])

  // Filtered view
  const filtered = signals.filter(s => {
    if (filterDir !== 'all' && s.direction !== filterDir) return false
    if (filterSym && !((s.symbol ?? '').toUpperCase().includes(filterSym.toUpperCase()))) return false
    return true
  })

  const toggleRow = (idx) => setExpandedRow(expandedRow === idx ? null : idx)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* ── Controls ── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">Signal History</span>
          <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            {filtered.length} / {signals.length} records
          </span>
        </div>
        <div className="card-body">
          <div className="history-filter-bar">
            <input
              className="input-field"
              style={{ maxWidth: 180 }}
              placeholder="Filter by symbol…"
              value={filterSym}
              onChange={e => setFilterSym(e.target.value.toUpperCase())}
            />

            <select
              className="select-field"
              value={filterDir}
              onChange={e => setFilterDir(e.target.value)}
            >
              <option value="all">All directions</option>
              <option value="long">Long only</option>
              <option value="short">Short only</option>
              <option value="no_trade">No trade only</option>
            </select>

            <select
              className="select-field"
              value={limit}
              onChange={e => setLimit(Number(e.target.value))}
            >
              <option value={50}>Last 50</option>
              <option value={100}>Last 100</option>
              <option value={200}>Last 200</option>
              <option value={500}>Last 500</option>
            </select>

            <button
              className="btn btn-primary"
              onClick={load}
              disabled={loading}
            >
              {loading
                ? <><span className="spinner" style={{ width: 14, height: 14 }} /> Loading…</>
                : '↻ Refresh'
              }
            </button>
          </div>
        </div>
      </div>

      {/* ── Error ── */}
      {error && (
        <div className="error-box fade-in">
          <span>⚠</span>
          <div>
            <strong>Failed to load history</strong>
            <div style={{ fontSize: 12, marginTop: 4 }}>{error}</div>
          </div>
        </div>
      )}

      {/* ── Summary Stats ── */}
      {signals.length > 0 && <SummaryStats signals={signals} />}

      {/* ── Confidence Chart ── */}
      {signals.length > 0 && <MiniChart signals={[...signals].reverse()} />}

      {/* ── No data state ── */}
      {!loading && signals.length === 0 && !error && (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state-icon">📋</div>
            <div className="empty-state-title">No signal history yet</div>
            <div className="empty-state-desc">
              Run the Coin Analyzer or Market Scanner to generate signals.
              All decisions (including NO_TRADE) are recorded here.
            </div>
          </div>
        </div>
      )}

      {/* ── Table ── */}
      {filtered.length > 0 && (
        <div className="card fade-in">
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Symbol</th>
                  <th>Direction</th>
                  <th>Confidence</th>
                  <th>Regime</th>
                  <th>Strategies</th>
                  <th>Reason / Timestamp</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((sig, i) => (
                  <React.Fragment key={sig.ts ? `${sig.symbol}-${sig.ts}` : i}>
                    <tr
                      style={{ cursor: 'pointer' }}
                      onClick={() => toggleRow(i)}
                    >
                      <td>
                        <span className="font-mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                          {filtered.length - i}
                        </span>
                      </td>
                      <td>
                        <span className="font-mono" style={{ fontWeight: 700, fontSize: 13 }}>
                          {sig.symbol ?? '—'}
                        </span>
                      </td>
                      <td>
                        <DirectionBadge direction={sig.direction} />
                      </td>
                      <td>
                        <ConfDot value={sig.confidence} />
                      </td>
                      <td>
                        <span style={{ fontSize: 12, color: 'var(--text-dim)', textTransform: 'capitalize' }}>
                          {sig.regime ? sig.regime.replace(/_/g, ' ') : '—'}
                        </span>
                      </td>
                      <td>
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', maxWidth: 240 }}>
                          {(sig.strategies || []).slice(0, 3).map(s => (
                            <span
                              key={s}
                              style={{
                                fontSize: 10,
                                padding: '2px 6px',
                                background: 'var(--long-dim)',
                                color: 'var(--long)',
                                borderRadius: 10,
                                border: '1px solid rgba(0,230,118,0.2)',
                              }}
                            >
                              {s.replace(/_/g, ' ')}
                            </span>
                          ))}
                          {(sig.strategies || []).length === 0 && (
                            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>none</span>
                          )}
                        </div>
                      </td>
                      <td style={{ maxWidth: 260 }}>
                        <div>
                          <span className="font-mono" style={{ fontSize: 11, color: 'var(--text-muted)', display: 'block' }}>
                            {sig.ts ? new Date(sig.ts).toLocaleString() : '—'}
                          </span>
                          {sig.no_trade_reason && (
                            <span style={{ fontSize: 10, color: 'var(--text-muted)', opacity: 0.7, display: 'block', marginTop: 2, wordBreak: 'break-word' }}>
                              {sig.no_trade_reason.replace('SCORED_NO_TRADE: ', '')}
                            </span>
                          )}
                        </div>
                      </td>
                      <td>
                        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                          {expandedRow === i ? '▲' : '▼'}
                        </span>
                      </td>
                    </tr>

                    {/* ── Expanded Row Detail ── */}
                    {expandedRow === i && (
                      <tr>
                        <td colSpan={8} style={{ padding: '0 16px 16px', background: 'var(--bg-primary)' }}>
                          <div
                            style={{
                              marginTop: 12,
                              background: 'var(--bg-elevated)',
                              border: '1px solid var(--border)',
                              borderRadius: 8,
                              padding: 16,
                              display: 'grid',
                              gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
                              gap: 12,
                            }}
                          >
                            {[
                              { label: 'Symbol',     value: sig.symbol },
                              { label: 'Direction',  value: sig.direction },
                              { label: 'Confidence', value: sig.confidence != null ? `${(sig.confidence * 100).toFixed(1)}%` : '—' },
                              { label: 'Entry',      value: sig.entry != null ? `$${sig.entry}` : null },
                              { label: 'Stop',       value: sig.stop != null ? `$${sig.stop}` : null },
                              { label: 'R/R',        value: sig.rr != null && sig.rr > 0 ? `${sig.rr}R` : null },
                              { label: 'Regime',     value: sig.regime?.replace(/_/g, ' ') },
                              { label: 'Phase',      value: sig.phase?.replace(/_/g, ' ') },
                              { label: 'Timestamp',  value: sig.ts ? new Date(sig.ts).toLocaleString() : '—' },
                            ].filter(item => item.value).map(item => (
                              <div key={item.label} className="detail-item" style={{ background: 'var(--bg-card)' }}>
                                <div className="detail-item-label">{item.label}</div>
                                <div className="detail-item-value" style={{ fontSize: 13 }}>{item.value}</div>
                              </div>
                            ))}
                          </div>

                          {/* Targets */}
                          {sig.targets && sig.targets.length > 0 && (
                            <div style={{ marginTop: 10 }}>
                              <div className="section-label" style={{ marginBottom: 6 }}>Targets</div>
                              <div className="target-list">
                                {sig.targets.map((t, ti) => (
                                  <span key={ti} className="target-tag">
                                    TP{ti + 1}: ${typeof t === 'number' ? t.toLocaleString() : t}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* No trade reason */}
                          {sig.no_trade_reason && (
                            <div
                              style={{
                                marginTop: 10,
                                padding: '10px 14px',
                                background: 'var(--no-trade-dim)',
                                border: '1px solid rgba(84,110,122,0.3)',
                                borderRadius: 8,
                                fontSize: 12,
                                color: 'var(--no-trade)',
                              }}
                            >
                              <strong>Reason:</strong> {sig.no_trade_reason}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
