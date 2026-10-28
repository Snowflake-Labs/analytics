"use client"

import { useQuery } from "@tanstack/react-query"
import { RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"

async function fetchTime(): Promise<{ time: string }> {
  const res = await fetch("/api/time")
  if (!res.ok) throw new Error(`HTTP error ${res.status}`)
  const data = await res.json()
  if (data.error) throw new Error(data.error)
  return data
}

export function TimeCard() {
  const { data, error, isFetching, refetch } = useQuery({
    queryKey: ["time"],
    queryFn: fetchTime,
  })

  return (
    <Card className="flex flex-col">
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle>Calling an API endpoint</CardTitle>
          <CardDescription>
            Calls an API route that returns the current Snowflake timestamp.
          </CardDescription>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => refetch()}
          disabled={isFetching}
          aria-label="Refresh"
          className="shrink-0 ml-2"
        >
          <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} />
        </Button>
      </CardHeader>
      <Separator />
      <CardContent className="flex-1 flex flex-col items-center justify-center text-center py-8 bg-muted rounded-b-lg">
        {error && <p className="text-sm text-destructive">{error.message}</p>}
        {!data && !error && (
          <div className="space-y-2 flex flex-col items-center">
            <Skeleton className="h-3 w-[120px]" />
            <Skeleton className="h-7 w-[220px]" />
          </div>
        )}
        {data && (
          <div>
            <p className="text-xs text-muted-foreground uppercase tracking-wide mb-2">Snowflake Timestamp</p>
            <p className="text-2xl font-semibold tabular-nums">{data.time}</p>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
