/**
 * PaperTrading — simulated paper trade tracker.
 * NO real money, NO real orders. Tracks bot signals as fake trades.
 * Open positions auto-update every 30s. SL/TP auto-close via backend.
 */
import { useState, useEffect, useCallback, useRef } from 'react'

const API = 'http://localhost:8000'

function fmt(n, d = 2) {
  if (n == null) return '—'
  return Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })
}

function fmtPrice(p) {
  if (p == null) return '—'
  const n = Number(p)
  return '$' + (n > 100 ? fmt(n, 2) : fmt(n, 4))
}

function pnlClass(v) {
  if (v == null) return ''
  return v > 0 ? 'text-green' : v < 0 ? 'text-red' : 'text-dim'
}

function DirectionBadge({ direction }) {
  if (direction === 'long') {
    return <span className="badge badge-long">▲ LONG</span>
  }
  if (direction === 'short') {
    return <span className="badge badge-short">▼ SHORT</span>
  }
  return null
}

function OutcomeBadge({ outcome }) {
  if (outcome === 'win')       return <span className="badge badge-long">WIN</span>
  if (outcome === 'loss')      return <span className="badge badge-short">LOSS</span>
  if (outcome === 'breakeven') return <span className="badge badge-no-trade">B/E</span>
  return <span className="badge badge-no-trade">{outcome ?? '—'}</span>
}

function ExitReasonLabel({ reason }) {
  const labels = {
    stop_loss:      '⛔ Stop Loss',
    take_profit_1:  '✅ Take Profit',
    manual_close:   '🖐 Manual Close',
  }
  return <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{labels[reason] ?? reason ?? '—'}</span>
}

function PnlCell({ unrealized_pnl, unrealized_pct }) {
  const cls = pnlClass(unrealized_pnl)
  return (
    <div className={`font-mono ${cls}`} style={{ fontSize: 13, fontWeight: 700 }}>
      {unrealized_pnl != null ? (unrealized_pnl >= 0 ? '+' : '') + fmt(unrealized_pnl, 2) : '—'}
      {unrealized_pct != null && (
        <span style={{ fontSize: 10, marginLeft: 4, opacity: 0.75 }}>
          ({unrealized_pct >= 0 ? '+' : ''}{fmt(unrealized_pct, 2)}%)
        </span>
      )}
    </div>
  )
}

function DistanceBar({ entry, current, stop, tp1, direction }) {
  if (!entry || !stop || !tp1) return null
  const range = Math.abs(tp1 - stop)
  if (range === 0) return null
  const pos = direction === 'long'
    ? Math.min(1, Math.max(0, (current - stop) / range))
    : Math.min(1, Math.max(0, (stop - current) / range))
  const pct = Math.round(pos * 100)
  const barColor = pct >= 60 ? 'var(--long)' : pct >= 30 ? 'var(--warning)' : 'var(--short)'
  return (
    <div title={`${pct}% toward TP`} style={{ width: 70 }}>
      <div style={{ height: 4, background: 'var(--bg-elevated)', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: barColor, borderRadius: 2 }} />
      </div>
      <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>{pct}%→TP</span>
    </div>
  )
}

// ── Open Positions Table ──────────────────────────────────────────────────────

function OpenPositionsTable({ positions, onClose, closing }) {
  if (positions.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">📭</div>
        <div className="empty-state-title">No open paper trades</div>
        <div className="empty-state-desc">
          Analyze a coin in the Coin Analyzer tab, then click
          "Open Paper Trade" on an actionable signal.
        </div>
      </div>
    )
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Dir</th>
            <th>Entry</th>
            <th>Current</th>
            <th>Stop Loss</th>
            <th>Take Profit</th>
            <th>Unrealized P&amp;L</th>
            <th>Progress</th>
            <th>Conf</th>
            <th>Opened</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {positions.map(t => (
            <tr key={t.id}>
              <td>
                <span className="font-mono" style={{ fontWeight: 700, fontSize: 13 }}>{t.symbol}</span>
              </td>
              <td><DirectionBadge direction={t.direction} /></td>
              <td>
                <span className="font-mono" style={{ fontSize: 12 }}>{fmtPrice(t.entry_price)}</span>
              </td>
              <td>
                <span className="font-mono" style={{ fontSize: 12, color: 'var(--text-primary)', fontWeight: 600 }}>
                  {fmtPrice(t.current_price)}
                </span>
              </td>
              <td>
                <span className="font-mono text-red" style={{ fontSize: 12 }}>{fmtPrice(t.stop_price)}</span>
              </td>
              <td>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  {t.targets && t.targets.length > 0
                    ? t.targets.slice(0, 3).map((tp, i) => (
                        <span key={i} className={`font-mono ${t.direction === 'long' ? 'text-green' : 'text-red'}`}
                          style={{ fontSize: 11 }}>
                          TP{i + 1}: {fmtPrice(tp)}
                        </span>
                      ))
                    : <span className="text-muted" style={{ fontSize: 12 }}>—</span>
                  }
                </div>
              </td>
              <td>
                <PnlCell unrealized_pnl={t.unrealized_pnl} unrealized_pct={t.unrealized_pct} />
              </td>
              <td>
                <DistanceBar
                  entry={t.entry_price}
                  current={t.current_price}
                  stop={t.stop_price}
                  tp1={t.targets?.[0]}
                  direction={t.direction}
                />
              </td>
              <td>
                <span className="font-mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {t.confidence ? Math.round(t.confidence * 100) + '%' : '—'}
                </span>
              </td>
              <td>
                <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                  {t.opened_at ? new Date(t.opened_at).toLocaleTimeString() : '—'}
                </span>
              </td>
              <td>
                <button
                  className="btn btn-danger btn-sm"
                  onClick={() => onClose(t.id)}
                  disabled={closing === t.id}
                >
                  {closing === t.id
                    ? <span className="spinner" style={{ width: 12, height: 12 }} />
                    : '✕ Close'
                  }
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Closed Trades History Table ───────────────────────────────────────────────

function HistoryTable({ trades }) {
  const [expandedId, setExpandedId] = useState(null)

  if (trades.length === 0) {
    return (
      <div className="empty-state" style={{ padding: '32px 24px' }}>
        <div className="empty-state-icon">📂</div>
        <div className="empty-state-title">No closed trades yet</div>
        <div className="empty-state-desc">
          Closed paper trades appear here and are written to the journal
          so the bot can learn from them.
        </div>
      </div>
    )
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Dir</th>
            <th>Entry</th>
            <th>Exit</th>
            <th>Realized P&amp;L</th>
            <th>R Multiple</th>
            <th>Outcome</th>
            <th>Exit Reason</th>
            <th>Closed</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {trades.map(t => (
            <>
              <tr
                key={t.id}
                onClick={() => setExpandedId(expandedId === t.id ? null : t.id)}
                style={{ cursor: 'pointer' }}
              >
                <td>
                  <span className="font-mono" style={{ fontWeight: 700, fontSize: 13 }}>{t.symbol}</span>
                </td>
                <td><DirectionBadge direction={t.direction} /></td>
                <td>
                  <span className="font-mono" style={{ fontSize: 12 }}>{fmtPrice(t.entry_price)}</span>
                </td>
                <td>
                  <span className="font-mono" style={{ fontSize: 12 }}>{fmtPrice(t.exit_price)}</span>
                </td>
                <td>
                  <span className={`font-mono ${pnlClass(t.realized_pnl)}`} style={{ fontWeight: 700, fontSize: 13 }}>
                    {t.realized_pnl != null
                      ? (t.realized_pnl >= 0 ? '+' : '') + fmt(t.realized_pnl, 2)
                      : '—'}
                  </span>
                </td>
                <td>
                  <span className={`font-mono ${pnlClass(t.realized_r)}`} style={{ fontSize: 13 }}>
                    {t.realized_r != null ? (t.realized_r >= 0 ? '+' : '') + t.realized_r.toFixed(2) + 'R' : '—'}
                  </span>
                </td>
                <td><OutcomeBadge outcome={t.outcome} /></td>
                <td><ExitReasonLabel reason={t.exit_reason} /></td>
                <td>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                    {t.closed_at ? new Date(t.closed_at).toLocaleString() : '—'}
                  </span>
                </td>
                <td>
                  <button className="btn btn-secondary btn-sm">
                    {expandedId === t.id ? '▲' : '▼'}
                  </button>
                </td>
              </tr>
              {expandedId === t.id && (
                <tr key={`${t.id}-detail`}>
                  <td colSpan={10} style={{ padding: '0 16px 16px', background: 'var(--bg-primary)' }}>
                    <div style={{
                      display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
                      gap: 10, paddingTop: 12,
                    }}>
                      {[
                        { label: 'Confidence', value: t.confidence ? Math.round(t.confidence * 100) + '%' : '—' },
                        { label: 'Regime',     value: t.regime?.replace(/_/g, ' ') ?? '—' },
                        { label: 'Stop Price', value: fmtPrice(t.stop_price) },
                        { label: 'Risk Amount', value: t.risk_amount ? '$' + fmt(t.risk_amount) : '—' },
                        { label: 'Position Size', value: t.position_size ? (t.position_size * 100).toFixed(2) + '%' : '—' },
                        { label: 'Strategies', value: (t.strategies_used || []).join(', ') || '—' },
                      ].map(item => (
                        <div key={item.label} className="detail-item">
                          <div className="detail-item-label">{item.label}</div>
                          <div className="detail-item-value" style={{ fontSize: 12, textTransform: 'capitalize' }}>
                            {item.value}
                          </div>
                        </div>
                      ))}
                    </div>
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Summary Stats ─────────────────────────────────────────────────────────────

function SummaryStats({ summary, openCount }) {
  const wr = summary.win_rate != null ? Math.round(summary.win_rate * 100) : null
  const wrColor = wr != null ? (wr >= 60 ? 'green' : wr >= 40 ? 'blue' : 'red') : ''
  return (
    <div className="stat-grid">
      <div className="stat-tile">
        <span className="stat-label">Open Positions</span>
        <span className="stat-value blue">{openCount}</span>
      </div>
      <div className="stat-tile">
        <span className="stat-label">Total Closed</span>
        <span className="stat-value">{summary.total ?? 0}</span>
      </div>
      <div className="stat-tile">
        <span className="stat-label">Wins</span>
        <span className="stat-value green">{summary.wins ?? 0}</span>
      </div>
      <div className="stat-tile">
        <span className="stat-label">Losses</span>
        <span className="stat-value red">{summary.losses ?? 0}</span>
      </div>
      <div className="stat-tile">
        <span className="stat-label">Win Rate</span>
        <span className={`stat-value ${wrColor}`}>{wr != null ? wr + '%' : '—'}</span>
      </div>
      <div className="stat-tile">
        <span className="stat-label">Total P&amp;L</span>
        <span className={`stat-value ${pnlClass(summary.total_pnl)}`}>
          {summary.total_pnl != null
            ? (summary.total_pnl >= 0 ? '+$' : '-$') + Math.abs(summary.total_pnl).toFixed(2)
            : '—'}
        </span>
      </div>
      <div className="stat-tile">
        <span className="stat-label">Avg R Multiple</span>
        <span className={`stat-value ${pnlClass(summary.avg_r)}`}>
          {summary.avg_r != null ? (summary.avg_r >= 0 ? '+' : '') + summary.avg_r.toFixed(2) + 'R' : '—'}
        </span>
      </div>
    </div>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function PaperTrading() {
  const [positions, setPositions]   = useState([])
  const [history, setHistory]       = useState([])
  const [summary, setSummary]       = useState({})
  const [loading, setLoading]       = useState(false)
  const [closing, setClosing]       = useState(null)
  const [lastRefresh, setLastRefresh] = useState(null)
  const [activeTab, setActiveTab]   = useState('open') // 'open' | 'history'
  const timerRef = useRef(null)

  const fetchAll = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    try {
      const [posRes, histRes, sumRes] = await Promise.all([
        fetch(`${API}/api/paper-trade/positions`),
        fetch(`${API}/api/paper-trade/history?limit=200`),
        fetch(`${API}/api/paper-trade/summary`),
      ])
      const [posData, histData, sumData] = await Promise.all([
        posRes.json(),
        histRes.json(),
        sumRes.json(),
      ])
      setPositions(posData.positions ?? [])
      setHistory(histData.trades ?? [])
      setSummary(sumData)
      setLastRefresh(new Date())
    } catch (_) {
      // silently handle — the user sees stale data instead of error
    } finally {
      setLoading(false)
    }
  }, [])

  // Initial load + 30s auto-refresh to pick up backend SL/TP auto-closes
  useEffect(() => {
    fetchAll()
    timerRef.current = setInterval(() => fetchAll(true), 30000)
    return () => clearInterval(timerRef.current)
  }, [fetchAll])

  const handleClose = async (tradeId) => {
    setClosing(tradeId)
    try {
      const res = await fetch(`${API}/api/paper-trade/close/${tradeId}`, { method: 'DELETE' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        alert(`Close failed: ${body.detail ?? 'Unknown error'}`)
        return
      }
      await fetchAll(true)
    } catch (err) {
      alert(`Close error: ${err.message}`)
    } finally {
      setClosing(null)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* ── Header card ── */}
      <div className="card">
        <div className="card-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span className="card-title">Paper Trading</span>
            <span className="badge badge-warning">SIMULATED — NO REAL MONEY</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {lastRefresh && (
              <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                Refreshed {lastRefresh.toLocaleTimeString()}
              </span>
            )}
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => fetchAll()}
              disabled={loading}
            >
              {loading
                ? <><span className="spinner" style={{ width: 12, height: 12 }} /> Loading…</>
                : '↻ Refresh'
              }
            </button>
          </div>
        </div>

        {/* Explainer */}
        <div style={{
          padding: '12px 20px',
          background: 'var(--accent-dim)',
          borderBottom: '1px solid var(--border)',
          fontSize: 12,
          color: 'var(--text-dim)',
          lineHeight: 1.6,
        }}>
          <strong style={{ color: 'var(--accent)' }}>How it works:</strong> When the bot gives a LONG or SHORT signal,
          click <em>"Open Paper Trade"</em> on the signal card. The position is tracked here with live prices.
          Stop-loss and take-profit auto-close in the background every 30 seconds.
          Closed trades are written to the journal so the bot can learn from them.
        </div>

        {/* Summary stats */}
        <div className="card-body">
          <SummaryStats summary={summary} openCount={positions.length} />
        </div>
      </div>

      {/* ── Sub-tabs ── */}
      <div style={{ display: 'flex', gap: 2, borderBottom: '1px solid var(--border)' }}>
        {[
          { id: 'open',    label: `Open Positions (${positions.length})` },
          { id: 'history', label: `Closed Trades (${history.length})` },
        ].map(t => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            style={{
              padding: '8px 18px',
              background: 'transparent',
              border: 'none',
              borderBottom: activeTab === t.id ? '2px solid var(--accent)' : '2px solid transparent',
              color: activeTab === t.id ? 'var(--accent)' : 'var(--text-muted)',
              fontWeight: activeTab === t.id ? 700 : 400,
              fontSize: 13,
              cursor: 'pointer',
              fontFamily: 'var(--font-sans)',
              marginBottom: -1,
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Open Positions ── */}
      {activeTab === 'open' && (
        <div className="card fade-in">
          <div className="card-header">
            <span className="card-title">Live Open Positions</span>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Prices + SL/TP auto-check every 30s
            </span>
          </div>
          <OpenPositionsTable
            positions={positions}
            onClose={handleClose}
            closing={closing}
          />
        </div>
      )}

      {/* ── Closed Trades History ── */}
      {activeTab === 'history' && (
        <div className="card fade-in">
          <div className="card-header">
            <span className="card-title">Closed Trades</span>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Written to journal for bot self-learning
            </span>
          </div>
          <HistoryTable trades={history} />
        </div>
      )}
    </div>
  )
}
