import { NextResponse, type NextRequest } from "next/server";
import { DOCS_ORIGIN } from "@/lib/docs-proxy";

// Mintlify loads its client assets and API from the domain root, and 404s
// those calls unless Origin names its own host. These paths cannot be plain
// rewrites in next.config.ts because of that header, so they are proxied here.
export const config = {
  matcher: ["/_mintlify/:path*", "/api/request"],
};

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const path = pathname === "/api/request" ? "/_mintlify/api/request" : pathname;
  const headers = new Headers(request.headers);
  headers.set("origin", DOCS_ORIGIN);
  headers.delete("host");
  return NextResponse.rewrite(new URL(path + search, DOCS_ORIGIN), { request: { headers } });
}
