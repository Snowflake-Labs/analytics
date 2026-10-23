import { NextRequest } from "next/server"
import { querySnowflake } from "@/lib/snowflake"
import { openSession } from "@/lib/benchmark-client"
import { reconcileHistory, startRun, state, stopRun, summarize } from "@/lib/benchmark"
import { filterBinds, parseFilters, parseRunOptions, QUERIES, Side, SIDES, WAREHOUSES } from "@/lib/workload"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

function json(value: unknown, status = 200) {
  return Response.json(value, { status, headers: { "Cache-Control": "no-store" } })
}

export async function GET(request: NextRequest) {
  const action = request.nextUrl.searchParams.get("action")
  if (action === "export") {
    const run = state.runs.find((item) => item.id === request.nextUrl.searchParams.get("id"))
    if (!run) return json({ error: "Run not found" }, 404)
    return json({ summary: summarize(run), queries: run.samples, refreshes: run.refreshes })
  }
  if (action === "config") {
    try {
      const warehouses = await querySnowflake("SHOW WAREHOUSES LIKE 'DEMO%XS'")
      const parameters = await Promise.all(SIDES.map(async (side) => {
        const rows = await querySnowflake(`SHOW PARAMETERS LIKE 'MAX_CONCURRENCY_LEVEL' IN WAREHOUSE ${WAREHOUSES[side]}`)
        return [side, rows[0]?.value ?? rows[0]?.VALUE ?? null]
      }))
      return json({ warehouses, concurrency: Object.fromEntries(parameters), table: "CONCURRENCY_DEMO_DB.DEMO.SALES" })
    } catch (error) { return json({ error: error instanceof Error ? error.message : "Configuration unavailable" }, 500) }
  }
  const latest = state.runs[0]
  if (latest && (!latest.historyUpdatedAt || Date.now() - latest.historyUpdatedAt > 10_000)) void reconcileHistory(latest)
  return json({ active: state.active?.id ?? null, runs: state.runs.map(summarize) })
}

export async function POST(request: NextRequest) {
  const origin = request.headers.get("origin")
  if (origin && new URL(origin).host !== request.headers.get("host")) return json({ error: "Cross-origin mutations are not allowed" }, 403)
  if (!request.headers.get("content-type")?.startsWith("application/json")) return json({ error: "JSON body required" }, 415)
  try {
    const body = await request.json()
    if (body.action === "start") return json(summarize(startRun(parseRunOptions(body))), 202)
    if (body.action === "stop") { stopRun(); return json({ stopping: true }) }
    if (body.action === "history") {
      const run = state.runs.find((item) => item.id === body.id)
      if (!run) return json({ error: "Run not found" }, 404)
      await reconcileHistory(run)
      return json(summarize(run))
    }
    if (body.action === "suspend") {
      if (state.active || state.dashboardBusy.size || state.maintenance) return json({ error: "Stop active operations first" }, 409)
      state.maintenance = true
      try {
        await Promise.all(Object.values(state.dashboardSessions).map((session) => session.close()))
        state.dashboardSessions = {}
        const warehouses = await querySnowflake("SHOW WAREHOUSES LIKE 'DEMO%XS'")
        await Promise.all(SIDES.filter((side) => warehouses.some((warehouse) => warehouse.name === WAREHOUSES[side] && warehouse.state !== "SUSPENDED")).map((side) => querySnowflake(`ALTER WAREHOUSE ${WAREHOUSES[side]} SUSPEND`)))
        return json({ suspended: true })
      } finally { state.maintenance = false }
    }
    if (body.action === "dashboard") {
      const side = body.side as Side
      if (!SIDES.includes(side)) return json({ error: "Invalid warehouse" }, 400)
      const filters = parseFilters(body.filters)
      if (state.dashboardBusy.has(side) || state.maintenance || state.active?.status === "preparing") return json({ error: "Warehouse operation in progress" }, 409)
      state.dashboardBusy.add(side)
      const started = performance.now()
      try {
        const session = state.dashboardSessions[side] ??= await openSession(side)
        const data = []
        for (const sql of QUERIES) {
          const result = await session.execute(sql, filterBinds(filters), `concurrency-dashboard:${side}`, request.signal)
          if (result.error) {
            delete state.dashboardSessions[side]
            await session.close()
            return json({ error: result.error, elapsedMs: performance.now() - started, data }, 502)
          }
          data.push(result)
        }
        return json({ data, elapsedMs: performance.now() - started, filters })
      } finally { state.dashboardBusy.delete(side) }
    }
    return json({ error: "Unknown action" }, 400)
  } catch (error) { return json({ error: error instanceof Error ? error.message : "Request failed" }, 400) }
}
