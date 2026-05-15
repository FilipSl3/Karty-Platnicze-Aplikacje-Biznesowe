import { useState, useEffect } from 'react'
import { cardAPI } from '../api.js'

const NEXT_STATUS = {
  REQUESTED: 'PRODUCING',
  PRODUCING: 'SHIPPED',
}

const STATUS_COLORS = {
  REQUESTED: '#2563eb',
  PRODUCING: '#7c3aed',
  SHIPPED:   '#059669',
  ACTIVE:    '#16a34a',
  BLOCKED:   '#dc2626',
}

export default function CardDetail({ token, onBack, onRefresh }) {
  const [card, setCard] = useState(null)
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState(false)
  const [message, setMessage] = useState(null)
  const [topupAmount, setTopupAmount] = useState('')

  const fetchCard = async () => {
    try {
      setLoading(true)
      const res = await cardAPI.get(token)
      setCard(res.data)
    } catch {
      setMessage({ type: 'error', text: 'Nie można pobrać danych karty' })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchCard() }, [token])

  const handleAction = async (action, ...args) => {
    setActionLoading(true)
    setMessage(null)
    try {
      await action(...args)
      await fetchCard()
      onRefresh()
      setMessage({ type: 'success', text: 'Operacja wykonana pomyślnie' })
    } catch (e) {
      setMessage({
        type: 'error',
        text: e.response?.data?.detail || 'Błąd operacji'
      })
    } finally {
      setActionLoading(false)
    }
  }

  if (loading) return <div style={styles.loading}>Ładowanie...</div>
  if (!card) return <div style={styles.loading}>Brak danych</div>

  const statusColor = STATUS_COLORS[card.status] || '#64748b'
  const nextStatus = NEXT_STATUS[card.status]

  return (
    <div>
      <button onClick={onBack} style={styles.backBtn}>← Wróć</button>

      {message && (
        <div style={{
          ...styles.message,
          background: message.type === 'success' ? '#dcfce7' : '#fee2e2',
          color: message.type === 'success' ? '#16a34a' : '#dc2626',
        }}>
          {message.text}
        </div>
      )}

      {/* Status badge */}
      <div style={styles.statusRow}>
        <span style={{ ...styles.statusBadge, background: statusColor }}>
          {card.status}
        </span>
        <span style={styles.cardType}>{card.card_type}</span>
      </div>

      {/* Dane karty */}
      <div style={styles.panDisplay}>{card.masked_pan}</div>

      <div style={styles.table}>
        <Row label="Bank ID" value={card.bank_id || '—'} />
        <Row label="Saldo" value={`${card.balance?.toFixed(2)} PLN`} />
        <Row label="Limit dzienny" value={`${card.daily_limit?.toFixed(2)} PLN`} />
        <Row label="Token" value={card.card_token} mono small />
      </div>

        {/* Cykl życia */}
        <div style={styles.section}>
          <h3 style={styles.sectionTitle}>Cykl życia</h3>
          <div style={styles.lifecycle}>
            {['REQUESTED', 'PRODUCING', 'SHIPPED', 'ACTIVE'].map((s, i, arr) => (
              <div key={s} style={styles.lifecycleStep}>
                {i > 0 && (
                  <div style={{
                    ...styles.lifecycleLine,
                    background: isStepDone(card.status, s) ? '#6366f1' : '#e2e8f0'
                  }} />
                )}
                <div style={{
                  ...styles.lifecycleDot,
                  background: isStepDone(card.status, s) ? '#6366f1' : '#e2e8f0',
                  border: card.status === s ? '2px solid #4f46e5' : 'none'
                }} />
                <div style={styles.lifecycleLabel}>{s}</div>
              </div>
            ))}
          </div>
        </div>

      {/* Akcje */}
      <div style={styles.section}>
        <h3 style={styles.sectionTitle}>Akcje</h3>
        <div style={styles.actions}>

          {/* Przesuń do następnego etapu */}
          {nextStatus && (
            <ActionButton
              label={`▶ Przesuń do ${nextStatus}`}
              color="#6366f1"
              loading={actionLoading}
              onClick={() => handleAction(
                cardAPI.lifecycle, token, nextStatus
              )}
            />
          )}

          {/* Aktywuj (gdy SHIPPED) */}
          {card.status === 'SHIPPED' && (
            <ActionButton
              label="✅ Aktywuj kartę"
              color="#22c55e"
              loading={actionLoading}
              onClick={() => handleAction(cardAPI.activate, token)}
            />
          )}

          {/* Blokuj / Odblokuj */}
          {card.status === 'ACTIVE' && (
            <ActionButton
              label="🔒 Zablokuj kartę"
              color="#ef4444"
              loading={actionLoading}
              onClick={() => handleAction(
                cardAPI.block, token, 'Admin panel action'
              )}
            />
          )}
          {card.status === 'BLOCKED' && (
            <ActionButton
              label="🔓 Odblokuj kartę"
              color="#22c55e"
              loading={actionLoading}
              onClick={() => handleAction(cardAPI.unblock, token)}
            />
          )}

          {/* Doładowanie prepaid */}
          {card.card_type === 'PREPAID' && card.status === 'ACTIVE' && (
            <div style={styles.topup}>
              <input
                style={styles.topupInput}
                type="number"
                placeholder="Kwota doładowania (PLN)"
                value={topupAmount}
                onChange={e => setTopupAmount(e.target.value)}
                min="1"
              />
              <ActionButton
                label="💰 Doładuj"
                color="#f59e0b"
                loading={actionLoading}
                onClick={() => {
                  if (!topupAmount || topupAmount <= 0) return
                  handleAction(cardAPI.topup, token, parseFloat(topupAmount))
                  setTopupAmount('')
                }}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function Row({ label, value, mono, small }) {
  return (
    <div style={styles.tableRow}>
      <span style={styles.tableLabel}>{label}</span>
      <span style={{ ...styles.tableValue, fontFamily: mono ? 'monospace' : 'inherit', fontSize: small ? '10px' : '13px', wordBreak: 'break-all', }}>
        {value}
      </span>
    </div>
  )
}

function ActionButton({ label, color, loading, onClick }) {
  return (
    <button
      onClick={onClick}
      disabled={loading}
      style={{
        ...styles.actionBtn,
        background: loading ? '#e2e8f0' : color,
        cursor: loading ? 'not-allowed' : 'pointer',
      }}
    >
      {loading ? '...' : label}
    </button>
  )
}

function isStepDone(currentStatus, step) {
  const order = ['REQUESTED', 'PRODUCING', 'SHIPPED', 'ACTIVE']
  return order.indexOf(currentStatus) >= order.indexOf(step)
}

const styles = {
  loading: { textAlign: 'center', color: '#64748b', padding: '40px' },
  backBtn: {
    background: 'none', border: 'none', color: '#6366f1',
    cursor: 'pointer', fontSize: '13px', marginBottom: '16px',
    fontFamily: 'Inter, sans-serif', padding: 0
  },
  message: {
    padding: '10px 14px', borderRadius: '8px',
    fontSize: '13px', marginBottom: '12px'
  },
  statusRow: {
    display: 'flex', alignItems: 'center',
    gap: '8px', marginBottom: '8px'
  },
  statusBadge: {
    color: 'white', padding: '3px 12px', borderRadius: '20px',
    fontSize: '12px', fontWeight: '700'
  },
  cardType: { fontSize: '12px', color: '#64748b' },
  panDisplay: {
    fontSize: '20px', fontWeight: '700', letterSpacing: '2px',
    color: '#1e293b', marginBottom: '16px', fontFamily: 'monospace'
  },
  table: {
    background: '#f8fafc', borderRadius: '10px',
    padding: '4px 0', marginBottom: '16px'
  },
  tableRow: {
    display: 'flex', justifyContent: 'space-between',
    padding: '8px 14px', borderBottom: '1px solid #f1f5f9'
  },
  tableLabel: { fontSize: '12px', color: '#64748b' },
  tableValue: { fontSize: '13px', fontWeight: '500', color: '#1e293b' },
  section: { marginBottom: '16px' },
  sectionTitle: {
    fontSize: '13px', fontWeight: '600', color: '#374151',
    marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '0.5px'
  },
    lifecycle: {
      display: 'flex',
      alignItems: 'flex-start',
      marginBottom: '8px',
      paddingTop: '8px',
    },
    lifecycleStep: {
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      flex: 1,
      position: 'relative',
    },
    lifecycleDot: {
      width: '14px',
      height: '14px',
      borderRadius: '50%',
      zIndex: 1,
      flexShrink: 0,
    },
    lifecycleLabel: {
      fontSize: '9px',
      color: '#64748b',
      marginTop: '6px',
      textAlign: 'center',
      wordBreak: 'break-word',
      maxWidth: '55px',
      lineHeight: '1.3',
    },
    lifecycleLine: {
      position: 'absolute',
      top: '7px',
      right: '50%',
      width: '100%',
      height: '2px',
      zIndex: 0,
    },
  actions: { display: 'flex', flexDirection: 'column', gap: '8px' },
  actionBtn: {
    padding: '10px 16px', color: 'white', border: 'none',
    borderRadius: '8px', fontSize: '13px', fontWeight: '600',
    fontFamily: 'Inter, sans-serif', transition: 'opacity 0.15s',
    textAlign: 'left'
  },
  topup: { display: 'flex', gap: '8px' },
  topupInput: {
    flex: 1, padding: '10px 12px', border: '1.5px solid #e2e8f0',
    borderRadius: '8px', fontSize: '13px', fontFamily: 'Inter, sans-serif'
  },
}