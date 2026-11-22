def local_reference:
  "(?:https://github\\.com/PrefectHQ/fastmcp/pull/|(?<![A-Za-z0-9_/=?&])#)([1-9][0-9]*)(?![0-9])(?!(?:\\]\\()(?!https://github\\.com/PrefectHQ/fastmcp/pull/))";
def referenced_numbers($body):
  [
    ($body | scan("\\b(?:canonical(?:\\s+(?:PR|pull\\s+request|fix|change|implementation))?(?:\\s+(?:is|at|in))?|superseded\\s+by|superseding\\s+(?:PR|pull\\s+request)|(?:duplicate|redundant)\\s+(?:of|with)|in\\s+favou?r\\s+of)[ \t:*`\\[(]*" + local_reference; "i") | .[0]),
    ($body | scan(local_reference + "\\s+(?:as|is)\\s+(?:the\\s+)?canonical\\b"; "i") | .[0]),
    (if ($body | test("\\b(?:duplicate|redundant)\\b"; "i")) then
      ($body | scan(local_reference + "\\s+already\\s+(?:fixes|covers)\\b"; "i") | .[0])
     else empty end)
  ] | .[] | tonumber;
[
  .[] as $pr |
  ($pr.comments // [])[] |
  select(.user.type == "User" and (.author_association | IN("OWNER", "MEMBER", "COLLABORATOR"))) |
  . as $comment |
  referenced_numbers($comment.body // "") |
  select(. != $pr.number) |
  {number: ., source_pr: $pr.number, comment_id: $comment.id, comment_url: $comment.html_url,
   author: $comment.user.login, author_association: $comment.author_association}
] | unique_by([.number, .source_pr, .comment_id])
