import { describe, expect, it } from "vitest"
import { filterBinds, parseFilters, parseRunOptions, percentile, QUERIES, userFilters } from "../../lib/workload"

describe("benchmark workload", () => {
  it("rejects arbitrary load and duration", () => {
    expect(() => parseRunOptions({ users: 101, duration: 30, thinkMs: 0 })).toThrow()
    expect(() => parseRunOptions({ users: 100, duration: 3600, thinkMs: 0 })).toThrow()
    expect(parseRunOptions({ users: 50, duration: 30, thinkMs: 1000 })).toEqual({ users: 50, duration: 30, thinkMs: 1000 })
  })
  it("rejects injected or out of range filters", () => {
    expect(() => parseFilters({ region: "1 OR TRUE", month: 6 })).toThrow()
    expect(() => parseFilters({ region: 1, month: 13 })).toThrow()
  })
  it("uses exclusive month boundaries including December", () => {
    expect(filterBinds({ region: 8, month: 12 })).toEqual([8, "2025-12-01", "2026-01-01"])
  })
  it("produces matching reproducible workload sequences", () => {
    for (let user = 0; user < 100; user++) {
      for (let cycle = 0; cycle < 20; cycle++) expect(parseFilters(userFilters(user, cycle))).toEqual(userFilters(user, cycle))
    }
    expect(QUERIES).toHaveLength(4)
    for (const query of QUERIES) expect(query.match(/\?/g)).toHaveLength(3)
  })
  it("uses nearest-rank percentiles without fabricating empty metrics", () => {
    expect(percentile([], 0.95)).toBeNull()
    expect(percentile([10, 30, 20, 40], 0.5)).toBe(20)
    expect(percentile([10, 30, 20, 40], 0.95)).toBe(40)
  })
})
