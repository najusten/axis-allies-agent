// Thin fetch wrappers over the Flask JSON API.
async function req(url, opts) {
  const res = await fetch(url, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok || body.error) {
    const err = new Error(body.error || `HTTP ${res.status}`);
    err.body = body;
    throw err;
  }
  return body;
}

export const api = {
  state: () => req('/api/state'),
  action: (data) => req('/api/action', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
  newGame: (opts) => req('/api/new_game', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(opts || {}) }),
  abilities: () => req('/api/abilities'),
  scenarios: () => req('/api/scenarios'),
};
