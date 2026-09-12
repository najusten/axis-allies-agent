// Flat-top hex geometry in axial coordinates (mirrors visualization.HexRenderer).
export const SIZE = 40;                 // circumradius
export const SQRT3 = Math.sqrt(3);
export const HEX_W = 2 * SIZE;
export const HEX_H = SQRT3 * SIZE;

export function axialToPixel(q, r) {
  return { x: SIZE * 1.5 * q, y: SIZE * SQRT3 * (r + q / 2) };
}

export function vertices(q, r) {
  const { x, y } = axialToPixel(q, r);
  const pts = [];
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 180) * (60 * i);
    pts.push([x + SIZE * Math.cos(a), y + SIZE * Math.sin(a)]);
  }
  return pts;
}

export function polygonPoints(q, r) {
  return vertices(q, r).map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
}

export function viewBox(hexes, pad = 30) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const h of hexes) {
    const { x, y } = axialToPixel(h.q, h.r);
    minX = Math.min(minX, x - SIZE); maxX = Math.max(maxX, x + SIZE);
    minY = Math.min(minY, y - HEX_H / 2); maxY = Math.max(maxY, y + HEX_H / 2);
  }
  return { x: minX - pad, y: minY - pad, w: maxX - minX + 2 * pad, h: maxY - minY + 2 * pad };
}

// Engine HexDirection index -> axial vector (facing.py DIRECTION_VECTORS)
export const DIRS = [[1, 0], [1, -1], [0, -1], [-1, 0], [-1, 1], [0, 1]];
export const DIR_NAMES = ['SE', 'NE', 'N', 'NW', 'SW', 'S'];

export function dirAngleDeg(i) {
  const [dq, dr] = DIRS[i];
  const a = axialToPixel(0, 0), b = axialToPixel(dq, dr);
  return (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
}

export function hexDistance(q1, r1, q2, r2) {
  const dq = q1 - q2, dr = r1 - r2;
  return Math.max(Math.abs(dq), Math.abs(dr), Math.abs(dq + dr));
}
