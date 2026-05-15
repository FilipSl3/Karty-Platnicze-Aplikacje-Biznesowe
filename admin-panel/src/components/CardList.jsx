const STATUS_COLORS = {
  REQUESTED: { bg: '#eff6ff', color: '#2563eb' },
  PRODUCING: { bg: '#faf5ff', color: '#7c3aed' },
  SHIPPED:   { bg: '#ecfdf5', color: '#059669' },
  ACTIVE:    { bg: '#f0fdf4', color: '#16a34a' },
  BLOCKED:   { bg: '#fef2f2', color: '#dc2626' },
  EXPIRED:   { bg: '#f8fafc', color: '#64748b' },
}

const TYPE_ICONS = {
  VIRTUAL: '🌐',
  PHYSICAL: '💳',
  PREPAID: '💰',
}

export default function CardList({ cards, selectedToken, onSelect }) {
  if (!cards.length) {
    return (
      <div style={styles.empty}>
        Brak kart w systemie
      </div>
    )
  }

  return (
    <div style={styles.list}>
      {cards.map(card => {
        const statusStyle = STATUS_COLORS[card.status] || STATUS_COLORS.EXPIRED
        const isSelected = card.card_token === selectedToken
        return (
          <div
            key={card.card_token}
            onClick={() => onSelect(card.card_token)}
            style={{
              ...styles.row,
              background: isSelected ? '#f0f0ff' : 'white',
              borderColor: isSelected ? '#6366f1' : '#f1f5f9',
            }}
          >
            <div style={styles.rowLeft}>
              <span style={styles.typeIcon}>
                {TYPE_ICONS[card.card_type] || '💳'}
              </span>
              <div>
                <div style={styles.pan}>{card.masked_pan}</div>
                <div style={styles.token}>
                  {card.card_token.substring(0, 24)}...
                </div>
              </div>
            </div>
            <div style={styles.rowRight}>
              <span style={{
                ...styles.badge,
                background: statusStyle.bg,
                color: statusStyle.color,
              }}>
                {card.status}
              </span>
              <span style={styles.type}>{card.card_type}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

const styles = {
  list: { display: 'flex', flexDirection: 'column', gap: '8px' },
  row: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '12px 16px', borderRadius: '10px', cursor: 'pointer',
    border: '1.5px solid #f1f5f9', transition: 'all 0.15s',
  },
  rowLeft: { display: 'flex', alignItems: 'center', gap: '12px' },
  typeIcon: { fontSize: '22px' },
  pan: { fontSize: '14px', fontWeight: '600', color: '#1e293b' },
  token: { fontSize: '11px', color: '#94a3b8', marginTop: '2px' },
  rowRight: {
    display: 'flex', flexDirection: 'column',
    alignItems: 'flex-end', gap: '4px'
  },
  badge: {
    padding: '2px 10px', borderRadius: '20px',
    fontSize: '11px', fontWeight: '700'
  },
  type: { fontSize: '11px', color: '#94a3b8' },
  empty: {
    textAlign: 'center', color: '#94a3b8',
    padding: '40px', fontSize: '14px'
  },
}