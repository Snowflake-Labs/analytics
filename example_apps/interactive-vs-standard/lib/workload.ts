export const WAREHOUSES = { standard: "DEMO_STANDARD_XS", interactive: "DEMO_INTERACTIVE_XS" } as const
export type Side = keyof typeof WAREHOUSES
export const SIDES: Side[] = ["standard", "interactive"]
export const TABLE = "CONCURRENCY_DEMO_DB.DEMO.SALES"
export const REGIONS = ["Pacific", "Mountain", "Central", "Northeast", "Southeast", "Europe", "Asia Pacific", "Latin America"]
export const CATEGORIES = ["Home", "Electronics", "Apparel", "Sports", "Beauty", "Outdoors", "Kitchen", "Accessories"]
export interface Filters { region: number; month: number }
export const DEFAULT_FILTERS: Filters = { region: 1, month: 6 }
export function parseFilters(value: unknown): Filters {
  const filters = value as Filters
  if (!filters || !Number.isInteger(filters.region) || filters.region < 1 || filters.region > 8 || !Number.isInteger(filters.month) || filters.month < 1 || filters.month > 12) throw new Error("Invalid region or month")
  return { region: filters.region, month: filters.month }
}
export function filterBinds(filters: Filters): [number, string, string] {
  const start = `2025-${String(filters.month).padStart(2, "0")}-01`
  const end = filters.month === 12 ? "2026-01-01" : `2025-${String(filters.month + 1).padStart(2, "0")}-01`
  return [filters.region, start, end]
}
const predicate = ` FROM ${TABLE} WHERE REGION_ID = ? AND SALE_DATE >= ?::DATE AND SALE_DATE < ?::DATE`
export const QUERIES = [
  `SELECT SUM(REVENUE) AS REVENUE, COUNT(*) AS ORDERS, SUM(QUANTITY) AS UNITS${predicate}`,
  `SELECT TO_CHAR(SALE_DATE, 'YYYY-MM-DD') AS DAY, SUM(REVENUE) AS REVENUE${predicate} GROUP BY SALE_DATE ORDER BY SALE_DATE`,
  `SELECT CATEGORY_ID, SUM(REVENUE) AS REVENUE${predicate} GROUP BY CATEGORY_ID ORDER BY CATEGORY_ID`,
  `SELECT PRODUCT_ID, SUM(REVENUE) AS REVENUE, SUM(QUANTITY) AS UNITS${predicate} GROUP BY PRODUCT_ID ORDER BY REVENUE DESC, PRODUCT_ID LIMIT 5`,
]
export function userFilters(user: number, cycle: number): Filters {
  return { region: (user + cycle) % 8 + 1, month: (Math.floor(user / 8) + cycle) % 12 + 1 }
}
export interface RunOptions { users: number; duration: number; thinkMs: number }
export function parseRunOptions(value: unknown): RunOptions {
  const options = value as RunOptions
  if (!options || ![1, 10, 25, 50, 100].includes(options.users) || ![15, 30, 60].includes(options.duration) || ![0, 1000, 3000].includes(options.thinkMs)) throw new Error("Invalid run options")
  return { users: options.users, duration: options.duration, thinkMs: options.thinkMs }
}
export function percentile(values: number[], fraction: number): number | null {
  if (!values.length) return null
  const sorted = [...values].sort((left, right) => left - right)
  return sorted[Math.max(0, Math.ceil(sorted.length * fraction) - 1)]
}
