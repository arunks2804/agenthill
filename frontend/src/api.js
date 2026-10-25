// Thin API client. Tenant and acting user travel as headers; credentials never touch the browser storage.
let identity = { tenant: 'acme', user: 'maya' }
export const setIdentity = (id) => { identity = { ...identity, ...id } }
export const getIdentity = () => identity

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail))
    this.status = status
    this.detail = detail
  }
}

async function request(method, path, body) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Tenant-ID': identity.tenant, 'X-User': identity.user },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await res.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!res.ok) {
    let detail = data && data.detail !== undefined ? data.detail : data
    if (typeof detail === 'string') { try { detail = JSON.parse(detail) } catch { /* plain message */ } }
    throw new ApiError(res.status, detail)
  }
  return data
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, b = {}) => request('POST', p, b),
  put: (p, b = {}) => request('PUT', p, b),
  patch: (p, b = {}) => request('PATCH', p, b),
  del: (p) => request('DELETE', p),
}

export function errorText(e) {
  if (!e) return ''
  const d = e.detail
  if (d && typeof d === 'object' && d.message) return `${d.code ? d.code + ': ' : ''}${d.message}${d.hint ? ' — ' + d.hint : ''}`
  return e.message || String(e)
}
