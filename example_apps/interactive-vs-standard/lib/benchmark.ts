import { randomUUID } from "crypto"
import { openSessions, Session, QueryResult } from "./benchmark-client"
import { filterBinds, percentile, QUERIES, RunOptions, Side, SIDES, userFilters } from "./workload"
import { querySnowflake } from "./snowflake"

export interface Sample extends Omit<QueryResult, "rows"> {
  side: Side; user: number; cycle: number; widget: number; finishedAt: number
  queueMs?: number; executionMs?: number; compilationMs?: number; actualWarehouse?: string
}
interface Refresh { side: Side; elapsedMs: number; success: boolean }
export interface Run {
  id: string; options: RunOptions; status: "preparing" | "running" | "stopping" | "completed" | "cancelled" | "failed"
  createdAt: number; startedAt: number | null; endedAt: number | null; error: string | null
  samples: Sample[]; refreshes: Refresh[]; inFlight: Record<Side, number>
  submitted: Record<Side, number>; prepared: Record<Side, number>; controller: AbortController
  historyError: string | null; historyUpdatedAt: number | null
}
interface State { runs: Run[]; active: Run | null; historyBusy: Set<string>; dashboardBusy: Set<Side>; maintenance: boolean; dashboardSessions: Partial<Record<Side, Session>> }
const globalState = globalThis as typeof globalThis & { concurrencyDemo?: State }
export const state = globalState.concurrencyDemo ??= { runs: [], active: null, historyBusy: new Set(), dashboardBusy: new Set(), maintenance: false, dashboardSessions: {} }
state.dashboardSessions ??= {}
export const MAX_QUERIES_PER_SIDE = 4000

function pause(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    if (signal.aborted) return resolve()
    const finish = () => { clearTimeout(timer); signal.removeEventListener("abort", finish); resolve() }
    const timer = setTimeout(finish, milliseconds)
    signal.addEventListener("abort", finish, { once: true })
  })
}

export function startRun(options: RunOptions): Run {
  if (state.active || state.maintenance || state.dashboardBusy.size) throw new Error("Another operation is active")
  const run: Run = {
    id: randomUUID(), options, status: "preparing", createdAt: Date.now(), startedAt: null, endedAt: null, error: null,
    samples: [], refreshes: [], inFlight: { standard: 0, interactive: 0 }, submitted: { standard: 0, interactive: 0 },
    prepared: { standard: 0, interactive: 0 }, controller: new AbortController(), historyError: null, historyUpdatedAt: null,
  }
  state.active = run
  state.runs.unshift(run)
  state.runs.splice(20)
  void executeRun(run)
  return run
}

export function stopRun() {
  if (state.active) { state.active.status = "stopping"; state.active.controller.abort() }
}

async function worker(run: Run, side: Side, session: Session, user: number) {
  const deadline = run.startedAt! + run.options.duration * 1000
  let cycle = 0
  while (!run.controller.signal.aborted && Date.now() < deadline && run.submitted[side] < MAX_QUERIES_PER_SIDE) {
    const started = performance.now()
    let successful = true
    let widgets = 0
    for (let widget = 0; widget < QUERIES.length; widget++) {
      if (run.controller.signal.aborted || Date.now() >= deadline || run.submitted[side] >= MAX_QUERIES_PER_SIDE) { successful = false; break }
      run.submitted[side]++
      run.inFlight[side]++
      const result = await session.execute(QUERIES[widget], filterBinds(userFilters(user, cycle)), `concurrency-demo:${run.id}:${side}:${user}:${cycle}:${widget}`, run.controller.signal)
      run.inFlight[side]--
      const { rows: _rows, ...measurement } = result
      run.samples.push({ ...measurement, side, user, cycle, widget, finishedAt: Date.now() })
      widgets++
      if (result.error) { successful = false; break }
    }
    if (widgets) run.refreshes.push({ side, elapsedMs: performance.now() - started, success: successful && widgets === 4 })
    cycle++
    await pause(run.options.thinkMs, run.controller.signal)
  }
}

async function executeRun(run: Run) {
  const sessions: Record<Side, Session[]> = { standard: [], interactive: [] }
  try {
    for (const side of SIDES) {
      sessions[side] = await openSessions(side, run.options.users, run.controller.signal)
      run.prepared[side] = sessions[side].length
      for (let repetition = 0; repetition < 2; repetition++) {
        const warmed = await Promise.allSettled(sessions[side].map(async (session, user) => {
          for (let widget = 0; widget < QUERIES.length; widget++) {
            if (run.controller.signal.aborted) throw new Error("Preparation cancelled")
            const result = await session.execute(QUERIES[widget], filterBinds(userFilters(user, 0)), `concurrency-warmup:${run.id}`, run.controller.signal)
            if (result.error) throw new Error(`${side} warm-up: ${result.error}`)
          }
        }))
        const failure = warmed.find((result) => result.status === "rejected")
        if (failure?.status === "rejected") throw failure.reason
      }
    }
    if (run.controller.signal.aborted) throw new Error("Preparation cancelled")
    run.startedAt = Date.now()
    run.status = "running"
    await Promise.all(SIDES.flatMap((side) => sessions[side].map((session, user) => worker(run, side, session, user))))
    run.status = run.controller.signal.aborted ? "cancelled" : "completed"
  } catch (error) {
    run.status = run.controller.signal.aborted ? "cancelled" : "failed"
    run.error = error instanceof Error ? error.message : String(error)
    run.controller.abort()
  } finally {
    await Promise.allSettled(SIDES.flatMap((side) => sessions[side].map((session) => session.close())))
    run.endedAt = Date.now()
    state.active = null
    void reconcileHistory(run)
  }
}

export async function reconcileHistory(run: Run) {
  if (state.historyBusy.has(run.id) || !run.startedAt) return
  state.historyBusy.add(run.id)
  try {
    const rows = await querySnowflake(
      `SELECT QUERY_ID, WAREHOUSE_NAME, QUEUED_OVERLOAD_TIME, EXECUTION_TIME, COMPILATION_TIME
       FROM TABLE(CONCURRENCY_DEMO_DB.INFORMATION_SCHEMA.QUERY_HISTORY(END_TIME_RANGE_START => TO_TIMESTAMP_LTZ(?), RESULT_LIMIT => 10000))
       WHERE QUERY_TAG LIKE ?`,
      { binds: [new Date(run.createdAt).toISOString(), `concurrency-demo:${run.id}:%`] },
    )
    const byId = new Map(rows.map((row) => [row.QUERY_ID, row]))
    for (const sample of run.samples) {
      const row = sample.queryId ? byId.get(sample.queryId) : null
      if (row) {
        sample.queueMs = Number(row.QUEUED_OVERLOAD_TIME)
        sample.executionMs = Number(row.EXECUTION_TIME)
        sample.compilationMs = Number(row.COMPILATION_TIME)
        sample.actualWarehouse = String(row.WAREHOUSE_NAME)
      }
    }
    run.historyUpdatedAt = Date.now()
    run.historyError = null
  } catch (error) { run.historyError = error instanceof Error ? error.message : String(error) }
  finally { state.historyBusy.delete(run.id) }
}

export function summarize(run: Run) {
  const end = run.endedAt ?? Date.now()
  const seconds = run.startedAt ? Math.max((end - run.startedAt) / 1000, 0.001) : 0
  return {
    id: run.id, status: run.status, options: run.options, createdAt: run.createdAt, startedAt: run.startedAt,
    endedAt: run.endedAt, error: run.error, historyError: run.historyError, historyUpdatedAt: run.historyUpdatedAt,
    elapsedSeconds: seconds, prepared: run.prepared,
    sides: Object.fromEntries(SIDES.map((side) => {
      const samples = run.samples.filter((sample) => sample.side === side)
      const successes = samples.filter((sample) => !sample.error)
      const latencies = successes.map((sample) => sample.elapsedMs)
      const history = samples.filter((sample) => sample.queueMs !== undefined)
      const refreshes = run.refreshes.filter((refresh) => refresh.side === side)
      const successfulRefreshes = refreshes.filter((refresh) => refresh.success)
      return [side, {
        completed: samples.length, submitted: run.submitted[side], successes: successes.length,
        capped: run.submitted[side] >= MAX_QUERIES_PER_SIDE,
        errors: samples.length - successes.length, cancellations: samples.filter((sample) => sample.cancelled).length,
        timeouts: samples.filter((sample) => sample.error && /timeout|timed out|deadline|time limit/i.test(sample.error)).length,
        inFlight: run.inFlight[side], qps: seconds ? successes.length / seconds : 0,
        p50: percentile(latencies, 0.5), p95: percentile(latencies, 0.95), p99: percentile(latencies, 0.99),
        refreshP95: percentile(successfulRefreshes.map((refresh) => refresh.elapsedMs), 0.95),
        refreshFailures: refreshes.length - successfulRefreshes.length,
        queueP95: percentile(history.map((sample) => sample.queueMs!), 0.95),
        executionP95: percentile(history.map((sample) => sample.executionMs!), 0.95),
        compilationP95: percentile(history.map((sample) => sample.compilationMs!), 0.95),
        historyCount: history.length, queuedCount: history.filter((sample) => sample.queueMs! > 0).length,
        clientPoolWaitMs: 0,
        recent: samples.slice(-60).map((sample) => ({ ms: sample.elapsedMs, error: Boolean(sample.error) })),
        lastError: samples.findLast((sample) => sample.error)?.error ?? null,
      }]
    })),
  }
}
