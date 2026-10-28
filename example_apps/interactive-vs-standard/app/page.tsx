"use client"

import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Activity, ArrowUpRight, Download, LoaderCircle, Play, Power, RefreshCw, Square, Users, Zap } from "lucide-react"
import { CATEGORIES, DEFAULT_FILTERS, Filters, REGIONS, Side, SIDES } from "@/lib/workload"

async function post(body: unknown) {
  const response = await fetch("/api/demo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
  const result = await response.json()
  if (!response.ok) throw new Error(result.error ?? "Request failed")
  return result
}
const milliseconds = (value: number | null | undefined) => value == null ? "--" : value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${Math.round(value)}ms`
const currency = (value: number) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(value)

function Trace({ values, side }: { values: { ms: number; error: boolean }[]; side: Side }) {
  const maximum = Math.max(1000, ...values.map((value) => value.ms))
  return <div className={`trace ${side}`} aria-label="Recent measured query latency">
    {values.length ? values.map((value, index) => <span key={index} title={`${milliseconds(value.ms)}${value.error ? " error" : ""}`} className={value.error ? "failed" : ""} style={{ height: `${Math.max(3, value.ms / maximum * 100)}%` }} />) : <div className="empty-trace">No measurements yet</div>}
  </div>
}

function RetailPanel({ side, filters, refreshKey, disabled }: { side: Side; filters: Filters; refreshKey: number; disabled: boolean }) {
  const [result, setResult] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [started, setStarted] = useState(0)
  const [clock, setClock] = useState(Date.now())
  useEffect(() => {
    if (!loading) return
    const timer = setInterval(() => setClock(Date.now()), 100)
    return () => clearInterval(timer)
  }, [loading])
  useEffect(() => {
    if (!refreshKey || disabled) return
    let ignored = false
    setLoading(true); setError(""); setStarted(Date.now()); setClock(Date.now())
    post({ action: "dashboard", side, filters }).then((data) => { if (!ignored) setResult(data) }).catch((failure) => { if (!ignored) setError(failure.message) }).finally(() => { if (!ignored) setLoading(false) })
    return () => { ignored = true }
  }, [refreshKey, side])
  const totals = result?.data?.[0]?.rows?.[0]
  const daily = result?.data?.[1]?.rows ?? []
  const categories = result?.data?.[2]?.rows ?? []
  const products = result?.data?.[3]?.rows ?? []
  const maximum = Math.max(1, ...daily.map((row: any) => Number(row.REVENUE)))
  const categoryMax = Math.max(1, ...categories.map((row: any) => Number(row.REVENUE)))
  const stale = result && (result.filters.region !== filters.region || result.filters.month !== filters.month)
  return <section className={`retail ${side}`}>
    <div className="section-heading"><span>Retail overview</span><span className={loading ? "refresh-timer busy" : "refresh-timer"}>{loading && <LoaderCircle size={13} className="spin" />}{loading ? milliseconds(clock - started) : result ? milliseconds(result.elapsedMs) : "Not loaded"}</span></div>
    {error && <div className="error" role="alert">{error}</div>}
    <div className={loading || stale ? "retail-data stale" : "retail-data"} aria-busy={loading}>
      <div className="retail-kpis"><div><small>Revenue</small><strong>{totals ? currency(Number(totals.REVENUE)) : "--"}</strong></div><div><small>Orders</small><strong>{totals ? Number(totals.ORDERS).toLocaleString() : "--"}</strong></div><div><small>Units sold</small><strong>{totals ? Number(totals.UNITS).toLocaleString() : "--"}</strong></div></div>
      <div className="section-heading minor"><span>Daily revenue</span><small>{result ? REGIONS[result.filters.region - 1] : REGIONS[filters.region - 1]}</small></div>
      <div className="sales-chart">{daily.length ? daily.map((row: any) => <div key={row.DAY} title={`${row.DAY}: ${currency(Number(row.REVENUE))}`} style={{ height: `${Number(row.REVENUE) / maximum * 100}%` }} />) : <span className="placeholder">Awaiting Snowflake results</span>}</div>
      <div className="chart-axis"><span>Start of month</span><span>End of month</span></div>
      <div className="retail-bottom"><div><h4>Category mix</h4>{categories.map((row: any) => <div className="category" key={row.CATEGORY_ID}><span>{CATEGORIES[Number(row.CATEGORY_ID)]}</span><div><i style={{ width: `${Number(row.REVENUE) / categoryMax * 100}%` }} /></div></div>)}</div><div><h4>Top products</h4>{products.map((row: any) => <div className="product" key={row.PRODUCT_ID}><span>SKU {String(row.PRODUCT_ID).padStart(4, "0")}</span><strong>{currency(Number(row.REVENUE))}</strong></div>)}</div></div>
    </div>
    {(loading || stale) && <div className="freshness">{loading ? "Refreshing" : "Previous filter results"}</div>}
  </section>
}

export default function Home() {
  const [users, setUsers] = useState(50)
  const [duration, setDuration] = useState(30)
  const [thinkMs, setThinkMs] = useState(1000)
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS)
  const [refreshKey, setRefreshKey] = useState(0)
  const [error, setError] = useState("")
  const [pending, setPending] = useState(false)
  const [notice, setNotice] = useState("")
  const status = useQuery({ queryKey: ["runs"], queryFn: async () => { const response = await fetch("/api/demo"); if (!response.ok) throw new Error("Status unavailable"); return response.json() }, refetchInterval: 1000 })
  const config = useQuery({ queryKey: ["config"], queryFn: async () => { const response = await fetch("/api/demo?action=config"); const result = await response.json(); if (!response.ok) throw new Error(result.error); return result }, staleTime: 60_000 })
  const run = status.data?.runs?.[0]
  const active = Boolean(status.data?.active)
  const preparing = run?.status === "preparing"
  async function action(body: any) {
    setPending(true); setError(""); setNotice("")
    try { await post(body); await status.refetch(); if (body.action === "suspend") { setNotice("Both benchmark warehouses are suspended."); await config.refetch() } }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Request failed") }
    finally { setPending(false) }
  }
  async function download(id: string) {
    const response = await fetch(`/api/demo?action=export&id=${encodeURIComponent(id)}`)
    if (!response.ok) { setError("Export unavailable"); return }
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement("a"); link.href = url; link.download = `concurrency-${id}.json`; link.click(); URL.revokeObjectURL(url)
  }
  return <main>
    <header className="masthead"><div className="brand"><Activity size={23} /><span>SNOWFLAKE LABS</span><span className="divider" /><span className="muted">Interactive analytics</span></div><span className="live"><i /> Live Snowflake data</span></header>
    <div className="title-row"><div><div className="eyebrow">THE CONCURRENCY TEST</div><h1>Same data. Different response.</h1><p>XSMALL standard <span>vs</span> XSMALL interactive</p></div><div className="dataset"><strong>10M</strong><span>synthetic sales rows<br />one shared table</span></div></div>
    <section className="controls">
      <div className="control-group"><label><Users size={15} /> Virtual users / warehouse</label><div className="segments">{[1, 10, 25, 50, 100].map((count) => <button disabled={active} aria-pressed={users === count} key={count} onClick={() => setUsers(count)}>{count}</button>)}</div></div>
      <div className="control-group"><label htmlFor="duration">Duration</label><select id="duration" value={duration} disabled={active} onChange={(event) => setDuration(Number(event.target.value))}>{[15, 30, 60].map((seconds) => <option key={seconds} value={seconds}>{seconds} seconds</option>)}</select></div>
      <div className="control-group"><label htmlFor="pace">Think time / refresh</label><select id="pace" value={thinkMs} disabled={active} onChange={(event) => setThinkMs(Number(event.target.value))}><option value={0}>0s / continuous</option><option value={1000}>1 second</option><option value={3000}>3 seconds</option></select></div>
      <div className="run-actions"><button className="primary" disabled={active || pending} onClick={() => action({ action: "start", users, duration, thinkMs })}><Play size={16} /> Run comparison</button><button className="icon-button" aria-label="Stop benchmark" title="Stop benchmark" disabled={!active || pending} onClick={() => action({ action: "stop" })}><Square size={17} /></button><button className="icon-button" aria-label="Suspend both warehouses" title="Suspend both warehouses" disabled={active || pending} onClick={() => { if (window.confirm("Suspend both benchmark warehouses? Resuming interactive later incurs a new one-hour minimum charge.")) void action({ action: "suspend" }) }}><Power size={18} /></button></div>
    </section>
    <div className="run-status"><span><i className={active ? "pulse" : ""} />{preparing ? `Preparing sessions and warming queries: ${run.prepared.standard}/${users} standard, ${run.prepared.interactive}/${users} interactive` : run ? `${run.status.toUpperCase()} / ${run.elapsedSeconds.toFixed(1)}s / ${run.options.users} users per warehouse` : "Ready / single cluster / result cache off"}</span><span>Four queries per refresh · one in flight per virtual user</span></div>
    {(error || run?.error || status.error || config.error) && <div className="error" role="alert">{error || run?.error || status.error?.message || config.error?.message}</div>}
    {notice && <div className="notice">{notice}</div>}
    <div className="comparison">{SIDES.map((side) => {
      const metrics = run?.sides?.[side]
      const warehouse = config.data?.warehouses?.find((item: any) => item.name === (side === "standard" ? "DEMO_STANDARD_XS" : "DEMO_INTERACTIVE_XS"))
      return <section key={side} className={`warehouse ${side}`}>
        <div className="warehouse-heading"><div className="warehouse-title">{side === "interactive" ? <Zap size={22} /> : <Activity size={22} />}<div><h2>{side === "standard" ? "Standard" : "Interactive"}</h2><span>{warehouse?.name ?? (side === "standard" ? "DEMO_STANDARD_XS" : "DEMO_INTERACTIVE_XS")}</span></div></div><span className="size">XSMALL</span></div>
        <div className="warehouse-meta"><span>{warehouse?.type ?? side} {warehouse?.resource_constraint ?? ""}</span><span>1 cluster</span><span>Concurrency limit {config.data?.concurrency?.[side] ?? "--"}</span></div>
        <div className="headline"><div><small>Dashboard refresh p95</small><strong>{milliseconds(metrics?.refreshP95)}</strong><span>Successful four-query refreshes</span></div><div className="throughput"><small>Throughput</small><strong>{metrics ? metrics.qps.toFixed(1) : "--"}<em> q/s</em></strong><span>{metrics?.inFlight ?? 0} currently in flight</span></div></div>
        <Trace values={metrics?.recent ?? []} side={side} />
        <div className="metric-grid"><div><small>Query p50</small><strong>{milliseconds(metrics?.p50)}</strong></div><div><small>Query p95 / p99</small><strong>{milliseconds(metrics?.p95)} / {milliseconds(metrics?.p99)}</strong></div><div><small>Server queue p95</small><strong>{metrics?.historyCount ? milliseconds(metrics.queueP95) : "Pending"}</strong></div><div><small>Failures / finished</small><strong className={metrics?.errors ? "negative" : ""}>{metrics?.errors ?? 0} / {metrics?.completed ?? 0}</strong></div></div>
        <div className="evidence"><span>{metrics?.historyCount ?? 0} history records · {metrics?.queuedCount ?? 0} queued · {metrics?.timeouts ?? 0} timeouts</span><span>{metrics?.capped ? "4,000-query safety cap reached" : `${metrics?.refreshFailures ?? 0} incomplete refreshes`}</span></div>
        {metrics?.lastError && <div className="error">{metrics.lastError}</div>}
        <RetailPanel side={side} filters={filters} refreshKey={refreshKey} disabled={preparing} />
      </section>
    })}</div>
    <section className="filters"><span className="eyebrow">RETAIL FILTERS</span><select aria-label="Sales region" value={filters.region} onChange={(event) => setFilters({ ...filters, region: Number(event.target.value) })}>{REGIONS.map((region, index) => <option key={region} value={index + 1}>{region}</option>)}</select><select aria-label="Sales month" value={filters.month} onChange={(event) => setFilters({ ...filters, month: Number(event.target.value) })}>{Array.from({ length: 12 }, (_, index) => <option key={index} value={index + 1}>{new Date(2025, index, 1).toLocaleDateString("en-US", { month: "long", year: "numeric" })}</option>)}</select><button disabled={preparing} onClick={() => setRefreshKey((key) => key + 1)}><RefreshCw size={15} /> Refresh both</button></section>
    <section className="runs"><div className="section-heading"><h3>Run history</h3><span>Session-local · last 20 runs</span></div><div className="table-scroll"><table><thead><tr><th>Started</th><th>Users</th><th>Status</th><th>Standard p95</th><th>Interactive p95</th><th>Failures S / I</th><th /></tr></thead><tbody>{status.data?.runs?.map((item: any) => <tr key={item.id}><td>{new Date(item.createdAt).toLocaleTimeString()}</td><td>{item.options.users}</td><td>{item.status}</td><td>{milliseconds(item.sides.standard.refreshP95)}</td><td>{milliseconds(item.sides.interactive.refreshP95)}</td><td>{item.sides.standard.errors} / {item.sides.interactive.errors}</td><td><button className="icon-button" aria-label="Download run measurements" onClick={() => download(item.id)}><Download size={15} /></button></td></tr>)}</tbody></table></div>{!run && <div className="empty">No completed comparisons</div>}</section>
    {run?.historyError && <div className="error">Query-history reconciliation: {run.historyError}</div>}
    <footer><span>CONCURRENCY_DEMO_DB.DEMO.SALES · same SQL, same data, warm caches</span><a href="https://docs.snowflake.com/en/user-guide/interactive" target="_blank" rel="noreferrer">Interactive warehouses <ArrowUpRight size={13} /></a><span>Interactive: 5s limit · 1h minimum billing per resume · hosting billed separately</span></footer>
  </main>
}
