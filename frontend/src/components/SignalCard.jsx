/**
 * SignalCard — renders a full signal analysis result card.
 * Props:
 *   signal: the API response object from POST /api/analyze
 *   compact: boolean — show condensed version (used in scanner rows)
 *   onPaperTradeOpened: optional callback(trade) called after a paper trade is created
 */
import { useState } from 'react'

const API = 'http://localhost:8000'

function fmt(n, decimals = 2) {
  if (n == null) return '—'
  return Number(n).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

function ConfBar({ value }) {
  const pct = Math.round((value ?? 0) * 100)
  const cls = pct >= 70 ? 'conf-high' : pct >= 45 ? 'conf-medium' : 'conf-low'
  return (
    <div className="confidence-bar-wrap">
      <div className="confidence-bar-track">
        <div className={`confidence-bar-fill ${cls}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="confidence-pct">{pct}%</span>
    </div>
  )
}

function DirectionBadge({ direction }) {
  const label = direction === 'long' ? '▲ LONG' : direction === 'short' ? '▼ SHORT' : '— NO TRADE'
  return (
    <span className={`signal-direction-badge ${direction ?? 'no_trade'}`}>
      {label}
    </span>
  )
}

function PriceSection({ signal }) {
  const isShort = signal.direction === 'short'
  return (
    <div>
      <div className="section-label">Price Levels</div>
      <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
        <div className="price-row" style={{ padding: '8px 14px' }}>
          <span className="price-label">Entry</span>
          <span className="price-value">${fmt(signal.entry_price, signal.entry_price > 100 ? 2 : 4)}</span>
        </div>
        <div className="price-row" style={{ padding: '8px 14px' }}>
          <span className="price-label">Stop Loss</span>
          <span className="price-value text-red">${fmt(signal.stop_price, signal.stop_price > 100 ? 2 : 4)}</span>
        </div>
        {signal.targets && signal.targets.length > 0 && (
          <div className="price-row" style={{ padding: '8px 14px' }}>
            <span className="price-label">Targets</span>
            <div className="target-list" style={{ justifyContent: 'flex-end' }}>
              {signal.targets.map((t, i) => (
                <span key={i} className={`target-tag${isShort ? ' short-target' : ''}`}>
                  TP{i + 1}: ${fmt(t, t > 100 ? 2 : 4)}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function RiskSection({ signal }) {
  return (
    <div className="stat-grid">
      <div className="stat-tile">
        <span className="stat-label">Risk / Reward</span>
        <span className={`stat-value ${signal.risk_reward >= 2 ? 'green' : signal.risk_reward >= 1 ? 'blue' : 'red'}`}>
          {signal.risk_reward != null ? signal.risk_reward.toFixed(1) + 'R' : '—'}
        </span>
      </div>
      <div className="stat-tile">
        <span className="stat-label">Position Size</span>
        <span className="stat-value blue">
          {signal.position_size != null ? (signal.position_size * 100).toFixed(2) + '%' : '—'}
        </span>
      </div>
      <div className="stat-tile">
        <span className="stat-label">Risk Amount</span>
        <span className="stat-value">
          {signal.risk_amount != null ? '$' + fmt(signal.risk_amount) : '—'}
        </span>
      </div>
    </div>
  )
}

function MarketContext({ signal }) {
  return (
    <div className="detail-grid">
      {[
        { label: 'Regime',    value: signal.regime },
        { label: 'Trend',     value: signal.trend },
        { label: 'Phase',     value: signal.phase },
      ].map(item => (
        item.value && (
          <div key={item.label} className="detail-item">
            <div className="detail-item-label">{item.label}</div>
            <div className="detail-item-value" style={{ textTransform: 'capitalize' }}>
              {item.value.replace(/_/g, ' ')}
            </div>
          </div>
        )
      ))}
    </div>
  )
}

function StrategyDetails({ details }) {
  if (!details || details.length === 0) return null
  return (
    <div>
      <div className="section-label">Strategy Details</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {details.map(s => (
          <div
            key={s.id}
            style={{
              background: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)',
              padding: '10px 14px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
              <span className={`strategy-chip ${s.valid ? 'valid' : 'invalid'}`}>
                {s.valid ? '✓' : '✗'} {s.id.replace(/_/g, ' ')}
              </span>
              {s.confidence != null && (
                <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                  {Math.round(s.confidence * 100)}%
                </span>
              )}
            </div>
            {s.evidence && s.evidence.length > 0 && (
              <ul style={{ paddingLeft: 16, margin: 0 }}>
                {s.evidence.map((e, i) => (
                  <li key={i} style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>{e}</li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function PaperTradeButton({ signal, onPaperTradeOpened }) {
  const [loading, setLoading]   = useState(false)
  const [status, setStatus]     = useState(null) // 'ok' | 'err' | null
  const [message, setMessage]   = useState('')

  const openTrade = async () => {
    setLoading(true)
    setStatus(null)
    try {
      const res = await fetch(`${API}/api/paper-trade/open`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol:          signal.symbol,
          direction:       signal.direction,
          entry_price:     signal.entry_price,
          stop_price:      signal.stop_price,
          targets:         signal.targets || [],
          position_size:   signal.position_size || 0,
          risk_amount:     signal.risk_amount || 0,
          confidence:      signal.confidence || 0,
          regime:          signal.regime,
          strategies_used: signal.strategies_used || [],
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setStatus('ok')
      setMessage(`Paper trade opened @ $${data.trade?.entry_price?.toFixed(4) ?? '?'}`)
      if (onPaperTradeOpened) onPaperTradeOpened(data.trade)
      // Clear success message after 4s
      setTimeout(() => setStatus(null), 4000)
    } catch (err) {
      setStatus('err')
      setMessage(err.message || 'Failed to open paper trade')
      setTimeout(() => setStatus(null), 5000)
    } finally {
      setLoading(false)
    }
  }

  const dirColor = signal.direction === 'long' ? 'var(--long)' : 'var(--short)'

  return (
    <div>
      <button
        className="btn btn-sm"
        style={{
          background: signal.direction === 'long' ? 'var(--long-dim)' : 'var(--short-dim)',
          color: dirColor,
          border: `1px solid ${dirColor}40`,
          width: '100%',
          justifyContent: 'center',
        }}
        onClick={openTrade}
        disabled={loading}
      >
        {loading
          ? <><span className="spinner" style={{ width: 12, height: 12 }} /> Opening…</>
          : `📄 Open Paper Trade (${signal.direction?.toUpperCase()})`
        }
      </button>
      {status === 'ok' && (
        <div className="success-box fade-in" style={{ marginTop: 8, fontSize: 12, padding: '8px 12px' }}>
          ✓ {message} — check the Paper Trading tab
        </div>
      )}
      {status === 'err' && (
        <div className="error-box fade-in" style={{ marginTop: 8, fontSize: 12, padding: '8px 12px' }}>
          ⚠ {message}
        </div>
      )}
    </div>
  )
}

export default function SignalCard({ signal, compact = false, onPaperTradeOpened }) {
  const [expanded, setExpanded] = useState(false)

  if (!signal) return null

  const dir = signal.direction ?? 'no_trade'
  const cardClass = dir === 'long' ? 'long-card' : dir === 'short' ? 'short-card' : 'no-trade-card'

  if (compact) {
    return (
      <div className={`signal-card-outer ${cardClass} fade-in`} style={{ background: 'var(--bg-card)' }}>
        <div style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
          <DirectionBadge direction={dir} />
          <ConfBar value={signal.confidence} />
          {signal.risk_reward != null && (
            <span className="font-mono" style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
              RR {signal.risk_reward.toFixed(1)}R
            </span>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className={`signal-card-outer ${cardClass} fade-in`}>
      {/* ─── Header ─── */}
      <div className="signal-card-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span className="signal-symbol">{signal.symbol}</span>
          {signal.cached && <span className="cached-tag">CACHED</span>}
        </div>
        <DirectionBadge direction={dir} />
      </div>

      {/* ─── Body ─── */}
      <div className="signal-card-body">
        {/* Confidence */}
        <div>
          <div className="section-label">Confidence</div>
          <ConfBar value={signal.confidence} />
        </div>

        {/* No-trade reason */}
        {signal.no_trade_reason && (
          <div className="error-box" style={{ background: 'var(--no-trade-dim)', borderColor: 'rgba(84,110,122,0.3)', color: 'var(--no-trade)' }}>
            <span>⚠</span>
            <span>{signal.no_trade_reason}</span>
          </div>
        )}

        {/* Market context */}
        <MarketContext signal={signal} />

        {/* Price levels — only if actionable */}
        {signal.is_actionable && <PriceSection signal={signal} />}

        {/* Risk metrics — only if actionable */}
        {signal.is_actionable && <RiskSection signal={signal} />}

        {/* Strategies used */}
        {signal.strategies_used && signal.strategies_used.length > 0 && (
          <div>
            <div className="section-label">Strategies</div>
            <div className="strategy-list">
              {signal.strategies_used.map(s => (
                <span key={s} className="strategy-chip valid">{s.replace(/_/g, ' ')}</span>
              ))}
            </div>
          </div>
        )}

        {/* AI Explanation */}
        {signal.ai_explanation && (
          <div className="ai-box">
            <div className="ai-box-header">AI Analysis</div>
            {signal.ai_explanation}
          </div>
        )}

        {/* Expand / collapse strategy details */}
        {signal.strategy_details && signal.strategy_details.length > 0 && (
          <div>
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => setExpanded(x => !x)}
              style={{ width: '100%', justifyContent: 'center' }}
            >
              {expanded ? '▲ Hide' : '▼ Show'} Strategy Details
            </button>
            {expanded && <div style={{ marginTop: 10 }}><StrategyDetails details={signal.strategy_details} /></div>}
          </div>
        )}

        {/* Open Paper Trade — only for actionable signals */}
        {signal.is_actionable && (signal.direction === 'long' || signal.direction === 'short') && (
          <PaperTradeButton signal={signal} onPaperTradeOpened={onPaperTradeOpened} />
        )}

        {/* Footer timestamp */}
        <div style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
          Analyzed: {signal.analyzed_at ? new Date(signal.analyzed_at).toLocaleString() : '—'}
        </div>
      </div>
    </div>
  )
}
