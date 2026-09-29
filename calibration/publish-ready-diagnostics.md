# publish-ready diagnostics (2026-09-29 22:42:07Z)
# publish-ready diagnostics (2026-09-29 22:42:07Z)
```
tiles-20260929-26	2026-09-29T22:31:15Z	0	false
tiles-20260929-25	2026-09-29T16:49:33Z	61	false
tiles-20260929-24	2026-09-29T13:47:42Z	33	false
tiles-20260929-23	2026-09-29T09:02:26Z	56	false
tiles-20260929-22	2026-09-29T04:42:58Z	54	false
```
target: `tiles-20260929-25`

## trace
```
++ jq -r --arg t tiles-20260929-25 '[.[] | select(.draft and .tag_name == $t)
    | [.assets[] | select(.name | endswith(".tar-00"))] | length]' /tmp/releases.json
+ SLUGS='[
  61
]'
+ echo 'country slugs: [
  61
] (min 61)'
+ '[' '[
  61
]' -ge 61 ']'
/home/runner/work/_temp/66b6eaa9-17b0-48b0-99bf-95530e55339f.sh: line 12: [: [
  61
]: integer expression expected
+ echo 'incomplete supply — refusing to publish'
+ exit 2
```
