import { describe, expect, it } from 'vitest'

import { apiDate, localDateValue, shiftDate } from '../date'

describe('date helpers', () => {
  it('formats local dates without UTC drift', () => {
    expect(localDateValue(new Date(2026, 6, 29, 23, 30))).toBe('2026-07-29')
  })

  it('moves across month boundaries and formats API dates', () => {
    expect(shiftDate('2026-07-31', 1)).toBe('2026-08-01')
    expect(apiDate('2026-08-01')).toBe('2026/08/01')
  })
})
