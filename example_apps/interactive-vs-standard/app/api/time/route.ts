// Example route handler — returns the current Snowflake timestamp as JSON.
// Add more routes under app/api/ following the same pattern.

import { querySnowflake } from "@/lib/snowflake"

export const dynamic = "force-dynamic"

export async function GET() {
  try {
    const rows = await querySnowflake("SELECT CURRENT_TIMESTAMP() AS TIME")
    return Response.json({ time: rows[0]?.TIME ?? null })
  } catch (e) {
    const message = e instanceof Error ? e.message : "Unknown error"
    return Response.json({ error: message }, { status: 500 })
  }
}
