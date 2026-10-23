import { beforeEach, describe, expect, it, vi } from "vitest"
vi.mock("../../lib/snowflake", () => ({ querySnowflake: vi.fn().mockResolvedValue([]) }))
vi.mock("../../lib/benchmark-client", () => ({ openSessions: vi.fn() }))
import { openSessions } from "../../lib/benchmark-client"
import { startRun, state, stopRun, summarize, MAX_QUERIES_PER_SIDE } from "../../lib/benchmark"

beforeEach(() => { state.active = null; state.runs = []; state.dashboardBusy.clear(); state.maintenance = false; vi.clearAllMocks() })
describe("bounded runner", () => {
  it("allows only one active comparison and cleans up cancelled preparation", async () => {
    const close = vi.fn().mockResolvedValue(undefined)
    vi.mocked(openSessions).mockImplementation(async (_side, _count, signal) => {
      await new Promise((resolve) => setTimeout(resolve, 5))
      if (signal.aborted) throw new Error("Cancelled")
      return [{ execute: vi.fn(), close }]
    })
    const run = startRun({ users: 1, duration: 15, thinkMs: 0 })
    expect(() => startRun({ users: 1, duration: 15, thinkMs: 0 })).toThrow("Another operation")
    stopRun()
    await vi.waitFor(() => expect(state.active).toBeNull())
    expect(run.status).toBe("cancelled")
  })
  it("separates warehouse sessions, excludes warmup and enforces query caps", async () => {
    const close = vi.fn().mockResolvedValue(undefined)
    vi.mocked(openSessions).mockImplementation(async (side) => [{
      close,
      execute: vi.fn().mockImplementation(async () => ({ rows: [], queryId: `${side}-query`, elapsedMs: 10, error: null, cancelled: false })),
    }])
    const run = startRun({ users: 1, duration: 15, thinkMs: 0 })
    await vi.waitFor(() => expect(state.active).toBeNull(), { timeout: 15000 })
    expect(run.status).toBe("completed")
    expect(run.samples).toHaveLength(MAX_QUERIES_PER_SIDE * 2)
    expect(run.submitted).toEqual({ standard: MAX_QUERIES_PER_SIDE, interactive: MAX_QUERIES_PER_SIDE })
    expect(close).toHaveBeenCalledTimes(2)
    expect(openSessions).toHaveBeenCalledWith("standard", 1, expect.any(AbortSignal))
    expect(openSessions).toHaveBeenCalledWith("interactive", 1, expect.any(AbortSignal))
    const summary = summarize(run)
    expect(summary.sides.standard.historyCount).toBe(0)
    expect(summary.sides.standard.queueP95).toBeNull()
  }, 20000)
  it("counts failures rather than presenting them as fast successes", async () => {
    vi.mocked(openSessions).mockRejectedValue(new Error("Warmup unavailable"))
    const run = startRun({ users: 1, duration: 15, thinkMs: 0 })
    await vi.waitFor(() => expect(state.active).toBeNull())
    run.samples.push({ side: "interactive", user: 0, cycle: 0, widget: 0, finishedAt: Date.now(), queryId: "failure", elapsedMs: 5000, error: "Statement timed out", cancelled: false })
    const summary = summarize(run)
    expect(summary.sides.interactive.errors).toBe(1)
    expect(summary.sides.interactive.timeouts).toBe(1)
    expect(summary.sides.interactive.p95).toBeNull()
  })
})
