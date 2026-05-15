import { useState, useEffect } from 'react'
import Login from './components/Login.jsx'
import Dashboard from './components/Dashboard.jsx'

export default function App() {
  const [isLoggedIn, setIsLoggedIn] = useState(false)

  useEffect(() => {
    const token = localStorage.getItem('adminToken')
    if (token) setIsLoggedIn(true)
  }, [])

  const handleLogin = () => setIsLoggedIn(true)

  const handleLogout = () => {
    localStorage.removeItem('adminToken')
    localStorage.removeItem('apiKey')
    setIsLoggedIn(false)
  }

  return isLoggedIn
    ? <Dashboard onLogout={handleLogout} />
    : <Login onLogin={handleLogin} />
}