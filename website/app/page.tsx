import { Footer } from "@/components/footer";
import { Nav } from "@/components/nav";
import { Agents } from "@/components/sections/agents";
import { Clients } from "@/components/sections/clients";
import { Community } from "@/components/sections/community";
import { DeployCta } from "@/components/sections/deploy-cta";
import { Features } from "@/components/sections/features";
import { GetStarted } from "@/components/sections/get-started";
import { Hero } from "@/components/sections/hero";
import { Pillars } from "@/components/sections/pillars";
import { formatCount, getProjectStats } from "@/lib/stats";
import { GITHUB, SITE_URL } from "@/lib/links";

// Stars, downloads and the release tag refresh hourly.
export const revalidate = 3600;

export default async function HomePage() {
  const stats = await getProjectStats();

  const schema = {
    "@context": "https://schema.org",
    "@type": "SoftwareSourceCode",
    name: "FastMCP",
    description:
      "The framework for building Model Context Protocol servers, clients and interactive apps in Python.",
    url: SITE_URL,
    codeRepository: GITHUB,
    programmingLanguage: "Python",
    license: "https://www.apache.org/licenses/LICENSE-2.0",
    author: { "@type": "Organization", name: "Prefect", url: "https://www.prefect.io" },
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(schema) }} />
      <Nav stars={formatCount(stats.stars)} />
      <main id="main" className="flex-1">
        <Hero stats={stats} />
        <Clients />
        <Features />
        <Community stats={stats} />
        <Pillars />
        <Agents />
        <GetStarted />
        <DeployCta />
      </main>
      <Footer />
    </>
  );
}
