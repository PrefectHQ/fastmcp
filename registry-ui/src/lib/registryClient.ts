import { cookies } from "next/headers";

const DEFAULT_BACKEND_URL = "http://localhost:8000";

export type RegistryPayload = Record<string, unknown>;
export type PolicyConfig = Record<string, unknown>;

export type RegistryErrorResponse = {
  error?: string;
  status?: number;
};

export type RegistrySessionInfo = {
  username?: string;
  role?: string;
  display_name?: string;
  expires_at?: string;
  can_submit?: boolean;
  can_review?: boolean;
  can_admin?: boolean;
};

export type RegistrySessionResponse = {
  auth_enabled?: boolean;
  session?: RegistrySessionInfo | null;
};

export type RegistryDataFlow = {
  source?: string;
  destination?: string;
  classification?: string;
  description?: string;
};

export type RegistryManifestSummary = {
  data_flows?: RegistryDataFlow[];
};

export type RegistryToolListing = {
  tool_name: string;
  display_name?: string;
  description?: string;
  categories?: string[];
  certification_level?: string;
  publisher_id?: string;
  version?: string;
  author?: string;
  manifest?: RegistryManifestSummary | null;
};

export type RegistryToolCatalogResponse = RegistryErrorResponse & {
  count?: number;
  tools?: RegistryToolListing[];
  minimum_certification?: string;
  generated_at?: string;
};

export type InstallRecipe = {
  recipe_id: string;
  title?: string;
  content?: string;
};

export type InstallRecipesResponse = RegistryErrorResponse & {
  tool_name?: string;
  recipes?: InstallRecipe[];
};

export type ToolVerificationDetail = {
  signature_valid?: boolean;
  manifest_match?: boolean;
  issues?: string[];
};

export type ToolVerificationResponse = RegistryErrorResponse & {
  verification?: ToolVerificationDetail;
};

export type PublisherTrustScore = {
  overall?: number;
};

export type PublisherSummary = {
  publisher_id: string;
  display_name?: string;
  summary?: string;
  description?: string;
  tool_count?: number;
  verified_tool_count?: number;
  trust_score?: PublisherTrustScore | null;
};

export type PublisherDirectoryResponse = RegistryErrorResponse & {
  count?: number;
  publishers?: PublisherSummary[];
  generated_at?: string;
};

export type PublisherProfileResponse = RegistryErrorResponse & {
  summary?: PublisherSummary;
  listings?: RegistryToolListing[];
};

export type ReviewLogEntry = {
  action?: string;
  moderator_id?: string;
  reason?: string;
};

export type ReviewQueueItem = {
  listing_id: string;
  display_name?: string;
  tool_name: string;
  version?: string;
  certification_level?: string;
  description?: string;
  moderation_log?: ReviewLogEntry[];
  available_actions?: string[];
};

export type ReviewQueueResponse = RegistryErrorResponse & {
  sections?: Record<string, ReviewQueueItem[]>;
};

export type RegistryHealthResponse = RegistryErrorResponse & {
  status?: string;
  minimum_certification?: string;
  require_moderation?: boolean;
  auth_enabled?: boolean;
  issuer_id?: string;
  registered_tools?: number;
  verified_tools?: number;
  pending_review?: number;
  timestamp?: string;
  server?: string;
};

export type PolicyProviderItem = {
  index: number;
  type: string;
  policy_id?: string | null;
  policy_version?: string | null;
  editable: boolean;
  summary: string;
  config: PolicyConfig;
};

export type PolicyVersionItem = {
  version_id: string;
  version_number: number;
  description?: string;
  author?: string;
  created_at?: string;
};

export type PolicyGovernanceSummary = {
  enabled?: boolean;
  proposal_count?: number;
  pending_count?: number;
  require_simulation?: boolean;
  require_approval?: boolean;
};

export type PolicyVersioningSummary = {
  enabled?: boolean;
  policy_set_id?: string;
  version_count?: number;
  current_version?: number | null;
};

export type PolicyState = RegistryErrorResponse & {
  evaluation_count?: number;
  deny_count?: number;
  fail_closed?: boolean;
  allow_hot_swap?: boolean;
  provider_count?: number;
  providers?: PolicyProviderItem[];
  has_audit_log?: boolean;
  versioning?: PolicyVersioningSummary | null;
  governance?: PolicyGovernanceSummary | null;
  generated_at?: string;
};

export type PolicyVersionsResponse = RegistryErrorResponse & {
  policy_set_id?: string;
  version_count?: number;
  current_version?: number | null;
  versions?: PolicyVersionItem[];
  generated_at?: string;
};

export type PolicyVersionDiffResponse = RegistryErrorResponse & {
  v1?: number;
  v2?: number;
  diff?: Record<string, unknown>;
};

export type PolicySchemaResponse = RegistryErrorResponse & {
  definitions?: Record<string, unknown>;
  properties?: Record<string, unknown>;
};

export type PolicyValidationFinding = {
  severity?: string;
  message?: string;
  path?: string;
  code?: string;
};

export type PolicyValidationSummary = {
  valid?: boolean;
  error_count?: number;
  warning_count?: number;
  findings?: PolicyValidationFinding[];
};

export type PolicyProposalEvent = {
  event?: string;
  actor?: string;
  note?: string;
  created_at?: string;
};

export type PolicyProposalItem = {
  proposal_id?: string;
  action?: string;
  author?: string;
  description?: string;
  base_version_number?: number | null;
  live_version_number?: number | null;
  is_stale?: boolean;
  assigned_reviewer?: string | null;
  status?: string;
  created_at?: string;
  approved_by?: string | null;
  approved_at?: string | null;
  deployed_at?: string | null;
  rejection_reason?: string | null;
  target_index?: number | null;
  new_provider_type?: string;
  validation?: PolicyValidationSummary;
  provider?: PolicyProviderItem;
  simulation?: PolicySimulationReport;
  decision_trail?: PolicyProposalEvent[];
};

export type PolicyGovernanceResponse = RegistryErrorResponse & {
  total_proposals?: number;
  pending_count?: number;
  require_simulation?: boolean;
  require_approval?: boolean;
  current_version?: number | null;
  stale_count?: number;
  proposals?: PolicyProposalItem[];
  generated_at?: string;
};

export type PolicySimulationScenarioResult = {
  resource_id?: string;
  action?: string;
  actor_id?: string;
  label?: string;
  decision?: string;
  reason?: string;
  policy_id?: string;
  error?: string | null;
};

export type PolicySimulationReport = {
  total?: number;
  allowed?: number;
  denied?: number;
  deferred?: number;
  errors?: number;
  created_at?: string;
  results?: PolicySimulationScenarioResult[];
};

export type PolicySimulationScenario = {
  label?: string;
  resource_id?: string;
  action?: string;
  actor_id?: string;
  metadata?: Record<string, unknown>;
  tags?: string[];
};

export type PolicyManagementResponse = RegistryErrorResponse & {
  policy?: PolicyState;
  versions?: PolicyVersionsResponse;
  schema?: PolicySchemaResponse;
  governance?: PolicyGovernanceResponse;
  simulation_defaults?: PolicySimulationScenario[];
  generated_at?: string;
};

function backendBase() {
  return process.env.REGISTRY_BACKEND_URL ?? DEFAULT_BACKEND_URL;
}

async function parseJson<T>(response: Response): Promise<T | null> {
  return (await response.json().catch(() => null)) as T | null;
}

async function backendFetch(path: string, init?: RequestInit) {
  const cookieStore = await cookies();
  const sessionCookie = cookieStore.get("purecipher_registry_token");

  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");

  if (sessionCookie) {
    headers.set("cookie", `${sessionCookie.name}=${sessionCookie.value}`);
  }

  const response = await fetch(`${backendBase()}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });

  return response;
}

export async function getRegistrySession(): Promise<RegistrySessionResponse | null> {
  const response = await backendFetch("/registry/session");
  if (!response.ok) {
    return null;
  }
  return parseJson<RegistrySessionResponse>(response);
}

export async function listVerifiedTools(): Promise<RegistryToolCatalogResponse | null> {
  const response = await backendFetch("/registry/tools");
  if (!response.ok) {
    return null;
  }
  return parseJson<RegistryToolCatalogResponse>(response);
}

export async function getToolDetail(toolName: string): Promise<RegistryToolListing | RegistryErrorResponse | null> {
  const response = await backendFetch(`/registry/tools/${encodeURIComponent(toolName)}`);
  return parseJson<RegistryToolListing | RegistryErrorResponse>(response);
}

export async function getInstallRecipes(toolName: string): Promise<InstallRecipesResponse | null> {
  const response = await backendFetch(`/registry/install/${encodeURIComponent(toolName)}`);
  return parseJson<InstallRecipesResponse>(response);
}

export async function listPublishers(): Promise<PublisherDirectoryResponse | null> {
  const response = await backendFetch("/registry/publishers");
  return parseJson<PublisherDirectoryResponse>(response);
}

export async function getPublisherProfile(
  publisherId: string,
): Promise<PublisherProfileResponse | null> {
  const response = await backendFetch(`/registry/publishers/${encodeURIComponent(publisherId)}`);
  return parseJson<PublisherProfileResponse>(response);
}

export async function getReviewQueue(): Promise<ReviewQueueResponse | null> {
  const response = await backendFetch("/registry/review/submissions");
  return parseJson<ReviewQueueResponse>(response);
}

export async function requirePublisherRole(): Promise<{ allowed: boolean; role: string | null }> {
  const session = await getRegistrySession();
  const role = session?.session?.role ?? null;
  const allowed = role === "publisher" || role === "reviewer" || role === "admin";
  return { allowed, role };
}

export async function requirePolicyRole(): Promise<{ allowed: boolean; role: string | null }> {
  const session = await getRegistrySession();
  const role = session?.session?.role ?? null;
  const allowed = role === "reviewer" || role === "admin";
  return { allowed, role };
}

export async function getRegistryHealth(): Promise<RegistryHealthResponse | null> {
  const response = await backendFetch("/registry/health");
  if (!response.ok) {
    return null;
  }
  return parseJson<RegistryHealthResponse>(response);
}

export async function getPolicyManagement(): Promise<PolicyManagementResponse | null> {
  const response = await backendFetch("/registry/policy");
  return parseJson<PolicyManagementResponse>(response);
}

export async function getPolicyVersions(): Promise<PolicyVersionsResponse | null> {
  const response = await backendFetch("/registry/policy/versions");
  return parseJson<PolicyVersionsResponse>(response);
}

export async function verifyTool(toolName: string): Promise<ToolVerificationResponse | null> {
  const response = await backendFetch("/registry/verify", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ tool_name: toolName }),
  });
  return parseJson<ToolVerificationResponse>(response);
}
