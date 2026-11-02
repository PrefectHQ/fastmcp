import type { Metadata, Viewport } from "next";
import { DM_Mono } from "next/font/google";
import type { ReactNode } from "react";
import { Analytics } from "@/components/analytics";
import { SITE_URL } from "@/lib/links";
import "./globals.css";

const dmMono = DM_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-dm-mono",
  display: "swap",
});

const TITLE = "FastMCP: The framework for MCP";
const DESCRIPTION =
  "Build MCP servers, clients and interactive apps in ordinary Python or TypeScript. FastMCP handles schemas, validation, transport and auth, so your code stays about your application.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: TITLE,
    template: "%s | FastMCP",
  },
  description: DESCRIPTION,
  alternates: { canonical: "/" },
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: "website",
    siteName: "FastMCP",
    url: SITE_URL,
    images: [{ url: "/brand/og.png", width: 1200, height: 630, alt: "FastMCP" }],
  },
  twitter: {
    card: "summary_large_image",
    site: "@fastmcp",
    title: TITLE,
    description: DESCRIPTION,
    images: ["/brand/og.png"],
  },
  icons: {
    icon: "/brand/favicon-dark.svg",
  },
};

export const viewport: Viewport = {
  themeColor: "#06000e",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={dmMono.variable}>
      <body className="flex min-h-screen flex-col bg-background font-sans text-foreground antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4 focus:z-[100] focus:rounded-md focus:bg-foreground focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-background"
        >
          Skip to content
        </a>
        {children}
        <Analytics />
      </body>
    </html>
  );
}
