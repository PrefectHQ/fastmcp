import Link from "next/link";
import {
  getPolicyManagement,
  getRegistrySession,
  requirePolicyRole,
} from "@/lib/registryClient";
import { PolicyManager } from "./PolicyManager";

export default async function PolicyPage() {
  const { allowed } = await requirePolicyRole();
  const sessionPayload = await getRegistrySession();
  const username = sessionPayload?.session?.username ?? null;

  if (!allowed) {
    return (
      <main className="min-h-screen bg-emerald-950/95 px-4 py-10 text-sm text-emerald-50">
        <div className="mx-auto max-w-3xl rounded-3xl bg-emerald-900/40 p-6 ring-1 ring-emerald-700/60">
          <h1 className="text-xl font-semibold text-emerald-50">Policy management</h1>
          <p className="mt-2 text-[12px] text-emerald-100/90">
            Reviewer or admin role required to manage live SecureMCP policies.
          </p>
          <p className="mt-4">
            <Link
              href="/registry/app"
              className="text-[11px] font-medium text-emerald-200 hover:text-emerald-100"
            >
              ← Back to tools
            </Link>
          </p>
        </div>
      </main>
    );
  }

  const policyData = await getPolicyManagement();

  return (
    <main className="min-h-screen bg-emerald-950/95 px-4 py-10 text-sm text-emerald-50">
      <div className="mx-auto flex max-w-6xl flex-col gap-6">
        <header className="space-y-1">
          <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-emerald-300">
            Policy management
          </p>
          <h1 className="text-2xl font-semibold text-emerald-50">
            Propose, review, and apply SecureMCP rules
          </h1>
          <p className="max-w-2xl text-[11px] text-emerald-100/80">
            Draft changes first, run a quick simulation, approve them before they go live,
            and keep every draft tied to the live policy version it came from.
          </p>
        </header>

        <PolicyManager
          initialData={policyData ?? {}}
          currentUsername={username}
        />
      </div>
    </main>
  );
}
