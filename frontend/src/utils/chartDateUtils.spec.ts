import { describe, expect, it } from 'vitest'
import { buildDateLookup, dateToIdxRobust, resolveBarRange } from './chartDateUtils'

describe('chartDateUtils', () => {
  const dates = ['2024-01-02', '2024-01-03', '2024-01-04']

  it('dateToIdxRobust finds exact match', () => {
    expect(dateToIdxRobust('2024-01-03', dates)).toBe(1)
  })

  it('dateToIdxRobust uses lookup map', () => {
    const lookup = buildDateLookup(dates)
    expect(dateToIdxRobust('2024-01-04', dates, lookup)).toBe(2)
  })

  it('resolveBarRange clamps invalid endpoints', () => {
    const r = resolveBarRange('2024-01-01', '2024-01-99', 3, dates)
    expect(r).toEqual([0, 2])
  })

  // 回归：分钟级周期同一天有多根 bar，只按日期建索引会让笔退化成一个点
  describe('分钟级周期（同一天多根 bar）', () => {
    const keys = [
      '2026-06-11 16:00:00',
      '2026-06-12 10:30:00',
      '2026-06-12 14:00:00',
      '2026-06-12 16:00:00',
    ]
    const lookup = buildDateLookup(keys)

    it('按完整时间戳精确定位，同日不同 bar 不混淆', () => {
      expect(dateToIdxRobust('2026-06-12 10:30:00', keys, lookup)).toBe(1)
      expect(dateToIdxRobust('2026-06-12 14:00:00', keys, lookup)).toBe(2)
      expect(dateToIdxRobust('2026-06-12 16:00:00', keys, lookup)).toBe(3)
    })

    it('同一天内的笔不会再退化成单点', () => {
      const r = resolveBarRange('2026-06-12 10:30:00', '2026-06-12 14:00:00', keys.length, keys, lookup)
      expect(r).toEqual([1, 2])
    })

    it('只给到日期的端点回退到当天第一根', () => {
      expect(dateToIdxRobust('2026-06-12', keys, lookup)).toBe(1)
    })
  })
})
