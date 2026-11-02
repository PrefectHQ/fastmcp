// Same-origin proxies for the Amplitude browser SDK, copied from the Prefect
// marketing site: the browser never talks to Amplitude's hosts directly.

const EVENTS_TARGET = "https://api2.amplitude.com/2/httpapi";
const REMOTE_CONFIG_TARGET = "https://sr-client-cfg.amplitude.com/config";
const MAX_BODY_BYTES = 10 * 1024 * 1024;

function problem(status: number, message: string) {
  return Response.json({ error: message }, { status });
}

// The body is buffered rather than streamed: payloads are small JSON batches,
// and a streamed body plus a forwarded Content-Length fails under Node's
// fetch in local development.
async function forward(request: Request, target: string, allowedHeaders: string[]) {
  const headers = new Headers();
  for (const header of allowedHeaders) {
    const value = request.headers.get(header);
    if (value) headers.set(header, value);
  }

  const response = await fetch(target, {
    method: request.method,
    headers,
    body: request.method === "GET" ? undefined : await request.text(),
  });

  const filtered = new Headers();
  response.headers.forEach((value, header) => {
    if (!header.startsWith("access-control-")) filtered.append(header, value);
  });
  return new Response(response.body, { status: response.status, headers: filtered });
}

/** POST /api/t: event ingestion, Amplitude HTTP V2. */
export async function proxyEvents(request: Request): Promise<Response> {
  try {
    if (!request.headers.get("content-type")?.includes("application/json")) {
      return problem(400, "Content-Type must be application/json");
    }
    const length = request.headers.get("content-length");
    if (length !== null && Number(length) > MAX_BODY_BYTES) {
      return problem(413, "Request body too large");
    }
    return await forward(request, EVENTS_TARGET, ["content-type", "accept"]);
  } catch (error) {
    console.error("Amplitude proxy error:", error instanceof Error ? error.message : error);
    return problem(500, "Failed to process analytics request");
  }
}

/** GET /api/sr-cfg/<api-key>/...: remote config for autocapture. */
export async function proxyRemoteConfig(request: Request, path: string[]): Promise<Response> {
  try {
    const url = new URL(request.url);
    const query = url.searchParams.toString();
    const suffix = path.map(encodeURIComponent).join("/");
    const target = `${REMOTE_CONFIG_TARGET}${suffix ? `/${suffix}` : ""}${query ? `?${query}` : ""}`;
    return await forward(request, target, ["accept"]);
  } catch (error) {
    console.error("Amplitude config proxy error:", error instanceof Error ? error.message : error);
    return problem(500, "Failed to process analytics request");
  }
}
