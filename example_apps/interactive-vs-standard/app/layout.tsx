import type { Metadata } from "next"
import type React from "react"
import { QueryProvider } from "@/components/query-provider"
import "./globals.css"

export const metadata: Metadata = {
  title: "The Concurrency Test | Snowflake",
  description: "Live standard and interactive warehouse concurrency comparison",
  icons: { icon: "/icon.svg" },
}
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><QueryProvider>{children}</QueryProvider></body></html>
}
