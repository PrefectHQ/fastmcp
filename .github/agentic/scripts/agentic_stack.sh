#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${PREFECT_API_URL:?Set the Prefect Cloud workspace API URL}"
[[ $PREFECT_API_URL == https://api.prefect.cloud/api/accounts/*/workspaces/* ]] || {
  echo "Expected a Prefect Cloud workspace API URL" >&2; exit 2;
}
unset PREFECT_API_AUTH_STRING
api() { uv run --locked prefect --no-prompt api "$@" </dev/null; }
upsert() {
  local kind=$1 body=$2 name id
  name=$(jq -r .name "$body")
  jq -n '{limit:200}' > "$body.filter"
  id=$(api POST "/$kind/filter" --data "@$body.filter" | jq -r --arg name "$name" 'map(select(.name==$name)) | if length>1 then error("duplicate \($name)") else (.[0].id // "") end')
  if [[ -n $id ]]; then api PUT "/$kind/$id" --data "@$body" >/dev/null; echo "$id"
  else api POST "/$kind/" --data "@$body" | jq -er .id; fi
}

case "${1:-}" in
  validate)
    for role in assess reproduce prepare review; do
      api POST /execution-plans/validate --data "@workflows/fastmcp-stack-$role.plan.json" | jq -e '.valid == true'
    done
    ;;
  register)
    [[ -z $(git status --porcelain) ]] || {
      echo 'Commit local changes before registration' >&2; exit 1;
    }
    revision=$(git rev-parse HEAD)
    published=$(gh api "repos/PrefectHQ/fastmcp/commits/$revision" --jq .sha)
    [[ $published == "$revision" ]] || {
      echo 'Source commit must be available in PrefectHQ/fastmcp' >&2; exit 1;
    }
    FASTMCP_AGENTIC_REF=$revision uv run --locked prefect --no-prompt deploy \
      --prefect-file prefect.yaml \
      --name cloud-stack-batch --name cloud-stack-validation --name cloud-contribution-verification --name cloud-triage-intake \
      --name cloud-triage-verify --name cloud-triage-finalize
    ;;
  intake)
    config=${2:?intake configuration JSON file required}
    runtime=${3:?pinned runtime JSON file required}
    controller=${4:?managed controller deployment UUID required}
    deployment=${5:?managed intake deployment UUID required}
    operation=${6:?stable operation id required; reuse after an uncertain response}
    [[ $controller =~ ^[0-9a-f-]{36}$ && $deployment =~ ^[0-9a-f-]{36}$ ]] || exit 2
    [[ $operation =~ ^[a-z0-9-]{1,64}$ ]] || exit 2
    scratch=$(mktemp -d)
    trap 'rm -rf "$scratch"' EXIT
    jq -n --slurpfile config "$config" --slurpfile runtime "$runtime" \
      --arg controller "$controller" --arg operation "$operation" \
      '{name:$operation,idempotency_key:("fastmcp-intake-"+$operation),parameters:{config:$config[0],runtime:$runtime[0],controller_id:$controller}}' > "$scratch/request.json"
    api POST "/deployments/$deployment/create_flow_run" --data "@$scratch/request.json"
    ;;
  run)
    batch=${2:?batch JSON file required}
    runtime=${3:?pinned runtime JSON file required}
    deployment=${4:?managed stack controller deployment UUID required}
    [[ $deployment =~ ^[0-9a-f-]{36}$ ]] || exit 2
    scratch=$(mktemp -d)
    trap 'rm -rf "$scratch"' EXIT
    jq -e '.id | test("^[a-z0-9-]{1,40}$")' "$batch" >/dev/null
    jq -n --slurpfile batch "$batch" --slurpfile runtime "$runtime" \
      '{name:("stack-"+$batch[0].id),idempotency_key:("fastmcp-stack-"+$batch[0].id),parameters:{batch:$batch[0],runtime:$runtime[0]}}' \
      > "$scratch/request.json"
    api POST "/deployments/$deployment/create_flow_run" --data "@$scratch/request.json"
    ;;
  results)
    run=${2:?controller run UUID required}
    [[ $run =~ ^[0-9a-f-]{36}$ ]] || exit 2
    scratch=$(mktemp -d)
    trap 'rm -rf "$scratch"' EXIT
    api GET "/flow_runs/$run" > "$scratch/run.json"
    jq '{id,state_type,state_name,parameters:{batch:.parameters.batch}}' "$scratch/run.json"
    key=$(jq -er '.parameters.batch.id + "-review-bins"' "$scratch/run.json")
    jq -n --arg run "$run" --arg key "$key" \
      '{artifacts:{key:{any_:[$key]}},flow_runs:{id:{any_:[$run]}},limit:2}' > "$scratch/filter.json"
    api POST /artifacts/filter --data "@$scratch/filter.json" | jq -e 'if length==1 then .[0].data else error("Final review bins not available; inspect the run and retained stage artifacts") end'
    ;;
  events)
    runtime=${2:?pinned runtime JSON file required}
    controller=${3:?managed controller deployment UUID required}
    deployment=${4:?managed intake deployment UUID required}
    notify=${5:-}
    [[ $controller =~ ^[0-9a-f-]{36}$ && $deployment =~ ^[0-9a-f-]{36}$ ]] || exit 2
    [[ -z $notify || $notify =~ ^[0-9a-f-]{36}$ ]] || exit 2
    scratch=$(mktemp -d)
    trap 'rm -rf "$scratch"' EXIT
    jq -n --rawfile template workflows/events/github-webhook-template.json.j2 \
      '{name:"fastmcp-triage-github",description:"PrefectHQ/fastmcp issue and pull request events for agentic triage",enabled:true,template:$template}' > "$scratch/webhook.json"
    webhook=$(upsert webhooks "$scratch/webhook.json")
    echo "webhook $webhook"
    for kind in issue pr; do
      jq -n --slurpfile runtime "$runtime" --arg controller "$controller" --arg deployment "$deployment" --arg kind "$kind" '
        ($kind == "issue") as $issue |
        {name:("fastmcp-triage-on-github-" + $kind),
         description:(if $issue then "Consider changed FastMCP issues for bounded triage" else "Refresh issues referenced by ready FastMCP PRs; this does not review the PR itself" end),
         enabled:true,
         trigger:{type:"event",posture:"Reactive",threshold:1,within:60,
           expect:(if $issue then ["fastmcp.github.issues.opened","fastmcp.github.issues.reopened","fastmcp.github.issues.edited"]
                   else ["fastmcp.github.pull_request.opened","fastmcp.github.pull_request.reopened","fastmcp.github.pull_request.synchronize","fastmcp.github.pull_request.edited","fastmcp.github.pull_request.ready_for_review"] end),
           match:({"github.repository":"PrefectHQ/fastmcp","github.kind":$kind,"github.sender.type":"User"}
             + if $issue then {} else {"github.draft":"false","github.issue-reference":"true","github.relevant-change":"true"} end),
           match_related:{},after:[],for_each:["prefect.resource.id"]},
         actions:[{type:"run-deployment",source:"selected",deployment_id:$deployment,job_variables:null,schedule_after:60,
           parameters:{controller_id:$controller,runtime:$runtime[0],
             config:({queue:"events",max_items:3} + {(if $issue then "issues" else "pull_requests" end):
               {"__prefect_kind":"json",value:{"__prefect_kind":"jinja",template:"[{{ event.resource[\"github.number\"]|int }}]"}}})}}],
         actions_on_trigger:[],actions_on_resolve:[]}' > "$scratch/automation-$kind.json"
      echo "automation $kind $(upsert automations "$scratch/automation-$kind.json")"
    done
    if [[ -n $notify ]]; then
      jq -n --arg controller "$controller" --arg notify "$notify" '
        {name:"fastmcp-triage-run-started",description:"Announce issues admitted to the triage controller; empty intake runs stay quiet",enabled:true,
         trigger:{type:"event",posture:"Reactive",threshold:1,within:0,expect:["prefect.flow-run.Running"],
           match:{"prefect.resource.id":"prefect.flow-run.*","prefect.resource.name":"fastmcp-stack-intake-*"},
           match_related:{"prefect.resource.role":"deployment","prefect.resource.id":("prefect.deployment." + $controller)},
           after:[],for_each:["prefect.resource.id"]},
         actions:[{type:"send-notification",block_document_id:$notify,
           subject:"",
           body:"Accepted for triage: {% set ns = namespace(sep=\"\") %}{% for group in flow_run.parameters.batch.groups %}{% for item in group[\"items\"] %}{{ ns.sep }}<https://github.com/PrefectHQ/fastmcp/issues/{{ item.issue_number }}|#{{ item.issue_number }}>{% set ns.sep = \", \" %}{% endfor %}{% endfor %}\n<{{ flow_run|ui_url }}|Follow triage>"}],
         actions_on_trigger:[],actions_on_resolve:[]}' > "$scratch/automation-notify.json"
      echo "automation notify $(upsert automations "$scratch/automation-notify.json")"
    fi
    ;;
  alerts)
    runtime=${2:?pinned runtime JSON file required}
    notify=${3:?notification block document UUID required}
    [[ $notify =~ ^[0-9a-f-]{36}$ ]] || exit 2
    scratch=$(mktemp -d)
    trap 'rm -rf "$scratch"' EXIT
    jq -n '{deployments:{name:{any_:["cloud-triage-intake"]}},limit:2}' > "$scratch/filter.json"
    api POST /deployments/filter --data "@$scratch/filter.json" > "$scratch/deployments.json"
    jq -e 'length == 1' "$scratch/deployments.json" >/dev/null || {
      echo 'Expected exactly one intake deployment' >&2; exit 1;
    }
    jq -n --slurpfile deployments "$scratch/deployments.json" --rawfile body workflows/events/failure-notification.txt.j2 --arg notify "$notify" '
      ([$deployments[0][].id | "prefect.deployment." + .]) as $scope |
      {name:"fastmcp-triage-failure-alert",
       description:"Notify once per failed or degraded intake; child failures are retained in the intake review queue",
       enabled:true,
       trigger:{type:"event",posture:"Reactive",threshold:1,within:0,
         expect:["prefect.flow-run.Failed","prefect.flow-run.Crashed","prefect.flow-run.TimedOut","prefect.flow-run.Degraded"],
         match:{"prefect.resource.id":"prefect.flow-run.*"},
         match_related:{"prefect.resource.role":"deployment","prefect.resource.id":$scope},
         after:[],for_each:["prefect.resource.id"]},
       actions:[{type:"send-notification",block_document_id:$notify,
         subject:"",body:$body}],
       actions_on_trigger:[],actions_on_resolve:[]}' > "$scratch/automation-alert.json"
    echo "automation alert $(upsert automations "$scratch/automation-alert.json")"
    ;;
  *)
    echo 'Usage: agentic_stack.sh validate | register | intake CONFIG RUNTIME CONTROLLER DEPLOYMENT OPERATION | run BATCH RUNTIME DEPLOYMENT | results RUN | events RUNTIME CONTROLLER DEPLOYMENT [NOTIFY_BLOCK] | alerts RUNTIME NOTIFY_BLOCK' >&2
    exit 2
    ;;
esac
