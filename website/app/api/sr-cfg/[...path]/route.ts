import { proxyRemoteConfig } from "@/lib/analytics/proxy";

export const runtime = "edge";

export async function GET(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return proxyRemoteConfig(request, path);
}
