#!/usr/bin/env bash
set -euo pipefail
issue=${1:?issue number required}
[[ $issue =~ ^[1-9][0-9]*$ ]] || exit 2
base_ref=${2:-main}
[[ $base_ref == main || $base_ref == release/3.x || $base_ref == release/2.x ]] || exit 2
search_mode=${3:-route}
[[ $search_mode == route || $search_mode == publication ]] || exit 2
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
repo=PrefectHQ/fastmcp
api() {
  local attempt
  for attempt in 1 2 3; do
    if gh api "$@" > "$scratch/response.json"; then
      cat "$scratch/response.json"
      return
    fi
    sleep "$attempt"
  done
  return 1
}
pages() {
  local endpoint=$1 selection=${2:-.} page count separator='?'
  [[ $endpoint != *'?'* ]] || separator='&'
  printf '[]\n' > "$scratch/pages.json"
  for page in $(seq 1 10); do
    api "$endpoint${separator}per_page=100&page=$page" | jq "$selection" > "$scratch/page.json"
    count=$(jq -er 'if type=="array" then length else error("Expected page") end' "$scratch/page.json")
    jq -s '.[0]+.[1]' "$scratch/pages.json" "$scratch/page.json" > "$scratch/next.json"
    mv "$scratch/next.json" "$scratch/pages.json"
    if ((count < 100)); then cat "$scratch/pages.json"; return; fi
  done
  echo 'Evidence pagination exceeded 1000 records; refusing incomplete intake' >&2
  return 1
}
api "repos/$repo/issues/$issue" | jq '{number,title,body,state,html_url,updated_at,author_association,user:(.user|{id,login,type}),assignees:[.assignees[].login],labels:[.labels[].name],pull_request}' > "$scratch/issue.json"
jq -e '.pull_request == null' "$scratch/issue.json" >/dev/null
pages "repos/$repo/issues/$issue/comments" | jq '[.[]|{id,body,html_url,created_at,updated_at,author_association,user:(.user|{id,login,type})}]' > "$scratch/comments.json"
pages "repos/$repo/issues/$issue/timeline" | jq '[.[]|{id,event,created_at,source:(.source|if .==null then null else {type,issue:(.issue|if .==null then null else {number,title,state,html_url,pull_request:(.pull_request!=null),repository:{full_name:.repository.full_name}} end)} end),label,assignee,actor:(.actor|{id,login,type})}]' > "$scratch/timeline.json"
pages "repos/$repo/labels" | jq '[.[]|{name,description}]' > "$scratch/labels.json"
pages "repos/$repo/stacks" > "$scratch/stacks.json"
api "repos/$repo/commits/$base_ref" | jq -er '.sha' > "$scratch/base-sha"
printf '[]\n' > "$scratch/reactions.json"
while IFS= read -r comment; do
  pages "repos/$repo/issues/comments/$comment/reactions" | jq --argjson comment "$comment" '[.[]|{comment_id:$comment,content,user:(.user|{id,login,type})}]' > "$scratch/reaction-page.json"
  jq -s '.[0]+.[1]' "$scratch/reactions.json" "$scratch/reaction-page.json" > "$scratch/next.json"
  mv "$scratch/next.json" "$scratch/reactions.json"
done < <(jq -r '.[]|select(.user.type=="Bot" and (.body|ascii_downcase|contains("possible duplicate issues")))|.id' "$scratch/comments.json")
jq -rn --slurpfile issue "$scratch/issue.json" --arg number "$issue" '
  "repo:PrefectHQ/fastmcp is:pr " + $number + " in:body",
  ($issue[0].title | gsub("\\[Bug\\]|\\[bug\\]";"") |
    [scan("[A-Za-z_][A-Za-z0-9_]{3,}") | select((ascii_downcase | IN("fastmcp","with","when","from","that","does","doesn","have","this","into","after","before","without","using","support","fails","returns","error"))|not)] |
    unique | sort_by(-length) | .[:6] | map("\"" + . + "\"") | join(" ") |
    if length==0 then empty else "repo:PrefectHQ/fastmcp is:pr " + . end)
' > "$scratch/queries"
printf '[]\n' > "$scratch/searches.json"
printf '[]\n' > "$scratch/matches.json"
while IFS= read -r query; do
  api -X GET search/issues -f q="$query" -f per_page=100 > "$scratch/search.json"
  jq -e '.incomplete_results == false and .total_count <= 100' "$scratch/search.json" >/dev/null || {
    echo "PR search incomplete: $query" >&2; exit 1;
  }
  jq --arg query "$query" --slurpfile result "$scratch/search.json" '. + [{query:$query,total_count:$result[0].total_count,complete:true}]' "$scratch/searches.json" > "$scratch/next.json"
  mv "$scratch/next.json" "$scratch/searches.json"
  jq -s --arg number "$issue" --argjson by_number "$([[ $query == *" is:pr $issue in:body" ]] && echo true || echo false)" '
    .[0] + [.[1].items[] | select(($by_number | not) or ((.body // "") | test("(#|/issues/|/pull/)" + $number + "(?![0-9])")))]
    | unique_by(.number)' "$scratch/matches.json" "$scratch/search.json" > "$scratch/next.json"
  mv "$scratch/next.json" "$scratch/matches.json"
  if [[ $search_mode == route ]] && jq -e --arg number "$issue" 'any(.items[]; (.body // "") | test("(?i)(fix(es|ed)?|close[sd]?|resolve[sd]?)\\s+#"+$number+"(?![0-9])"))' "$scratch/search.json" >/dev/null; then
    break
  fi
done < "$scratch/queries"
jq -nr --slurpfile timeline "$scratch/timeline.json" '
  [$timeline[0][] | .source.issue | select(.pull_request and (.repository.full_name == "PrefectHQ/fastmcp" or (.html_url|startswith("https://github.com/PrefectHQ/fastmcp/pull/")))) | .number] | unique[]
' > "$scratch/numbers"
jq -r --arg number "$issue" '.[] | select((.body // "") | test("(#|/issues/|/pull/)" + $number + "(?![0-9])")) | .number' "$scratch/matches.json" >> "$scratch/numbers"
sort -nu "$scratch/numbers" > "$scratch/linked"
jq -r '.[].number' "$scratch/matches.json" >> "$scratch/numbers"
sort -nu "$scratch/numbers" > "$scratch/unique-numbers"
if (($(wc -l < "$scratch/unique-numbers") > 30)); then
  echo 'More than 30 PR candidates; bounded matching requires narrower investigation' >&2
  exit 1
fi
printf '[]\n' > "$scratch/prs.json"
while IFS= read -r number; do
  [[ -n $number ]] || continue
  api "repos/$repo/pulls/$number" | jq '{number,title,body,state,draft,merged,html_url,updated_at,user:(.user|{id,login,type}),labels:[.labels[].name],base_ref:.base.ref,base_sha:.base.sha,head_ref:.head.ref,head_sha:.head.sha,head_repository:.head.repo.full_name}' > "$scratch/pr.json"
  if [[ $search_mode == route ]] && ! grep -qx "$number" "$scratch/linked"; then
    jq -s '.[0]+[.[1]+{discussion_collected:false}]' "$scratch/prs.json" "$scratch/pr.json" > "$scratch/next.json"
    mv "$scratch/next.json" "$scratch/prs.json"
    continue
  fi
  pages "repos/$repo/issues/$number/comments" | jq '[.[]|{id,body,html_url,created_at,updated_at,user:(.user|{id,login,type})}]' > "$scratch/pr-comments.json"
  pages "repos/$repo/issues/$number/timeline" | jq '[.[]|{id,event,created_at,label,source:(.source|if .==null then null else {type,issue:(.issue|if .==null then null else {number,title,state,html_url,pull_request:(.pull_request!=null),repository:{full_name:.repository.full_name}} end)} end),actor:(.actor|{id,login,type})}]' > "$scratch/pr-timeline.json"
  pages "repos/$repo/pulls/$number/reviews" | jq '[.[]|{id,state,body,submitted_at,commit_id,html_url,user:(.user|{id,login,type})}]' > "$scratch/pr-reviews.json"
  pages "repos/$repo/pulls/$number/comments" | jq '[.[]|{id,body,path,line,original_line,commit_id,original_commit_id,created_at,updated_at,html_url,in_reply_to_id,user:(.user|{id,login,type})}]' > "$scratch/pr-review-comments.json"
  head_sha=$(jq -er '.head_sha' "$scratch/pr.json")
  pages "repos/$repo/commits/$head_sha/check-runs?filter=latest" '.check_runs' | jq '[.[]|{id,name,head_sha,status,conclusion,details_url,completed_at}]' > "$scratch/pr-checks.json"
  pages "repos/$repo/commits/$head_sha/statuses" | jq '[.[]|{id,context,state,target_url,created_at,updated_at}]' > "$scratch/pr-statuses.json"
  jq --slurpfile comments "$scratch/pr-comments.json" --slurpfile timeline "$scratch/pr-timeline.json" \
    --slurpfile reviews "$scratch/pr-reviews.json" --slurpfile review_comments "$scratch/pr-review-comments.json" \
    --slurpfile checks "$scratch/pr-checks.json" --slurpfile statuses "$scratch/pr-statuses.json" \
    '. + {comments:$comments[0],timeline:$timeline[0],reviews:$reviews[0],review_comments:$review_comments[0],checks:$checks[0],statuses:$statuses[0]}' "$scratch/pr.json" > "$scratch/next-pr.json"
  mv "$scratch/next-pr.json" "$scratch/pr.json"
  api "repos/$repo/pulls/$number" | jq -e --slurpfile original "$scratch/pr.json" '
    .updated_at == $original[0].updated_at and .head.sha == $original[0].head_sha and
    .base.ref == $original[0].base_ref and .base.sha == $original[0].base_sha and
    .state == $original[0].state and .draft == $original[0].draft and
    ([.labels[].name]|sort) == ($original[0].labels|sort)
  ' >/dev/null || { echo 'Contribution changed during collection; retry intake' >&2; exit 1; }
  jq -s '.[0]+[.[1]]' "$scratch/prs.json" "$scratch/pr.json" > "$scratch/next.json"
  mv "$scratch/next.json" "$scratch/prs.json"
done < "$scratch/unique-numbers"
api "repos/$repo/issues/$issue" | jq -e --slurpfile original "$scratch/issue.json" '
  .updated_at == $original[0].updated_at and .state == $original[0].state and
  ([.labels[].name]|sort) == ($original[0].labels|sort) and
  ([.assignees[].login]|sort) == ($original[0].assignees|sort)
' >/dev/null || { echo 'Issue changed during collection; retry intake' >&2; exit 1; }
jq -n --slurpfile issue "$scratch/issue.json" --slurpfile comments "$scratch/comments.json" \
  --slurpfile timeline "$scratch/timeline.json" --slurpfile searches "$scratch/searches.json" --slurpfile prs "$scratch/prs.json" \
  --slurpfile labels "$scratch/labels.json" --slurpfile reactions "$scratch/reactions.json" \
  --slurpfile stacks "$scratch/stacks.json" --arg base_sha "$(cat "$scratch/base-sha")" \
  '{schema_version:2,collected_at:(now|todateiso8601),complete:true,base_sha:$base_sha,
    repository_labels:$labels[0],issue:$issue[0],comments:$comments[0],timeline:$timeline[0],
    reactions:$reactions[0],searches:$searches[0],prs:$prs[0],
    stacks:[$stacks[0][]|select(any(.pull_requests[]; .number as $n|any($prs[0][];.number==$n)))]}' > "$scratch/evidence.json"
if (($(wc -c < "$scratch/evidence.json") > 250000)); then
  echo 'Evidence exceeds the 250 KB intake bound' >&2
  exit 1
fi
cat "$scratch/evidence.json"
