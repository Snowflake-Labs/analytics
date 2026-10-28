/**
 * Runs CURRENT_USER() / CURRENT_ROLE() with both owner's rights and caller's
 * rights in a single request so the UI can compare them side-by-side.
 *
 * GET /api/query
 *
 * Outside SPCS (local dev), both calls fall back to local dev credentials.
 */

import { querySnowflake } from "@/lib/snowflake"

export const dynamic = "force-dynamic"

const QUERY = `SELECT CURRENT_USER() AS "USER", CURRENT_ROLE() AS ROLE`

export async function GET() {
  try {
    const serviceRows = await querySnowflake(QUERY)

    let callerResult: { mode: string; result: Record<string, any> | null; error?: string }
    try {
      const callerRows = await querySnowflake(QUERY, { callersRights: true })
      callerResult = { mode: "caller", result: callerRows[0] ?? null }
    } catch (e) {
      callerResult = {
        mode: "caller",
        result: null,
        error: e instanceof Error ? e.message : "Failed",
      }
    }

    return Response.json({
      service: { mode: "service", result: serviceRows[0] ?? null },
      caller: callerResult,
    })
  } catch (e) {
    console.error("Query route error:", e)
    return Response.json({ error: "Query failed" }, { status: 500 })
  }
}
