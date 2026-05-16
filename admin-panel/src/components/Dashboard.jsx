import { useState, useEffect } from 'react'
import { cardAPI } from '../api.js'
import CardList from './CardList.jsx'
import CardDetail from './CardDetail.jsx'

export default function Dashboard({ onLogout }) {
  const [cards, setCards] = useState([])
  const [loading, setLoading] = useState(true)
  const [selectedToken, setSelectedToken] = useState(null)
  const [error, setError] = useState('')

  const fetchCards = async () => {
    try {
      setLoading(true)
      const res = await cardAPI.list()
      setCards(res.data.cards || [])
      setError('')
    } catch (e) {
      setError('Błąd połączenia z API')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchCards() }, [])

  // Statystyki
  const stats = {
    total: cards.length,
    active: cards.filter(c => c.status === 'ACTIVE').length,
    requested: cards.filter(c => c.status === 'REQUESTED').length,
    producing: cards.filter(c => c.status === 'PRODUCING').length,
    shipped: cards.filter(c => c.status === 'SHIPPED').length,
    blocked: cards.filter(c => c.status === 'BLOCKED').length,
  }

  return (
    <div style={styles.page}>
      {/* Navbar */}
      <nav style={styles.nav}>
        <div style={styles.navLeft}>
          <span style={styles.navLogo}>💳</span>
          <span style={styles.navTitle}>Card Provider</span>
          <span style={styles.navSub}>Panel Admina</span>
        </div>
        <div style={styles.navRight}>
          <button onClick={onLogout} style={styles.logoutBtn}>
            Wyloguj
          </button>
        </div>
      </nav>

      <div style={styles.content}>
        {/* Stats */}
        <div style={styles.statsGrid}>
          <StatCard label="Wszystkie karty" value={stats.total} color="#6366f1" />
          <StatCard label="Aktywne" value={stats.active} color="#22c55e" />
          <StatCard label="Zamówione" value={stats.requested} color="#f59e0b" />
          <StatCard label="W produkcji" value={stats.producing} color="#8b5cf6" />
          <StatCard label="Wysłane" value={stats.shipped} color="#06b6d4" />
          <StatCard label="Zablokowane" value={stats.blocked} color="#ef4444" />
        </div>

        {error && <div style={styles.error}>{error}</div>}

        {/* Main content */}
        <div style={styles.mainGrid}>
          <div style={styles.listSection}>
            <div style={styles.sectionHeader}>
              <h2 style={styles.sectionTitle}>Karty płatnicze</h2>
              <button onClick={fetchCards} style={styles.refreshBtn}>
                🔄 Odśwież
              </button>
            </div>
            {loading
              ? <div style={styles.loading}>Ładowanie...</div>
              : <CardList
                  cards={cards}
                  selectedToken={selectedToken}
                  onSelect={setSelectedToken}
                />
            }
          </div>

          <div style={styles.detailSection}>
            {selectedToken
              ? <CardDetail
                  token={selectedToken}
                  onBack={() => setSelectedToken(null)}
                  onRefresh={fetchCards}
                />
              : <div style={styles.empty}>
                  <div style={{ fontSize: '48px' }}>👈</div>
                  <p>Wybierz kartę aby zobaczyć szczegóły</p>
                </div>
            }
          </div>
        </div>
      </div>
    </div>
  )
}

function StatCard({ label, value, color }) {
  return (
    <div style={{ ...styles.statCard, borderTop: `3px solid ${color}` }}>
      <div style={{ ...styles.statValue, color }}>{value}</div>
      <div style={styles.statLabel}>{label}</div>
    </div>
  )
}

const styles = {
  page: { minHeight: '100vh', background: '#f8fafc' },
  nav: {
    background: 'white',
    borderBottom: '1px solid #e2e8f0',
    padding: '0 24px',
    height: '60px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    position: 'sticky',
    top: 0,
    zIndex: 100,
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
  },
  navLeft: { display: 'flex', alignItems: 'center', gap: '12px' },
  navLogo: { fontSize: '24px' },
  navTitle: { fontWeight: '700', fontSize: '16px', color: '#1e293b' },
  navSub: {
    fontSize: '12px', color: '#64748b',
    background: '#f1f5f9', padding: '2px 8px', borderRadius: '20px'
  },
  navRight: { display: 'flex', alignItems: 'center', gap: '12px' },
  bankBadge: {
    fontSize: '12px', fontWeight: '600', color: '#6366f1',
    background: '#e0e7ff', padding: '4px 10px', borderRadius: '20px'
  },
  logoutBtn: {
    padding: '6px 14px', background: 'white', border: '1.5px solid #e2e8f0',
    borderRadius: '8px', cursor: 'pointer', fontSize: '13px',
    color: '#64748b', fontFamily: 'Inter, sans-serif'
  },
  content: { padding: '24px', maxWidth: '1400px', margin: '0 auto' },
  statsGrid: {
    display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)',
    gap: '16px', marginBottom: '24px'
  },
  statCard: {
    background: 'white', borderRadius: '12px', padding: '16px 20px',
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)'
  },
  statValue: { fontSize: '28px', fontWeight: '700', marginBottom: '4px' },
  statLabel: { fontSize: '12px', color: '#64748b', fontWeight: '500' },
  mainGrid: {
    display: 'grid', gridTemplateColumns: '1fr 420px', gap: '24px'
  },
  listSection: {
    background: 'white', borderRadius: '12px', padding: '20px',
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)'
  },
  detailSection: {
    background: 'white', borderRadius: '12px', padding: '20px',
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)', height: 'fit-content',
    position: 'sticky', top: '80px'
  },
  sectionHeader: {
    display: 'flex', justifyContent: 'space-between',
    alignItems: 'center', marginBottom: '16px'
  },
  sectionTitle: { fontSize: '16px', fontWeight: '600', color: '#1e293b' },
  refreshBtn: {
    padding: '6px 12px', background: '#f1f5f9', border: 'none',
    borderRadius: '8px', cursor: 'pointer', fontSize: '13px',
    fontFamily: 'Inter, sans-serif'
  },
  loading: { textAlign: 'center', color: '#64748b', padding: '40px' },
  error: {
    background: '#fee2e2', color: '#dc2626', padding: '12px 16px',
    borderRadius: '8px', marginBottom: '16px', fontSize: '14px'
  },
  empty: {
    textAlign: 'center', color: '#94a3b8', padding: '40px 20px',
    fontSize: '14px', display: 'flex', flexDirection: 'column',
    alignItems: 'center', gap: '12px'
  },
}