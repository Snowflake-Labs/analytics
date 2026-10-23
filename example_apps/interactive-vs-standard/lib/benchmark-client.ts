import snowflake from "snowflake-sdk"
import { baseConfig, getServiceToken, readTomlDefaultConnection, tomlConnectionConfig } from "./snowflake"
import { Side, WAREHOUSES } from "./workload"

export interface QueryResult {
  rows: Record<string, any>[]
  queryId: string | null
  elapsedMs: number
  error: string | null
  cancelled: boolean
}
export interface Session {
  execute: (sql: string, binds: snowflake.Binds, tag: string, signal?: AbortSignal) => Promise<QueryResult>
  close: () => Promise<void>
}

export async function openSession(side: Side): Promise<Session> {
  const token = getServiceToken()
  const local = token ? null : readTomlDefaultConnection()
  if (!token && !local) throw new Error("No Snowflake runtime identity or local connection configured")
  const connection = snowflake.createConnection({
    ...(local ? tomlConnectionConfig(local) : {}), ...baseConfig(),
    ...(token ? { authenticator: "OAUTH", token } : {}),
    warehouse: WAREHOUSES[side], application: "ConcurrencyDemo",
    clientSessionKeepAlive: false,
  })
  try {
    await new Promise<void>((resolve, reject) => connection.connect((error) => error ? reject(error) : resolve()))
    await new Promise<void>((resolve, reject) => connection.execute({
      sqlText: "ALTER SESSION SET USE_CACHED_RESULT = FALSE, STATEMENT_TIMEOUT_IN_SECONDS = 60, STATEMENT_QUEUED_TIMEOUT_IN_SECONDS = 30",
      complete: (error) => error ? reject(error) : resolve(),
    }))
  } catch (error) {
    connection.destroy(() => {})
    throw error
  }
  return {
    execute(sql, binds, tag, signal) {
      const started = performance.now()
      if (signal?.aborted) return Promise.resolve({ rows: [], queryId: null, elapsedMs: 0, error: "Cancelled", cancelled: true })
      return new Promise<QueryResult>((resolve) => {
        let settled = false
        let statement: snowflake.RowStatement | undefined
        const cancel = () => { if (!settled && statement) statement.cancel(() => {}) }
        const finish = (error: Error | undefined, returned?: snowflake.RowStatement, rows?: Record<string, any>[]) => {
          if (settled) return
          settled = true
          clearTimeout(timeout)
          signal?.removeEventListener("abort", cancel)
          resolve({ rows: rows ?? [], queryId: returned?.getQueryId() ?? statement?.getQueryId() ?? null, elapsedMs: performance.now() - started, error: error?.message ?? null, cancelled: Boolean(signal?.aborted) })
        }
        const timeout = setTimeout(() => {
          cancel()
          connection.destroy(() => {})
          finish(new Error("Client deadline exceeded after 65 seconds"))
        }, 65_000)
        signal?.addEventListener("abort", cancel, { once: true })
        try {
          statement = connection.execute({
            sqlText: sql, binds,
            parameters: { QUERY_TAG: tag, USE_CACHED_RESULT: false },
            complete: (error, returned, rows) => finish(error, returned, rows),
          } as Parameters<typeof connection.execute>[0])
          if (signal?.aborted) cancel()
        } catch (error) { finish(error as Error) }
      })
    },
    close: () => new Promise<void>((resolve) => connection.destroy(() => resolve())),
  }
}

export async function openSessions(side: Side, count: number, signal: AbortSignal): Promise<Session[]> {
  const sessions: Session[] = []
  try {
    for (let offset = 0; offset < count; offset += 10) {
      if (signal.aborted) throw new Error("Preparation cancelled")
      const batch = await Promise.allSettled(Array.from({ length: Math.min(10, count - offset) }, () => openSession(side)))
      for (const result of batch) if (result.status === "fulfilled") sessions.push(result.value)
      const failure = batch.find((result) => result.status === "rejected")
      if (failure?.status === "rejected") throw failure.reason
    }
    return sessions
  } catch (error) {
    await Promise.allSettled(sessions.map((session) => session.close()))
    throw error
  }
}
