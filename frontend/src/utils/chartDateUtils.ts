/** K 线日期与缠论结构在图表轴上的索引映射（PC / 移动端共用） */

/**
 * 归一化成 'YYYY-MM-DD HH:mm:ss'；不足秒级精度（如只有 'YYYY-MM-DD'）时原样返回。
 *
 * 分钟级周期同一天有多根 bar，键必须带时间才唯一。若只按日期建索引，
 * 同一天内的笔会退化成一个点，跨天的笔也会整体错位到当天最后一根。
 */
export function normTs(s: string): string {
  const t = String(s ?? '').replace('T', ' ').trim()
  return t.length >= 19 ? t.slice(0, 19) : t
}

/** 只保留日期部分 'YYYY-MM-DD' */
export function normDay(s: string): string {
  return String(s ?? '').replace('T', ' ').trim().slice(0, 10)
}

/** 轴标签：日线及以上显示日期，分钟级显示 'MM-DD HH:mm' */
export function formatAxisLabel(v: string): string {
  const t = String(v ?? '')
  if (t.length <= 10) return t
  return t.slice(5, 16)
}

/** 完整时间戳展示：日线及以上只显示日期，分钟级显示到分钟 */
export function formatStamp(v: string): string {
  const t = normTs(v)
  if (t.length <= 10) return t
  return t.slice(11) === '00:00:00' ? t.slice(0, 10) : t.slice(0, 16)
}

export function buildDateLookup(keys: string[]): Map<string, number> {
  const lookup = new Map<string, number>()
  for (let j = 0; j < keys.length; j++) lookup.set(keys[j], j)
  return lookup
}

function parseTs(v: string): number {
  const t = Date.parse(String(v).replace(/-/g, '/'))
  return Number.isNaN(t) ? NaN : t
}

/**
 * 在 keys（K 线时间戳，已 normTs）里定位索引。
 * 策略：完整时间戳精确匹配 → 时间戳最接近的一根 → 按日期匹配当天第一根。
 */
export function dateToIdxRobust(d: string, keys: string[], lookup?: Map<string, number>): number {
  const full = normTs(d)
  const fromMap = lookup?.get(full)
  if (fromMap != null) return fromMap

  const exact = keys.indexOf(full)
  if (exact >= 0) return exact

  // 端点只给到日期（无时间分量）：先按日期匹配当天第一根。
  // 不能用「时间最近」，那会被吸到前一天最后一根 bar 上（跨天偏差）。
  if (full.length <= 10) {
    const day = full.slice(0, 10)
    for (let j = 0; j < keys.length; j++) {
      if (keys[j].slice(0, 10) === day) return j
    }
  }

  // 带时间但精确匹配不上（bar 时间点不同）→ 取时间最接近的一根
  const t = parseTs(full)
  if (!Number.isNaN(t)) {
    let best = -1
    let bestDiff = Infinity
    for (let j = 0; j < keys.length; j++) {
      const tj = parseTs(keys[j])
      if (Number.isNaN(tj)) continue
      const diff = Math.abs(tj - t)
      if (diff < bestDiff) {
        bestDiff = diff
        best = j
      }
    }
    if (best >= 0) return best
  }

  // 最后兜底：按日期前缀匹配当天第一根
  const day = normDay(d)
  if (day) {
    for (let j = 0; j < keys.length; j++) {
      if (keys[j].slice(0, 10) === day) return j
    }
  }
  return -1
}

export function resolveBarRange(
  start: string,
  end: string,
  n: number,
  keys: string[],
  lookup?: Map<string, number>,
): [number, number] | null {
  if (n <= 0) return null
  let s = dateToIdxRobust(start, keys, lookup)
  let e = dateToIdxRobust(end, keys, lookup)
  if (s < 0 && e < 0) return null
  if (s < 0) s = 0
  if (e < 0) e = n - 1
  if (s > e) [s, e] = [e, s]
  s = Math.max(0, Math.min(n - 1, s))
  e = Math.max(0, Math.min(n - 1, e))
  if (s > e) return null
  return [s, e]
}
