import { describe, it, expect, vi, beforeEach } from "vitest"
import { GET } from "../../app/api/time/route"

vi.mock("../../lib/snowflake", () => ({
  querySnowflake: vi.fn(),
}))

import { querySnowflake } from "../../lib/snowflake"

describe("GET /api/time", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("returns time from Snowflake on success", async () => {
    vi.mocked(querySnowflake).mockResolvedValue([{ TIME: "2024-01-15T12:00:00Z" }])

    const response = await GET()
    const json = await response.json()

    expect(response.status).toBe(200)
    expect(json.time).toBe("2024-01-15T12:00:00Z")
    expect(querySnowflake).toHaveBeenCalledWith("SELECT CURRENT_TIMESTAMP() AS TIME")
  })

  it("returns null time when query returns no rows", async () => {
    vi.mocked(querySnowflake).mockResolvedValue([])

    const response = await GET()
    const json = await response.json()

    expect(response.status).toBe(200)
    expect(json.time).toBeNull()
  })

  it("returns 500 with error message on query failure", async () => {
    vi.mocked(querySnowflake).mockRejectedValue(new Error("Connection failed"))

    const response = await GET()
    const json = await response.json()

    expect(response.status).toBe(500)
    expect(json.error).toBe("Connection failed")
  })

  it("handles non-Error exceptions", async () => {
    vi.mocked(querySnowflake).mockRejectedValue("string error")

    const response = await GET()
    const json = await response.json()

    expect(response.status).toBe(500)
    expect(json.error).toBe("Unknown error")
  })
})
