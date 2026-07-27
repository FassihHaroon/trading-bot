/**
 * PaperTrading — simulated limit-order tracker.
 *
 * Trade lifecycle:
 *   PENDING  — order placed, waiting for price to reach entry
 *   OPEN     — entry triggered, tracking live P&L
 *   CLOSED   — SL / TP hit or manually closed
 *
 * NO real money, NO real orders.
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
  if (direction === 'long')  return <span className="badge badge-long">▲ LONG</span>
  if (direction === 'short') return <span className="badge badge-short">▼ SHORT</span>
  return null
}

function OutcomeBadge({ outcome }) {
  if (outcome === 'win')        return <span className="badge badge-long">WIN</span>
  if (outcome === 'loss')       return <span className="badge badge-short">LOSS</span>
  if (outcome === 'breakeven')  return <span className="badge badge-no-trade">B/E</span>
  if (outcome === 'cancelled')  return <span className="badge badge-no-trade">CANCELLED</span>
  return <span className="badge badge-no-trade">{outcome ?? '—'}</span>
}

function ExitReasonLabel({ reason }) {
  const labels = {
    stop_loss:      '⛔ Stop Loss',
    take_profit_1:  '✅ Take Profit',
    manual_close:   '🖐 Manual Close',
    cancelled:      '✕ Cancelled',
  }
  return <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{labels[reason] ?? reason ?? '—'}</span>
}

// ── Pending Orders Table ──────────────────────────────────────────────────────

function PendingOrdersTable({ orders, onCancel, cancelling }) {
  if (orders.length === 0) {
    return (
      <div className="empty-state" style={{ padding: '28px 24px' }}>
        <div className="empty-state-icon" style={{ fontSize: 24, opacity: 0.4 }}>⏳</div>
        <div className="empty-state-title" style={{ fontSize: 13 }}>No pending orders</div>
        <div className="empty-state-desc">
          Pending orders appear here after you click "Open Paper Trade" and are waiting
          for price to reach the bot's entry level.
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
            <th>Entry (Limit)</th>
            <th>Current Price</th>
            <th>Distance to Entry</th>
            <th>Stop Loss</th>
            <th>Take Profit</th>
            <th>Conf</th>
            <th>Placed At</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {orders.map(t => {
            const dist = t.entry_distance  // positive = current above entry
            const needsToMove = t.direction === 'long'
              ? `Price needs to DROP ${Math.abs(dist).toFixed(2)}% to fill`
              : `Price needs to RISE ${Math.abs(dist).toFixed(2)}% to fill`

            return (
              <tr key={t.id}>
                <td>
                  <span className="font-mono" style={{ fontWeight: 700, fontSize: 13 }}>{t.symbol}</span>
                </td>
                <td><DirectionBadge direction={t.direction} /></td>
                <td>
                  <span className="font-mono" style={{ fontSize: 13, fontWeight: 700, color: 'var(--accent)' }}>
                    {fmtPrice(t.entry_price)}
                  </span>
                </td>
                <td>
                  <span className="font-mono" style={{ fontSize: 12 }}>{fmtPrice(t.current_price)}</span>
                </td>
                <td>
                  <div>
                    <span
                      className="font-mono"
                      style={{
                        fontSize: 12,
                        color: Math.abs(dist) < 0.5 ? 'var(--warning)' : 'var(--text-muted)',
                        fontWeight: Math.abs(dist) < 0.5 ? 700 : 400,
                      }}
                    >
                      {dist != null ? (dist >= 0 ? '+' : '') + dist.toFixed(2) + '%' : '—'}
                    </span>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>
                      {dist != null ? needsToMove : ''}
                    </div>
                  </div>
                </td>
                <td>
                  <span className="font-mono text-red" style={{ fontSize: 12 }}>{fmtPrice(t.stop_price)}</span>
                </td>
                <td>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {t.targets && t.targets.length > 0
                      ? t.targets.slice(0, 2).map((tp, i) => (
                          <span key={i}
                            className={`font-mono ${t.direction === 'long' ? 'text-green' : 'text-red'}`}
                            style={{ fontSize: 11 }}>
                            TP{i + 1}: {fmtPrice(tp)}
                          </span>
                        ))
                      : <span className="text-muted" style={{ fontSize: 12 }}>—</span>
                    }
                  </div>
                </td>
                <td>
                  <span className="font-mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    {t.confidence ? Math.round(t.confidence * 100) + '%' : '—'}
                  </span>
                </td>
                <td>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                    {t.placed_at ? new Date(t.placed_at).toLocaleTimeString() : '—'}
                  </span>
                </td>
                <td>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => onCancel(t.id)}
                    disabled={cancelling === t.id}
                    title="Cancel this pending order"
                  >
                    {cancelling === t.id
                      ? <span className="spinner" style={{ width: 12, height: 12 }} />
                      : '✕ Cancel'
                    }
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Open Positions Table ──────────────────────────────────────────────────────

function OpenPositionsTable({ positions, onClose, closing }) {
  if (positions.length === 0) {
    return (
      <div className="empty-state" style={{ padding: '28px 24px' }}>
        <div className="empty-state-icon" style={{ fontSize: 24, opacity: 0.4 }}>📭</div>
        <div className="empty-state-title" style={{ fontSize: 13 }}>No active positions</div>
        <div className="empty-state-desc">
          Positions appear here once a pending order's entry price is triggered.
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
            <th>Entry (Filled)</th>
            <th>Current Price</th>
            <th>Stop Loss</th>
            <th>Take Profit</th>
            <th>Unrealized P&amp;L</th>
            <th>Conf</th>
            <th>Filled At</th>
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
                <span className="font-mono" style={{ fontSize: 12, color: 'var(--text-primary)', fontWeight: 600 }}>
                  {fmtPrice(t.entry_price)}
                </span>
              </td>
              <td>
                <span className="font-mono" style={{ fontSize: 13, fontWeight: 700 }}>
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
                        <span key={i}
                          className={`font-mono ${t.direction === 'long' ? 'text-green' : 'text-red'}`}
                          style={{ fontSize: 11 }}>
                          TP{i + 1}: {fmtPrice(tp)}
                        </span>
                      ))
                    : <span className="text-muted" style={{ fontSize: 12 }}>—</span>
                  }
                </div>
              </td>
              <td>
                <div>
                  <span
                    className={`font-mono ${pnlClass(t.unrealized_pnl)}`}
                    style={{ fontWeight: 700, fontSize: 13 }}
                  >
                    {t.unrealized_pnl != null
                      ? (t.unrealized_pnl >= 0 ? '+' : '') + fmt(t.unrealized_pnl, 2)
                      : '—'}
                  </span>
                  {t.unrealized_pct != null && (
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 4 }}>
                      ({t.unrealized_pct >= 0 ? '+' : ''}{fmt(t.unrealized_pct, 2)}%)
                    </span>
                  )}
                </div>
              </td>
              <td>
                <span className="font-mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {t.confidence ? Math.round(t.confidence * 100) + '%' : '—'}
                </span>
              </td>
              <td>
                <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                  {t.filled_at ? new Date(t.filled_at).toLocaleTimeString() : '—'}
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
  const realTrades = trades.filter(t => t.outcome !== 'cancelled')
  const cancelled  = trades.filter(t => t.outcome === 'cancelled')

  if (trades.length === 0) {
    return (
      <div className="empty-state" style={{ padding: '28px 24px' }}>
        <div className="empty-state-icon" style={{ fontSize: 24, opacity: 0.4 }}>📂</div>
        <div className="empty-state-title" style={{ fontSize: 13 }}>No closed trades yet</div>
        <div className="empty-state-desc">
          Closed trades are written to the journal so the bot can learn from them.
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
                style={{ cursor: 'pointer', opacity: t.outcome === 'cancelled' ? 0.5 : 1 }}
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
                    {t.realized_r != null
                      ? (t.realized_r >= 0 ? '+' : '') + t.realized_r.toFixed(2) + 'R'
                      : '—'}
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
                        { label: 'Confidence',    value: t.confidence ? Math.round(t.confidence * 100) + '%' : '—' },
                        { label: 'Regime',        value: t.regime?.replace(/_/g, ' ') ?? '—' },
                        { label: 'Stop Price',    value: fmtPrice(t.stop_price) },
                        { label: 'Risk Amount',   value: t.risk_amount ? '$' + fmt(t.risk_amount) : '—' },
                        { label: 'Position Size', value: t.position_size ? (t.position_size * 100).toFixed(2) + '%' : '—' },
                        { label: 'Strategies',    value: (t.strategies_used || []).join(', ') || '—' },
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
      {cancelled.length > 0 && (
        <div style={{ padding: '8px 16px', fontSize: 11, color: 'var(--text-muted)', borderTop: '1px solid var(--border)' }}>
          {cancelled.length} cancelled order(s) shown above (greyed out) — not counted in win rate.
        </div>
      )}
    </div>
  )
}

// ── Summary Stats ─────────────────────────────────────────────────────────────

function SummaryStats({ summary }) {
  const wr    = summary.win_rate != null ? Math.round(summary.win_rate * 100) : null
  const wrCls = wr != null ? (wr >= 60 ? 'green' : wr >= 40 ? 'blue' : 'red') : ''
  return (
    <div className="stat-grid">
      <div className="stat-tile">
        <span className="stat-label">⏳ Pending Orders</span>
        <span className="stat-value" style={{ color: 'var(--warning)' }}>{summary.pending_count ?? 0}</span>
      </div>
      <div className="stat-tile">
        <span className="stat-label">Active Positions</span>
        <span className="stat-value blue">{summary.open_count ?? 0}</span>
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
        <span className={`stat-value ${wrCls}`}>{wr != null ? wr + '%' : '—'}</span>
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
  const [allOpen, setAllOpen]       = useState([])
  const [history, setHistory]       = useState([])
  const [summary, setSummary]       = useState({})
  const [loading, setLoading]       = useState(false)
  const [acting, setActing]         = useState(null)  // trade_id being cancelled/closed
  const [lastRefresh, setLastRefresh] = useState(null)
  const [activeTab, setActiveTab]   = useState('pending')
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
      setAllOpen(posData.positions ?? [])
      setHistory(histData.trades ?? [])
      setSummary(sumData)
      setLastRefresh(new Date())
    } catch (_) {}
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    fetchAll()
    timerRef.current = setInterval(() => fetchAll(true), 30000)
    return () => clearInterval(timerRef.current)
  }, [fetchAll])

  const handleAction = async (tradeId) => {
    setActing(tradeId)
    try {
      const res = await fetch(`${API}/api/paper-trade/close/${tradeId}`, { method: 'DELETE' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        alert(`Failed: ${body.detail ?? 'Unknown error'}`)
        return
      }
      await fetchAll(true)
    } catch (err) {
      alert(`Error: ${err.message}`)
    } finally {
      setActing(null)
    }
  }

  const pending   = allOpen.filter(t => t.status === 'pending')
  const active    = allOpen.filter(t => t.status === 'open')

  const tabs = [
    { id: 'pending',  label: `Pending Orders (${pending.length})` },
    { id: 'open',     label: `Active Positions (${active.length})` },
    { id: 'history',  label: `Closed Trades (${history.length})` },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* ── Header ── */}
      <div className="card">
        <div className="card-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span className="card-title">Paper Trading</span>
            <span className="badge badge-warning">SIMULATED — NO REAL MONEY</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {lastRefresh && (
              <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                {lastRefresh.toLocaleTimeString()}
              </span>
            )}
            <button className="btn btn-secondary btn-sm" onClick={() => fetchAll()} disabled={loading}>
              {loading
                ? <><span className="spinner" style={{ width: 12, height: 12 }} /> Loading…</>
                : '↻ Refresh'
              }
            </button>
          </div>
        </div>

        {/* How it works */}
        <div style={{
          padding: '10px 20px',
          background: 'var(--accent-dim)',
          borderBottom: '1px solid var(--border)',
          fontSize: 12,
          color: 'var(--text-dim)',
          lineHeight: 1.6,
        }}>
          <strong style={{ color: 'var(--accent)' }}>Limit order logic:</strong>{' '}
          Orders are <strong>PENDING</strong> until price reaches the bot's entry level.
          A LONG fills when price drops to entry; a SHORT fills when price rises to entry.
          SL / TP auto-close every 30 seconds. Closed trades train the self-learner.
        </div>

        <div className="card-body">
          <SummaryStats summary={summary} />
        </div>
      </div>

      {/* ── Sub-tabs ── */}
      <div style={{ display: 'flex', gap: 2, borderBottom: '1px solid var(--border)' }}>
        {tabs.map(t => (
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

      {/* ── Pending Orders ── */}
      {activeTab === 'pending' && (
        <div className="card fade-in">
          <div className="card-header">
            <span className="card-title">⏳ Pending Limit Orders</span>
            <span style={{ fontSize: 11, color: 'var(--warning)' }}>
              Waiting for price to reach entry — not yet in a trade
            </span>
          </div>
          <PendingOrdersTable
            orders={pending}
            onCancel={handleAction}
            cancelling={acting}
          />
        </div>
      )}

      {/* ── Active Positions ── */}
      {activeTab === 'open' && (
        <div className="card fade-in">
          <div className="card-header">
            <span className="card-title">Active Positions</span>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Entry triggered — live P&amp;L tracking, SL/TP auto-check every 30s
            </span>
          </div>
          <OpenPositionsTable
            positions={active}
            onClose={handleAction}
            closing={acting}
          />
        </div>
      )}

      {/* ── History ── */}
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
