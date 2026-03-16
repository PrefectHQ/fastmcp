"use client";

import { useMemo, useState } from "react";
import type {
  PolicyConfig,
  PolicyGovernanceResponse,
  PolicyManagementResponse,
  PolicyProposalEvent,
  PolicyProposalItem,
  PolicyProviderItem,
  PolicySimulationScenario,
  PolicyVersionDiffResponse,
  PolicyVersionItem,
} from "@/lib/registryClient";

type PolicyTemplateName =
  | "allowlist"
  | "denylist"
  | "rbac"
  | "rate_limit"
  | "time_based";

type PolicyState = NonNullable<PolicyManagementResponse["policy"]>;
type PolicyVersionsState = NonNullable<PolicyManagementResponse["versions"]>;
type PolicyManagerData = Pick<
  PolicyManagementResponse,
  "policy" | "versions" | "governance" | "simulation_defaults"
>;

const POLICY_TEMPLATES: Record<PolicyTemplateName, PolicyConfig> = {
  allowlist: {
    type: "allowlist",
    policy_id: "allowlist-policy",
    version: "1.0.0",
    allowed: ["tool:*"],
  },
  denylist: {
    type: "denylist",
    policy_id: "denylist-policy",
    version: "1.0.0",
    denied: ["tool:admin-*"],
  },
  rbac: {
    type: "rbac",
    policy_id: "rbac-policy",
    version: "1.0.0",
    role_mappings: {
      admin: ["*"],
      reviewer: ["call_tool", "read_resource"],
    },
    default_decision: "deny",
  },
  rate_limit: {
    type: "rate_limit",
    policy_id: "rate-limit-policy",
    version: "1.0.0",
    max_requests: 200,
    window_seconds: 3600,
  },
  time_based: {
    type: "time_based",
    policy_id: "business-hours-policy",
    version: "1.0.0",
    allowed_days: [0, 1, 2, 3, 4],
    start_hour: 9,
    end_hour: 17,
    utc_offset_hours: 0,
  },
};

const TERMINAL_PROPOSAL_STATUSES = new Set([
  "deployed",
  "rejected",
  "withdrawn",
]);

function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "Unknown time";
  }

  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function proposalStatusLabel(status: string | undefined): string {
  switch (status) {
    case "draft":
      return "Draft";
    case "validated":
      return "Ready for approval";
    case "validation_failed":
      return "Needs fixes";
    case "simulated":
      return "Simulated";
    case "approved":
      return "Approved";
    case "deployed":
      return "Live";
    case "rejected":
      return "Rejected";
    case "withdrawn":
      return "Withdrawn";
    default:
      return "Proposal";
  }
}

function proposalStatusClass(status: string | undefined): string {
  switch (status) {
    case "validated":
    case "simulated":
    case "approved":
    case "deployed":
      return "bg-emerald-500/15 text-emerald-100 ring-emerald-400/60";
    case "validation_failed":
    case "rejected":
      return "bg-rose-500/15 text-rose-100 ring-rose-400/60";
    case "withdrawn":
      return "bg-zinc-500/15 text-zinc-100 ring-zinc-400/50";
    default:
      return "bg-amber-500/15 text-amber-100 ring-amber-400/60";
  }
}

function actionLabel(action: string | undefined): string {
  switch (action) {
    case "add":
      return "Add rule";
    case "swap":
      return "Change rule";
    case "remove":
      return "Remove rule";
    default:
      return "Policy change";
  }
}

function trailEventLabel(event: string | undefined): string {
  const raw = (event ?? "").trim();
  if (!raw) {
    return "Policy event";
  }

  return raw
    .split("_")
    .map((segment) =>
      segment ? `${segment[0].toUpperCase()}${segment.slice(1)}` : segment,
    )
    .join(" ");
}

export function PolicyManager({
  initialData,
  currentUsername,
}: {
  initialData: PolicyManagerData;
  currentUsername?: string | null;
}) {
  const policy: PolicyState = initialData?.policy ?? {};
  const versionsState: PolicyVersionsState = initialData?.versions ?? {};
  const governance: PolicyGovernanceResponse = initialData?.governance ?? {};
  const simulationDefaults: PolicySimulationScenario[] =
    initialData?.simulation_defaults ?? [];
  const versions = versionsState.versions ?? [];
  const providers = policy.providers ?? [];
  const proposals = governance.proposals ?? [];
  const currentVersion = versionsState.current_version ?? null;
  const sortedVersions = versions
    .slice()
    .sort((left, right) => right.version_number - left.version_number);
  const sortedProposals = proposals
    .slice()
    .sort((left, right) => {
      const rightTime = Date.parse(right.created_at ?? "") || 0;
      const leftTime = Date.parse(left.created_at ?? "") || 0;
      return rightTime - leftTime;
    });
  const activeProposals = sortedProposals.filter(
    (proposal) => !TERMINAL_PROPOSAL_STATUSES.has(proposal.status ?? ""),
  );
  const historyProposals = sortedProposals.filter((proposal) =>
    TERMINAL_PROPOSAL_STATUSES.has(proposal.status ?? ""),
  );
  const versionNumbers = sortedVersions.map((version) => version.version_number);
  const requireApproval = governance.require_approval !== false;
  const requireSimulation = governance.require_simulation === true;

  const [banner, setBanner] = useState<{ tone: "success" | "error"; message: string } | null>(
    null,
  );
  const [creating, setCreating] = useState(false);
  const [createDescription, setCreateDescription] = useState("");
  const [createTemplate, setCreateTemplate] = useState<PolicyTemplateName>("allowlist");
  const [createConfigText, setCreateConfigText] = useState(
    prettyJson(POLICY_TEMPLATES.allowlist),
  );
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editTexts, setEditTexts] = useState<Record<number, string>>({});
  const [editDescriptions, setEditDescriptions] = useState<Record<number, string>>({});
  const [proposalNotes, setProposalNotes] = useState<Record<string, string>>({});
  const [assignmentTargets, setAssignmentTargets] = useState<Record<string, string>>(
    {},
  );
  const [rollbackReason, setRollbackReason] = useState("");
  const [diffFrom, setDiffFrom] = useState<number | "">(
    versionNumbers[1] ?? versionNumbers[0] ?? "",
  );
  const [diffTo, setDiffTo] = useState<number | "">(versionNumbers[0] ?? "");
  const [diffLoading, setDiffLoading] = useState(false);
  const [versionDiff, setVersionDiff] = useState<PolicyVersionDiffResponse | null>(null);

  const stats = useMemo(
    () => [
      {
        label: "Live rules",
        value: String(policy.provider_count ?? providers.length ?? 0),
      },
      {
        label: "Live version",
        value: currentVersion ? `v${currentVersion}` : "Not versioned",
      },
      {
        label: "Saved versions",
        value: String(versions.length),
      },
      {
        label: "Pending changes",
        value: String(governance.pending_count ?? activeProposals.length ?? 0),
      },
      {
        label: "Stale drafts",
        value: String(governance.stale_count ?? 0),
      },
    ],
    [
      activeProposals.length,
      currentVersion,
      governance.pending_count,
      governance.stale_count,
      policy.provider_count,
      providers.length,
      versions.length,
    ],
  );

  function chooseTemplate(name: PolicyTemplateName) {
    setCreateTemplate(name);
    setCreateConfigText(prettyJson(POLICY_TEMPLATES[name]));
  }

  async function createProposal(payload: {
    action: "add" | "swap" | "remove";
    config?: PolicyConfig;
    targetIndex?: number;
    description: string;
  }) {
    const response = await fetch("/api/policy/proposals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: payload.action,
        config: payload.config,
        target_index: payload.targetIndex,
        description: payload.description,
      }),
    });
    const body = (await response.json().catch(() => ({}))) as { error?: string };
    if (!response.ok) {
      throw new Error(body.error ?? "Unable to create policy proposal.");
    }
  }

  async function handleCreateProposal() {
    setBanner(null);
    setCreating(true);
    try {
      const config = JSON.parse(createConfigText) as PolicyConfig;
      await createProposal({
        action: "add",
        config,
        description: createDescription,
      });
      setBanner({
        tone: "success",
        message: "Proposal created. Review it below before it goes live.",
      });
      window.location.reload();
    } catch (error) {
      setBanner({
        tone: "error",
        message:
          error instanceof Error
            ? error.message
            : "Unable to create policy proposal.",
      });
    } finally {
      setCreating(false);
    }
  }

  async function handleDraftEdit(index: number) {
    setBanner(null);
    setBusyKey(`draft-${index}`);
    try {
      const rawText = editTexts[index] ?? prettyJson(providers[index]?.config ?? {});
      const config = JSON.parse(rawText) as PolicyConfig;
      await createProposal({
        action: "swap",
        targetIndex: index,
        config,
        description: editDescriptions[index] ?? "",
      });
      setBanner({
        tone: "success",
        message: "Edit proposal created. It is waiting in the changes lane.",
      });
      window.location.reload();
    } catch (error) {
      setBanner({
        tone: "error",
        message:
          error instanceof Error
            ? error.message
            : "Unable to create edit proposal.",
      });
    } finally {
      setBusyKey(null);
    }
  }

  async function handleDraftRemoval(index: number) {
    const description =
      window.prompt("Why should this rule be removed?", "No longer needed.") ?? null;
    if (description === null) {
      return;
    }

    setBanner(null);
    setBusyKey(`remove-${index}`);
    try {
      await createProposal({
        action: "remove",
        targetIndex: index,
        description,
      });
      setBanner({
        tone: "success",
        message: "Removal proposal created. It will stay pending until applied.",
      });
      window.location.reload();
    } catch (error) {
      setBanner({
        tone: "error",
        message:
          error instanceof Error
            ? error.message
            : "Unable to create removal proposal.",
      });
    } finally {
      setBusyKey(null);
    }
  }

  async function handleApproveAndDeploy(proposal: PolicyProposalItem) {
    if (!proposal.proposal_id) {
      return;
    }

    const note = proposalNotes[proposal.proposal_id] ?? "";
    setBanner(null);
    setBusyKey(`approve-${proposal.proposal_id}`);
    try {
      if (proposal.status !== "approved") {
        if (requireSimulation && proposal.status !== "simulated") {
          throw new Error(
            "Run the proposal simulation before approving this change.",
          );
        }
        if (requireApproval) {
          const approveResponse = await fetch(
            `/api/policy/proposals/${proposal.proposal_id}/approve`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ note }),
            },
          );
          const approvePayload = (await approveResponse.json().catch(() => ({}))) as {
            error?: string;
          };
          if (!approveResponse.ok) {
            throw new Error(approvePayload.error ?? "Unable to approve proposal.");
          }
        }
      }

      const deployResponse = await fetch(
        `/api/policy/proposals/${proposal.proposal_id}/deploy`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note }),
        },
      );
      const deployPayload = (await deployResponse.json().catch(() => ({}))) as {
        error?: string;
      };
      if (!deployResponse.ok) {
        throw new Error(deployPayload.error ?? "Unable to apply proposal.");
      }

      setBanner({
        tone: "success",
        message: "Proposal applied to the live policy chain.",
      });
      window.location.reload();
    } catch (error) {
      setBanner({
        tone: "error",
        message:
          error instanceof Error ? error.message : "Unable to apply proposal.",
      });
    } finally {
      setBusyKey(null);
    }
  }

  async function handleSimulate(proposalId: string) {
    setBanner(null);
    setBusyKey(`simulate-${proposalId}`);
    try {
      const response = await fetch(`/api/policy/proposals/${proposalId}/simulate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenarios: simulationDefaults,
        }),
      });
      const payload = (await response.json().catch(() => ({}))) as { error?: string };
      if (!response.ok) {
        throw new Error(payload.error ?? "Unable to run proposal simulation.");
      }
      setBanner({
        tone: "success",
        message: "Simulation complete. Review the impact before you apply the change.",
      });
      window.location.reload();
    } catch (error) {
      setBanner({
        tone: "error",
        message:
          error instanceof Error
            ? error.message
            : "Unable to run proposal simulation.",
      });
    } finally {
      setBusyKey(null);
    }
  }

  async function handleReject(proposalId: string) {
    setBanner(null);
    setBusyKey(`reject-${proposalId}`);
    try {
      const response = await fetch(`/api/policy/proposals/${proposalId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reason: proposalNotes[proposalId] ?? "",
        }),
      });
      const payload = (await response.json().catch(() => ({}))) as { error?: string };
      if (!response.ok) {
        throw new Error(payload.error ?? "Unable to reject proposal.");
      }
      setBanner({
        tone: "success",
        message: "Proposal rejected.",
      });
      window.location.reload();
    } catch (error) {
      setBanner({
        tone: "error",
        message:
          error instanceof Error ? error.message : "Unable to reject proposal.",
      });
    } finally {
      setBusyKey(null);
    }
  }

  async function handleAssign(proposalId: string, reviewerOverride?: string) {
    const reviewer = (
      reviewerOverride ??
      assignmentTargets[proposalId] ??
      currentUsername ??
      ""
    ).trim();

    if (!reviewer) {
      setBanner({
        tone: "error",
        message: "Choose a reviewer username before assigning ownership.",
      });
      return;
    }

    setBanner(null);
    setBusyKey(`assign-${proposalId}`);
    try {
      const response = await fetch(`/api/policy/proposals/${proposalId}/assign`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reviewer,
          note: proposalNotes[proposalId] ?? "",
        }),
      });
      const payload = (await response.json().catch(() => ({}))) as { error?: string };
      if (!response.ok) {
        throw new Error(payload.error ?? "Unable to assign proposal.");
      }
      setBanner({
        tone: "success",
        message: `Proposal assigned to ${reviewer}.`,
      });
      window.location.reload();
    } catch (error) {
      setBanner({
        tone: "error",
        message:
          error instanceof Error ? error.message : "Unable to assign proposal.",
      });
    } finally {
      setBusyKey(null);
    }
  }

  async function handleWithdraw(proposalId: string) {
    setBanner(null);
    setBusyKey(`withdraw-${proposalId}`);
    try {
      const response = await fetch(`/api/policy/proposals/${proposalId}/withdraw`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          note: proposalNotes[proposalId] ?? "",
        }),
      });
      const payload = (await response.json().catch(() => ({}))) as { error?: string };
      if (!response.ok) {
        throw new Error(payload.error ?? "Unable to withdraw proposal.");
      }
      setBanner({
        tone: "success",
        message: "Proposal withdrawn.",
      });
      window.location.reload();
    } catch (error) {
      setBanner({
        tone: "error",
        message:
          error instanceof Error ? error.message : "Unable to withdraw proposal.",
      });
    } finally {
      setBusyKey(null);
    }
  }

  async function handleRollback(versionNumber: number) {
    const confirmed = window.confirm(
      `Roll back the live policy chain to version ${versionNumber}?`,
    );
    if (!confirmed) {
      return;
    }

    setBanner(null);
    setBusyKey(`rollback-${versionNumber}`);
    try {
      const response = await fetch("/api/policy/rollback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          version_number: versionNumber,
          reason: rollbackReason,
        }),
      });
      const payload = (await response.json().catch(() => ({}))) as { error?: string };
      if (!response.ok) {
        throw new Error(payload.error ?? "Unable to roll back policy version.");
      }
      setBanner({
        tone: "success",
        message: `Rolled back to version ${versionNumber}.`,
      });
      window.location.reload();
    } catch (error) {
      setBanner({
        tone: "error",
        message:
          error instanceof Error
            ? error.message
            : "Unable to roll back policy version.",
      });
    } finally {
      setBusyKey(null);
    }
  }

  async function handleLoadDiff() {
    if (diffFrom === "" || diffTo === "") {
      setBanner({
        tone: "error",
        message: "Pick two saved versions before comparing them.",
      });
      return;
    }

    setBanner(null);
    setDiffLoading(true);
    try {
      const response = await fetch(`/api/policy/diff?v1=${diffFrom}&v2=${diffTo}`, {
        cache: "no-store",
      });
      const payload = (await response.json().catch(() => ({}))) as PolicyVersionDiffResponse;
      if (!response.ok) {
        throw new Error(payload.error ?? "Unable to compare policy versions.");
      }
      setVersionDiff(payload);
    } catch (error) {
      setVersionDiff(null);
      setBanner({
        tone: "error",
        message:
          error instanceof Error
            ? error.message
            : "Unable to compare policy versions.",
      });
    } finally {
      setDiffLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {banner ? (
        <section
          className={`rounded-3xl p-4 ring-1 ${
            banner.tone === "success"
              ? "bg-emerald-900/40 text-emerald-50 ring-emerald-600/60"
              : "bg-rose-950/40 text-rose-50 ring-rose-700/60"
          }`}
        >
          <p className="text-[12px] font-medium">{banner.message}</p>
        </section>
      ) : null}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        {stats.map((item) => (
          <div
            key={item.label}
            className="rounded-3xl bg-emerald-900/40 p-4 ring-1 ring-emerald-700/60"
          >
            <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-emerald-300">
              {item.label}
            </p>
            <p className="mt-2 text-2xl font-semibold text-emerald-50">{item.value}</p>
          </div>
        ))}
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.2fr,0.8fr]">
        <div className="flex flex-col gap-4">
          <div className="rounded-3xl bg-emerald-900/40 p-5 ring-1 ring-emerald-700/60">
            <div className="flex flex-col gap-1">
              <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-emerald-300">
                Live policy chain
              </p>
              <h2 className="text-xl font-semibold text-emerald-50">
                See what is active right now
              </h2>
              <p className="max-w-2xl text-[11px] text-emerald-100/80">
                These rules are live today. Draft a change or removal first, then approve
                and apply it from the proposal lane.
              </p>
            </div>

            <div className="mt-5 flex flex-col gap-4">
              {providers.length === 0 ? (
                <div className="rounded-2xl bg-emerald-950/70 p-4 ring-1 ring-emerald-700/70">
                  <p className="text-[12px] text-emerald-100/90">
                    No providers are active right now. Start by drafting the first rule from
                    the starter panel.
                  </p>
                </div>
              ) : (
                providers.map((provider) => {
                  const isEditing = editingIndex === provider.index;
                  const editableText =
                    editTexts[provider.index] ?? prettyJson(provider.config ?? {});

                  return (
                    <article
                      key={provider.index}
                      className="rounded-2xl bg-emerald-950/70 p-4 ring-1 ring-emerald-700/70"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="space-y-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="rounded-full bg-emerald-900/80 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-emerald-200">
                              Step {provider.index + 1}
                            </span>
                            <span className="text-[12px] font-semibold text-emerald-50">
                              {provider.type}
                            </span>
                          </div>
                          <p className="text-[11px] text-emerald-100/90">{provider.summary}</p>
                          <p className="text-[10px] text-emerald-300/90">
                            Policy ID: {provider.policy_id ?? "n/a"} · Version:{" "}
                            {provider.policy_version ?? "n/a"}
                          </p>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() => {
                              setEditingIndex(isEditing ? null : provider.index);
                              setEditTexts((current) => ({
                                ...current,
                                [provider.index]: prettyJson(provider.config ?? {}),
                              }));
                            }}
                            disabled={!provider.editable}
                            className="rounded-full border border-emerald-600/80 px-3 py-1 text-[10px] font-semibold text-emerald-100 transition hover:bg-emerald-700/30 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            {isEditing
                              ? "Close draft"
                              : provider.editable
                                ? "Draft change"
                                : "Read only"}
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleDraftRemoval(provider.index)}
                            disabled={busyKey === `remove-${provider.index}`}
                            className="rounded-full border border-rose-500/80 px-3 py-1 text-[10px] font-semibold text-rose-100 transition hover:bg-rose-500/10 disabled:opacity-60"
                          >
                            {busyKey === `remove-${provider.index}`
                              ? "Drafting…"
                              : "Draft removal"}
                          </button>
                        </div>
                      </div>

                      {isEditing ? (
                        <div className="mt-4 flex flex-col gap-3">
                          <textarea
                            value={editableText}
                            onChange={(event) =>
                              setEditTexts((current) => ({
                                ...current,
                                [provider.index]: event.target.value,
                              }))
                            }
                            className="min-h-[220px] rounded-2xl border border-emerald-700/70 bg-emerald-950 px-4 py-3 font-mono text-[11px] leading-6 text-emerald-50 outline-none focus:border-emerald-400"
                          />
                          <input
                            value={editDescriptions[provider.index] ?? ""}
                            onChange={(event) =>
                              setEditDescriptions((current) => ({
                                ...current,
                                [provider.index]: event.target.value,
                              }))
                            }
                            placeholder="What should change and why?"
                            className="rounded-full border border-emerald-700/70 bg-emerald-950 px-4 py-2 text-[11px] text-emerald-50 outline-none focus:border-emerald-400"
                          />
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="button"
                              onClick={() => void handleDraftEdit(provider.index)}
                              disabled={busyKey === `draft-${provider.index}`}
                              className="rounded-full bg-emerald-500 px-4 py-2 text-[11px] font-semibold text-emerald-950 transition hover:bg-emerald-400 disabled:opacity-60"
                            >
                              {busyKey === `draft-${provider.index}`
                                ? "Saving draft…"
                                : "Create proposal"}
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </article>
                  );
                })
              )}
            </div>
          </div>

          <div className="rounded-3xl bg-emerald-900/40 p-5 ring-1 ring-emerald-700/60">
            <div className="space-y-1">
              <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-emerald-300">
                Proposal lane
              </p>
              <h2 className="text-xl font-semibold text-emerald-50">
                Review changes before they go live
              </h2>
              <p className="max-w-2xl text-[11px] text-emerald-100/80">
                Drafts land here first. Approve and apply ready proposals, or reject and
                withdraw them when they should not ship.
              </p>
            </div>

            {requireSimulation ? (
              <div className="mt-4 rounded-2xl bg-amber-500/10 p-4 ring-1 ring-amber-400/40">
                <p className="text-[11px] text-amber-100">
                  This workspace requires a quick simulation before approval. Each
                  proposal can be tested against the registry’s default access scenarios
                  before it is applied.
                </p>
              </div>
            ) : null}

            <div className="mt-4 flex flex-col gap-3">
              {activeProposals.length === 0 ? (
                <div className="rounded-2xl bg-emerald-950/70 p-4 ring-1 ring-emerald-700/70">
                  <p className="text-[12px] text-emerald-100/90">
                    No policy changes are waiting right now. Draft a change from the live
                    chain or starter panel.
                  </p>
                </div>
              ) : (
                activeProposals.map((proposal) => {
                  const proposalId = proposal.proposal_id ?? "";
                  const validationFindings = proposal.validation?.findings ?? [];
                  const simulationResults = proposal.simulation?.results ?? [];
                  const simulationSummary = proposal.simulation;
                  const assignmentValue =
                    assignmentTargets[proposalId] ??
                    proposal.assigned_reviewer ??
                    currentUsername ??
                    "";
                  const decisionTrail = (proposal.decision_trail ?? [])
                    .slice()
                    .reverse()
                    .slice(0, 4);
                  const canApplyDirectly =
                    !proposal.is_stale &&
                    !requireApproval &&
                    (proposal.status === "validated" || proposal.status === "simulated");
                  const canApproveAndApply =
                    !proposal.is_stale &&
                    requireApproval &&
                    (proposal.status === "validated" || proposal.status === "simulated");
                  const canDeploy = !proposal.is_stale && proposal.status === "approved";
                  const needsSimulation =
                    !proposal.is_stale &&
                    requireSimulation &&
                    proposal.status !== "simulated" &&
                    proposal.status !== "approved";

                  return (
                    <article
                      key={proposalId}
                      className="rounded-2xl bg-emerald-950/70 p-4 ring-1 ring-emerald-700/70"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="space-y-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <span
                              className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.16em] ring-1 ${proposalStatusClass(
                                proposal.status,
                              )}`}
                            >
                              {proposalStatusLabel(proposal.status)}
                            </span>
                            {proposal.is_stale ? (
                              <span className="rounded-full bg-rose-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-rose-100 ring-1 ring-rose-400/60">
                                Out of date
                              </span>
                            ) : null}
                            <span className="text-[12px] font-semibold text-emerald-50">
                              {actionLabel(proposal.action)}
                            </span>
                          </div>
                          <div className="space-y-1">
                            <p className="text-[11px] text-emerald-100/90">
                              {proposal.description || "No reason captured for this proposal."}
                            </p>
                            <p className="text-[10px] text-emerald-300/90">
                              Proposed by {proposal.author ?? "unknown"} ·{" "}
                              {formatTimestamp(proposal.created_at)}
                            </p>
                            <p className="text-[10px] text-emerald-300/90">
                              Owner: {proposal.assigned_reviewer ?? "Unassigned"}
                            </p>
                            <p className="text-[10px] text-emerald-300/90">
                              Drafted for v{proposal.base_version_number ?? "?"} · live v
                              {proposal.live_version_number ?? "?"}
                            </p>
                            {proposal.provider?.summary ? (
                              <p className="text-[10px] text-emerald-200/90">
                                Draft: {proposal.provider.summary}
                              </p>
                            ) : null}
                            {proposal.target_index !== null &&
                            proposal.target_index !== undefined ? (
                              <p className="text-[10px] text-emerald-200/90">
                                Applies to step {proposal.target_index + 1}
                              </p>
                            ) : null}
                          </div>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() =>
                              void handleAssign(
                                proposalId,
                                currentUsername ?? undefined,
                              )
                            }
                            disabled={
                              !currentUsername ||
                              busyKey === `assign-${proposalId}` ||
                              proposal.assigned_reviewer === currentUsername
                            }
                            className="rounded-full border border-emerald-600/80 px-3 py-1 text-[10px] font-semibold text-emerald-100 transition hover:bg-emerald-700/30 disabled:opacity-60"
                          >
                            {busyKey === `assign-${proposalId}`
                              ? "Assigning…"
                              : proposal.assigned_reviewer === currentUsername
                                ? "Assigned to you"
                                : "Assign to me"}
                          </button>
                          {needsSimulation ? (
                            <button
                              type="button"
                              onClick={() => void handleSimulate(proposalId)}
                              disabled={busyKey === `simulate-${proposalId}`}
                              className="rounded-full border border-amber-400/80 px-3 py-1 text-[10px] font-semibold text-amber-100 transition hover:bg-amber-400/10 disabled:opacity-60"
                            >
                              {busyKey === `simulate-${proposalId}`
                                ? "Running…"
                                : "Run simulation"}
                            </button>
                          ) : null}
                          {canApproveAndApply ? (
                            <button
                              type="button"
                              onClick={() => void handleApproveAndDeploy(proposal)}
                              disabled={busyKey === `approve-${proposalId}`}
                              className="rounded-full bg-emerald-500 px-3 py-1 text-[10px] font-semibold text-emerald-950 transition hover:bg-emerald-400 disabled:opacity-60"
                            >
                              {busyKey === `approve-${proposalId}`
                                ? "Applying…"
                                : "Approve & apply"}
                            </button>
                          ) : null}
                          {canApplyDirectly || canDeploy ? (
                            <button
                              type="button"
                              onClick={() => void handleApproveAndDeploy(proposal)}
                              disabled={busyKey === `approve-${proposalId}`}
                              className="rounded-full bg-emerald-500 px-3 py-1 text-[10px] font-semibold text-emerald-950 transition hover:bg-emerald-400 disabled:opacity-60"
                            >
                              {busyKey === `approve-${proposalId}`
                                ? "Applying…"
                                : "Apply live"}
                            </button>
                          ) : null}
                          <button
                            type="button"
                            onClick={() => void handleWithdraw(proposalId)}
                            disabled={busyKey === `withdraw-${proposalId}`}
                            className="rounded-full border border-emerald-600/80 px-3 py-1 text-[10px] font-semibold text-emerald-100 transition hover:bg-emerald-700/30 disabled:opacity-60"
                          >
                            {busyKey === `withdraw-${proposalId}`
                              ? "Withdrawing…"
                              : "Withdraw"}
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleReject(proposalId)}
                            disabled={busyKey === `reject-${proposalId}`}
                            className="rounded-full border border-rose-500/80 px-3 py-1 text-[10px] font-semibold text-rose-100 transition hover:bg-rose-500/10 disabled:opacity-60"
                          >
                            {busyKey === `reject-${proposalId}`
                              ? "Rejecting…"
                              : "Reject"}
                          </button>
                        </div>
                      </div>

                      {proposal.is_stale ? (
                        <div className="mt-3 rounded-2xl bg-rose-500/10 p-3 ring-1 ring-rose-400/40">
                          <p className="text-[11px] text-rose-100">
                            This proposal was drafted against version{" "}
                            {proposal.base_version_number ?? "?"}, but the live policy
                            chain is now on version {proposal.live_version_number ?? "?"}.
                            Create a fresh proposal from the current chain before you
                            simulate or apply it.
                          </p>
                        </div>
                      ) : null}

                      <div className="mt-3 grid gap-3 lg:grid-cols-[0.95fr,1fr,0.9fr]">
                        <div className="rounded-2xl bg-emerald-900/20 p-3 ring-1 ring-emerald-700/40">
                          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-emerald-300">
                            Ownership
                          </p>
                          <p className="mt-2 text-[11px] text-emerald-100/90">
                            {proposal.assigned_reviewer
                              ? `Currently owned by ${proposal.assigned_reviewer}.`
                              : "No owner yet. Assign someone before final approval if you want a clear reviewer."}
                          </p>
                          <div className="mt-3 flex flex-col gap-2">
                            <input
                              value={assignmentValue}
                              onChange={(event) =>
                                setAssignmentTargets((current) => ({
                                  ...current,
                                  [proposalId]: event.target.value,
                                }))
                              }
                              placeholder="Reviewer username"
                              className="w-full rounded-full border border-emerald-700/70 bg-emerald-950 px-4 py-2 text-[11px] text-emerald-50 outline-none focus:border-emerald-400"
                            />
                            <div className="flex flex-wrap gap-2">
                              <button
                                type="button"
                                onClick={() => void handleAssign(proposalId)}
                                disabled={busyKey === `assign-${proposalId}`}
                                className="rounded-full border border-emerald-600/80 px-3 py-1 text-[10px] font-semibold text-emerald-100 transition hover:bg-emerald-700/30 disabled:opacity-60"
                              >
                                {busyKey === `assign-${proposalId}`
                                  ? "Assigning…"
                                  : "Save owner"}
                              </button>
                              {currentUsername ? (
                                <button
                                  type="button"
                                  onClick={() =>
                                    setAssignmentTargets((current) => ({
                                      ...current,
                                      [proposalId]: currentUsername,
                                    }))
                                  }
                                  className="rounded-full border border-emerald-700/70 px-3 py-1 text-[10px] font-semibold text-emerald-100 transition hover:bg-emerald-700/20"
                                >
                                  Fill with my username
                                </button>
                              ) : null}
                            </div>
                          </div>
                        </div>

                        <div className="rounded-2xl bg-emerald-900/20 p-3 ring-1 ring-emerald-700/40">
                          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-emerald-300">
                            Review note
                          </p>
                          <input
                            value={proposalNotes[proposalId] ?? ""}
                            onChange={(event) =>
                              setProposalNotes((current) => ({
                                ...current,
                                [proposalId]: event.target.value,
                              }))
                            }
                            placeholder="Optional note for reject or follow-up"
                            className="mt-2 w-full rounded-full border border-emerald-700/70 bg-emerald-950 px-4 py-2 text-[11px] text-emerald-50 outline-none focus:border-emerald-400"
                          />
                        </div>

                        <div className="rounded-2xl bg-emerald-900/20 p-3 ring-1 ring-emerald-700/40">
                          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-emerald-300">
                            Validation
                          </p>
                          <p className="mt-2 text-[11px] text-emerald-100/90">
                            {proposal.validation?.valid === false
                              ? "This proposal needs fixes before it can be approved."
                              : "This proposal is structurally ready to move forward."}
                          </p>
                          {validationFindings.length > 0 ? (
                            <ul className="mt-2 space-y-1 text-[10px] text-emerald-200/90">
                              {validationFindings.slice(0, 3).map((finding, index) => (
                                <li key={`${proposalId}-finding-${index}`}>
                                  {finding.severity?.toUpperCase()}: {finding.message}
                                </li>
                              ))}
                              {validationFindings.length > 3 ? (
                                <li>
                                  +{validationFindings.length - 3} more validation findings
                                </li>
                              ) : null}
                            </ul>
                          ) : null}
                        </div>
                      </div>

                      {decisionTrail.length > 0 ? (
                        <div className="mt-3 rounded-2xl bg-emerald-900/20 p-3 ring-1 ring-emerald-700/40">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-emerald-300">
                              Decision trail
                            </p>
                            <p className="text-[10px] text-emerald-200/90">
                              {proposal.decision_trail?.length ?? 0} recorded steps
                            </p>
                          </div>
                          <ol className="mt-3 space-y-2">
                            {decisionTrail.map((event: PolicyProposalEvent, index) => (
                              <li
                                key={`${proposalId}-trail-${event.created_at ?? index}`}
                                className="rounded-2xl bg-emerald-950/60 p-3 ring-1 ring-emerald-700/30"
                              >
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                  <p className="text-[11px] font-semibold text-emerald-50">
                                    {trailEventLabel(event.event)}
                                  </p>
                                  <p className="text-[10px] text-emerald-300/90">
                                    {formatTimestamp(event.created_at)}
                                  </p>
                                </div>
                                <p className="mt-1 text-[10px] text-emerald-300/90">
                                  {event.actor ?? "unknown"}
                                </p>
                                {event.note ? (
                                  <p className="mt-2 text-[11px] text-emerald-100/90">
                                    {event.note}
                                  </p>
                                ) : null}
                              </li>
                            ))}
                          </ol>
                        </div>
                      ) : null}

                      {simulationSummary ? (
                        <div className="mt-3 rounded-2xl bg-emerald-900/20 p-3 ring-1 ring-emerald-700/40">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-emerald-300">
                              Simulation
                            </p>
                            <p className="text-[10px] text-emerald-200/90">
                              {simulationSummary.allowed ?? 0} allowed ·{" "}
                              {simulationSummary.denied ?? 0} denied ·{" "}
                              {simulationSummary.errors ?? 0} errors
                            </p>
                          </div>
                          <p className="mt-2 text-[11px] text-emerald-100/90">
                            Tested against {simulationSummary.total ?? 0} registry access
                            scenarios.
                          </p>
                          {simulationResults.length > 0 ? (
                            <ul className="mt-2 space-y-1 text-[10px] text-emerald-200/90">
                              {simulationResults.slice(0, 4).map((result, index) => (
                                <li key={`${proposalId}-simulation-${index}`}>
                                  {(result.label || result.resource_id || "Scenario")}:{" "}
                                  {(result.decision || "unknown").toUpperCase()} ·{" "}
                                  {result.reason || "No reason captured."}
                                </li>
                              ))}
                              {simulationResults.length > 4 ? (
                                <li>
                                  +{simulationResults.length - 4} more simulated outcomes
                                </li>
                              ) : null}
                            </ul>
                          ) : null}
                        </div>
                      ) : null}
                    </article>
                  );
                })
              )}
            </div>

            {historyProposals.length > 0 ? (
              <div className="mt-5 rounded-2xl bg-emerald-950/60 p-4 ring-1 ring-emerald-700/60">
                <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-emerald-300">
                  Recent decisions
                </p>
                <div className="mt-3 flex flex-col gap-2">
                  {historyProposals.slice(0, 4).map((proposal) => (
                    <div
                      key={`history-${proposal.proposal_id ?? "unknown"}`}
                      className="rounded-2xl bg-emerald-900/20 p-3 ring-1 ring-emerald-700/30"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.16em] ring-1 ${proposalStatusClass(
                            proposal.status,
                          )}`}
                        >
                          {proposalStatusLabel(proposal.status)}
                        </span>
                        <span className="text-[11px] font-semibold text-emerald-50">
                          {actionLabel(proposal.action)}
                        </span>
                      </div>
                      <p className="mt-2 text-[11px] text-emerald-100/90">
                        {proposal.description || "No description recorded."}
                      </p>
                      <p className="mt-1 text-[10px] text-emerald-300/90">
                        {proposal.status === "deployed"
                          ? `Went live ${formatTimestamp(proposal.deployed_at)}`
                          : proposal.status === "rejected"
                            ? `Rejected ${proposal.rejection_reason ? `— ${proposal.rejection_reason}` : ""}`
                            : `Withdrawn ${formatTimestamp(proposal.created_at)}`}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>

          <div className="rounded-3xl bg-emerald-900/40 p-5 ring-1 ring-emerald-700/60">
            <div className="space-y-1">
              <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-emerald-300">
                Version history
              </p>
              <h2 className="text-xl font-semibold text-emerald-50">
                Roll back with confidence
              </h2>
              <p className="max-w-2xl text-[11px] text-emerald-100/80">
                Every live apply creates a saved version of the policy chain. Roll back when
                a change needs to be reversed quickly.
              </p>
            </div>

            <input
              value={rollbackReason}
              onChange={(event) => setRollbackReason(event.target.value)}
              placeholder="Optional rollback reason"
              className="mt-4 w-full rounded-full border border-emerald-700/70 bg-emerald-950 px-4 py-2 text-[11px] text-emerald-50 outline-none focus:border-emerald-400"
            />

            <div className="mt-4 flex flex-col gap-3">
              {versions.length === 0 ? (
                <div className="rounded-2xl bg-emerald-950/70 p-4 ring-1 ring-emerald-700/70">
                  <p className="text-[12px] text-emerald-100/90">
                    No saved versions yet. The first live policy change will create one
                    automatically.
                  </p>
                </div>
              ) : (
                sortedVersions.map((version) => {
                  const isCurrent = version.version_number === currentVersion;
                  return (
                    <article
                      key={version.version_id}
                      className="rounded-2xl bg-emerald-950/70 p-4 ring-1 ring-emerald-700/70"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="space-y-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-[12px] font-semibold text-emerald-50">
                              Version {version.version_number}
                            </span>
                            {isCurrent ? (
                              <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-emerald-100">
                                Live now
                              </span>
                            ) : null}
                          </div>
                          <p className="text-[11px] text-emerald-100/90">
                            {version.description || "No description recorded."}
                          </p>
                          <p className="text-[10px] text-emerald-300/90">
                            Saved by {version.author || "unknown"} ·{" "}
                            {formatTimestamp(version.created_at)}
                          </p>
                        </div>
                        {!isCurrent ? (
                          <button
                            type="button"
                            onClick={() => void handleRollback(version.version_number)}
                            disabled={busyKey === `rollback-${version.version_number}`}
                            className="rounded-full border border-emerald-600/80 px-3 py-1 text-[10px] font-semibold text-emerald-100 transition hover:bg-emerald-700/30 disabled:opacity-60"
                          >
                            {busyKey === `rollback-${version.version_number}`
                              ? "Rolling back…"
                              : "Roll back"}
                          </button>
                        ) : null}
                      </div>
                    </article>
                  );
                })
              )}
            </div>
          </div>

          <div className="rounded-3xl bg-emerald-900/40 p-5 ring-1 ring-emerald-700/60">
            <div className="space-y-1">
              <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-emerald-300">
                Version diff
              </p>
              <h2 className="text-xl font-semibold text-emerald-50">See what changed</h2>
              <p className="max-w-2xl text-[11px] text-emerald-100/80">
                Compare two saved versions before you roll back or stage another change.
              </p>
            </div>

            {versionNumbers.length < 2 ? (
              <div className="mt-4 rounded-2xl bg-emerald-950/70 p-4 ring-1 ring-emerald-700/70">
                <p className="text-[12px] text-emerald-100/90">
                  You need at least two saved versions before comparison is useful.
                </p>
              </div>
            ) : (
              <>
                <div className="mt-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                  <label className="flex flex-col gap-1 text-[11px] text-emerald-100/90">
                    From version
                    <select
                      value={diffFrom}
                      onChange={(event) => setDiffFrom(Number(event.target.value))}
                      className="rounded-2xl border border-emerald-700/70 bg-emerald-950 px-4 py-2 text-[11px] text-emerald-50 outline-none focus:border-emerald-400"
                    >
                      {versionNumbers.map((versionNumber) => (
                        <option key={`from-${versionNumber}`} value={versionNumber}>
                          Version {versionNumber}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="flex flex-col gap-1 text-[11px] text-emerald-100/90">
                    To version
                    <select
                      value={diffTo}
                      onChange={(event) => setDiffTo(Number(event.target.value))}
                      className="rounded-2xl border border-emerald-700/70 bg-emerald-950 px-4 py-2 text-[11px] text-emerald-50 outline-none focus:border-emerald-400"
                    >
                      {versionNumbers.map((versionNumber) => (
                        <option key={`to-${versionNumber}`} value={versionNumber}>
                          Version {versionNumber}
                        </option>
                      ))}
                    </select>
                  </label>

                  <button
                    type="button"
                    onClick={() => void handleLoadDiff()}
                    disabled={diffLoading}
                    className="self-end rounded-full bg-emerald-500 px-4 py-2 text-[11px] font-semibold text-emerald-950 transition hover:bg-emerald-400 disabled:opacity-60"
                  >
                    {diffLoading ? "Comparing…" : "Compare versions"}
                  </button>
                </div>

                <div className="mt-4 rounded-2xl bg-emerald-950/70 p-4 ring-1 ring-emerald-700/70">
                  {versionDiff?.diff ? (
                    <pre className="max-h-[320px] overflow-auto whitespace-pre-wrap break-words text-[11px] leading-6 text-emerald-50">
                      {prettyJson(versionDiff.diff)}
                    </pre>
                  ) : (
                    <p className="text-[12px] text-emerald-100/90">
                      Choose two versions to inspect the saved diff before you act on it.
                    </p>
                  )}
                </div>
              </>
            )}
          </div>
        </div>

        <aside className="flex flex-col gap-4">
          <div className="rounded-3xl bg-emerald-900/40 p-5 ring-1 ring-emerald-700/60">
            <div className="space-y-1">
              <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-emerald-300">
                New proposal
              </p>
              <h2 className="text-xl font-semibold text-emerald-50">
                Start from a policy template
              </h2>
              <p className="text-[11px] text-emerald-100/80">
                Pick a starter, adjust the JSON, and create a proposal that reviewers can
                approve before it goes live.
              </p>
            </div>

            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {(Object.keys(POLICY_TEMPLATES) as PolicyTemplateName[]).map((templateName) => (
                <button
                  key={templateName}
                  type="button"
                  onClick={() => chooseTemplate(templateName)}
                  className={`rounded-2xl px-3 py-3 text-left text-[11px] ring-1 transition ${
                    createTemplate === templateName
                      ? "bg-emerald-500/15 text-emerald-50 ring-emerald-400/70"
                      : "bg-emerald-950/70 text-emerald-100 ring-emerald-700/70 hover:bg-emerald-900/60"
                  }`}
                >
                  <span className="block font-semibold capitalize">
                    {templateName.replaceAll("_", " ")}
                  </span>
                  <span className="mt-1 block text-[10px] text-emerald-200/80">
                    {templateName === "allowlist"
                      ? "Allow only named tools"
                      : templateName === "denylist"
                        ? "Block named tools"
                        : templateName === "rbac"
                          ? "Map roles to actions"
                          : templateName === "rate_limit"
                            ? "Control request volume"
                            : "Restrict by time window"}
                  </span>
                </button>
              ))}
            </div>

            <textarea
              value={createConfigText}
              onChange={(event) => setCreateConfigText(event.target.value)}
              className="mt-4 min-h-[280px] w-full rounded-2xl border border-emerald-700/70 bg-emerald-950 px-4 py-3 font-mono text-[11px] leading-6 text-emerald-50 outline-none focus:border-emerald-400"
            />

            <input
              value={createDescription}
              onChange={(event) => setCreateDescription(event.target.value)}
              placeholder="What change should this proposal make?"
              className="mt-3 w-full rounded-full border border-emerald-700/70 bg-emerald-950 px-4 py-2 text-[11px] text-emerald-50 outline-none focus:border-emerald-400"
            />

            <button
              type="button"
              onClick={() => void handleCreateProposal()}
              disabled={creating}
              className="mt-4 rounded-full bg-emerald-500 px-4 py-2 text-[11px] font-semibold text-emerald-950 transition hover:bg-emerald-400 disabled:opacity-60"
            >
              {creating ? "Creating proposal…" : "Create proposal"}
            </button>
          </div>
        </aside>
      </section>
    </div>
  );
}
