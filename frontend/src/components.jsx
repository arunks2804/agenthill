import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText } from './api'

export const pct = (v, d = 0) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(d)}%`)
export const num = (v, d = 0) => (v === null || v === undefined ? '—' : Number(v).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d }))
export const ms = (v) => (v === null || v === undefined ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`)
export const usd = (v) => (v === null || v === undefined ? '—' : `$${Number(v).toFixed(4)}`)
export const when = (s) => (s ? new Date(s).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—')
export const delta = (v, d = 1, asPct = true, invert = false, fmt = null) => {
  if (v === null || v === undefined) return <span className="muted">—</span>
  const good = invert ? v < 0 : v > 0
  const sign = v > 0 ? '+' : v < 0 ? '−' : ''
  const txt = asPct ? `${sign}${Math.abs(v * 100).toFixed(d)} pts` : fmt ? `${sign}${fmt(Math.abs(v))}` : `${sign}${num(Math.abs(v), d)}`
  return <span className={v === 0 ? 'muted' : good ? 'pos' : 'neg'}>{txt}</span>
}

export function useApi(path, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const load = useCallback(async () => {
    if (!path) { setData(null); return }
    setLoading(true)
    try { setData(await api.get(path)); setError(null) } catch (e) { setError(e) } finally { setLoading(false) }
  }, [path])
  useEffect(() => { load() }, [load, ...deps])
  return { data, error, loading, reload: load, setData }
}

export function useInterval(fn, delay) {
  const ref = useRef(fn)
  ref.current = fn
  useEffect(() => {
    if (delay === null) return
    const id = setInterval(() => ref.current(), delay)
    return () => clearInterval(id)
  }, [delay])
}

const TONES = {
  pass: 'green', passed: 'green', PASS: 'green', completed: 'green', approved: 'green', accept: 'green', validated: 'green', fixed: 'green', resolved: 'green', ok: 'green', confirm: 'green', agree: 'green',
  fail: 'red', failed: 'red', FAIL: 'red', rejected: 'red', reject: 'red', regressed: 'red', critical: 'red', error: 'red', blocked: 'red', invalidated: 'red',
  running: 'blue', queued: 'blue', pending_approval: 'amber', pending_review: 'amber', proposed: 'amber', PASS_WITH_UNMEASURED: 'amber', not_measured: 'amber',
  high: 'orange', medium: 'amber', low: 'gray', P1: 'red', P2: 'orange', P3: 'gray', skip: 'gray', still_failing: 'orange', still_passing: 'gray',
  escalated: 'violet', declined: 'gray', incomplete: 'orange', experimental: 'blue', baseline: 'gray',
}
export function Badge({ children, tone, title }) {
  const t = tone || TONES[children] || 'gray'
  return <span className={`badge ${t}`} title={title}>{String(children).replaceAll('_', ' ')}</span>
}

export function Card({ title, actions, children, className = '', subtitle }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <div><h3>{title}</h3>{subtitle && <div className="sub">{subtitle}</div>}</div>
          <div className="actions">{actions}</div>
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  )
}

export function Stat({ label, value, hint, tone }) {
  return (
    <div className={`stat ${tone || ''}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  )
}

export function ErrorBox({ error }) {
  if (!error) return null
  return <div className="error-box" role="alert">{typeof error === 'string' ? error : errorText(error)}</div>
}

export function Empty({ children }) {
  return <div className="empty">{children}</div>
}

export function Button({ children, onClick, kind = 'primary', disabled, busy, ...rest }) {
  const [working, setWorking] = useState(false)
  const handle = async (e) => {
    if (!onClick) return
    setWorking(true)
    try { await onClick(e) } finally { setWorking(false) }
  }
  return (
    <button className={`btn ${kind}`} onClick={handle} disabled={disabled || working || busy} {...rest}>
      {(working || busy) && <span className="spinner" />}{children}
    </button>
  )
}

export function Json({ value, max = 2000 }) {
  const s = JSON.stringify(value, null, 2) || ''
  return <pre className="json">{s.length > max ? s.slice(0, max) + '\n…' : s}</pre>
}

export function Progress({ done, total }) {
  const p = total ? Math.round((done / total) * 100) : 0
  return <div className="progress"><div style={{ width: `${p}%` }} /><span>{done}/{total}</span></div>
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs">
      {tabs.map((t) => (
        <button key={t.id} className={value === t.id ? 'active' : ''} onClick={() => onChange(t.id)}>{t.label}</button>
      ))}
    </div>
  )
}

export function ModelTag({ m }) {
  if (!m) return null
  return <span className="model-tag">{m.provider}:{m.model}</span>
}
