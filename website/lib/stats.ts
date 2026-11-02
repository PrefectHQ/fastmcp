// Live project numbers for the homepage. Every source is public and
// unauthenticated; each one falls back to a recent known value so a rate
// limit or outage never blanks the page. Cached for an hour through Next's
// fetch cache (the page itself revalidates on the same schedule).

export interface ProjectStats {
  stars: number;
  forks: number;
  contributors: number;
  /** PyPI downloads in the last 30 days. */
  monthlyDownloads: number;
  /** Latest release tag on GitHub, e.g. "v4.1.5". */
  latestRelease: string;
}

// Known values as of 2026-11-02, used when a source is unreachable.
export const FALLBACK_STATS: ProjectStats = {
  stars: 28_000,
  forks: 2_500,
  contributors: 360,
  monthlyDownloads: 30_000_000,
  latestRelease: "v4.1.5",
};

const REVALIDATE_SECONDS = 3600;
const USER_AGENT = "gofastmcp.com";

function init(accept: string): RequestInit & { next: { revalidate: number } } {
  return {
    headers: { Accept: accept, "User-Agent": USER_AGENT },
    next: { revalidate: REVALIDATE_SECONDS },
  };
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url, init("application/json"));
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json() as Promise<T>;
}

// GitHub has no contributor count field; the paginated endpoint's Link
// header names the last page, which with one item per page is the count.
async function fetchContributorCount(): Promise<number> {
  const response = await fetch(
    "https://api.github.com/repos/PrefectHQ/fastmcp/contributors?per_page=1&anon=true",
    init("application/vnd.github+json"),
  );
  if (!response.ok) throw new Error(`contributors: ${response.status}`);
  const last = response.headers.get("link")?.match(/[?&]page=(\d+)>; rel="last"/);
  if (!last?.[1]) throw new Error("contributors: no last page");
  return Number(last[1]);
}

// pepy's badge reports lifetime-adjusted monthly downloads for the package
// and is the number the Prefect site shows; pypistats is the fallback.
async function fetchMonthlyDownloads(): Promise<number> {
  try {
    const response = await fetch("https://static.pepy.tech/badge/fastmcp/month", init("image/svg+xml"));
    if (!response.ok) throw new Error(`pepy: ${response.status}`);
    const svg = await response.text();
    const values = [...svg.matchAll(/<text[^>]*>([\d.]+)\s*([kKmM]?)<\/text>/g)].reverse();
    for (const match of values) {
      const value = Number(match[1]);
      if (!Number.isFinite(value)) continue;
      const suffix = (match[2] ?? "").toLowerCase();
      return Math.round(value * (suffix === "m" ? 1_000_000 : suffix === "k" ? 1_000 : 1));
    }
    throw new Error("pepy: no value");
  } catch {
    const data = await fetchJson<{ data: { last_month: number } }>(
      "https://pypistats.org/api/packages/fastmcp/recent",
    );
    return data.data.last_month;
  }
}

export async function getProjectStats(): Promise<ProjectStats> {
  const [repo, contributors, downloads, release] = await Promise.allSettled([
    fetchJson<{ stargazers_count: number; forks_count: number }>(
      "https://api.github.com/repos/PrefectHQ/fastmcp",
    ),
    fetchContributorCount(),
    fetchMonthlyDownloads(),
    fetchJson<{ tag_name: string }>("https://api.github.com/repos/PrefectHQ/fastmcp/releases/latest"),
  ]);

  return {
    stars: repo.status === "fulfilled" ? repo.value.stargazers_count : FALLBACK_STATS.stars,
    forks: repo.status === "fulfilled" ? repo.value.forks_count : FALLBACK_STATS.forks,
    contributors:
      contributors.status === "fulfilled" ? contributors.value : FALLBACK_STATS.contributors,
    monthlyDownloads:
      downloads.status === "fulfilled" ? downloads.value : FALLBACK_STATS.monthlyDownloads,
    latestRelease: release.status === "fulfilled" ? release.value.tag_name : FALLBACK_STATS.latestRelease,
  };
}

// 28275 -> "28.3k", 31400000 -> "31M", 363 -> "360+"
export function formatCount(value: number): string {
  if (value >= 1_000_000) return `${trim((value / 1_000_000).toFixed(1))}M`;
  if (value >= 10_000) return `${trim((value / 1_000).toFixed(1))}k`;
  if (value >= 1_000) return `${trim((value / 1_000).toFixed(1))}k`;
  return `${Math.floor(value / 10) * 10}+`;
}

function trim(fixed: string) {
  return fixed.replace(/\.0$/, "");
}
