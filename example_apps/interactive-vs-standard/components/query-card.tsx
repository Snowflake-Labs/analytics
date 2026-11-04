"use client"

import { useQuery } from "@tanstack/react-query"
import { RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"

type SingleResult = {
  mode: string
  result: { USER: string; ROLE: string } | null
  error?: string
}

type QueryResponse = {
  service: SingleResult
  caller: SingleResult
}

async function fetchQuery(): Promise<QueryResponse> {
  const res = await fetch("/api/query")
  if (!res.ok) throw new Error(`HTTP error ${res.status}`)
  const json = await res.json()
  if (json.error) throw new Error(json.error)
  return json
}

export function QueryCard() {
  const { data, error, isFetching, refetch } = useQuery({
    queryKey: ["query"],
    queryFn: fetchQuery,
  })

  const svc = data?.service
  const cal = data?.caller

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle>Service context vs user context</CardTitle>
          <CardDescription>
            Compares{" "}
            <Badge variant="secondary" className="font-mono text-xs">CURRENT_USER()</Badge>
            {" "}and{" "}
            <Badge variant="secondary" className="font-mono text-xs">CURRENT_ROLE()</Badge>
            {" "}between the service token and the caller&apos;s (user) token.
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
      <CardContent>
        {error && <p className="text-sm text-destructive">{error.message}</p>}
        {!data && !error && (
          <div className="space-y-3">
            <div className="flex gap-4">
              <Skeleton className="h-4 w-[60px]" />
              <Skeleton className="h-4 w-[100px]" />
              <Skeleton className="h-4 w-[100px]" />
            </div>
            <div className="flex gap-4">
              <Skeleton className="h-4 w-[60px]" />
              <Skeleton className="h-4 w-[80px]" />
              <Skeleton className="h-4 w-[80px]" />
            </div>
            <div className="flex gap-4">
              <Skeleton className="h-4 w-[60px]" />
              <Skeleton className="h-4 w-[80px]" />
              <Skeleton className="h-4 w-[80px]" />
            </div>
          </div>
        )}
        {data && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead />
                <TableHead>Service</TableHead>
                <TableHead>Caller</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow>
                <TableHead className="w-20">USER</TableHead>
                <TableCell>
                  {svc?.error ? (
                    <span className="text-destructive">{svc.error}</span>
                  ) : (
                    svc?.result?.USER ?? "—"
                  )}
                </TableCell>
                <TableCell>
                  {cal?.error ? (
                    <span className="text-destructive">{cal.error}</span>
                  ) : (
                    cal?.result?.USER ?? "—"
                  )}
                </TableCell>
              </TableRow>
              <TableRow>
                <TableHead className="w-20">ROLE</TableHead>
                <TableCell>
                  {svc?.error ? (
                    <span className="text-destructive">{svc.error}</span>
                  ) : (
                    svc?.result?.ROLE ?? "—"
                  )}
                </TableCell>
                <TableCell>
                  {cal?.error ? (
                    <span className="text-destructive">{cal.error}</span>
                  ) : (
                    cal?.result?.ROLE ?? "—"
                  )}
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
