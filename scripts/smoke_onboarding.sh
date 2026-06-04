#!/usr/bin/env bash
# First-run onboarding smoke test. Exercises the provisional-owner +
# magic flow against a running API. Read-and-create only. it never
# deletes data, so it is safe to point at any fresh instance.
#
# Usage. scripts/smoke_onboarding.sh [API_BASE]   (default http://localhost:4748)
set -u

API="${1:-http://localhost:4748}"
PASS=0
FAIL=0

j() { python3 -c "import sys,json;d=json.load(sys.stdin);print(d$1)" 2>/dev/null; }
ok()   { echo "  PASS. $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL. $1"; FAIL=$((FAIL+1)); }

echo "== Onboarding smoke against $API =="

# 1. Fresh install reports needs_setup.
NS=$(curl -fsS "$API/api/auth/needs-setup" | j "['needs_setup']")
[ "$NS" = "True" ] && ok "needs-setup is true on fresh install" || bad "needs-setup expected true, got '$NS'"

# 2. Bootstrap auto-creates a provisional owner and returns a token.
BOOT=$(curl -fsS -X POST "$API/api/auth/bootstrap")
TOKEN=$(echo "$BOOT" | j "['access_token']")
PROV=$(echo "$BOOT" | j "['user']['is_provisional']")
ROLE=$(echo "$BOOT" | j "['user']['role']")
[ -n "$TOKEN" ] && ok "bootstrap returned a token" || bad "bootstrap returned no token"
[ "$PROV" = "True" ] && ok "owner is provisional" || bad "owner.is_provisional expected true, got '$PROV'"
[ "$ROLE" = "admin" ] && ok "owner role is admin" || bad "owner.role expected admin, got '$ROLE'"

AUTH="Authorization: Bearer $TOKEN"

# 3. Bootstrap again. an UNCLAIMED provisional install re-adopts the same
#    owner (no lockout, no duplicate), so it returns 201 with the same id.
ID1=$(echo "$BOOT" | j "['user']['id']")
BOOT2=$(curl -fsS -X POST "$API/api/auth/bootstrap")
ID2=$(echo "$BOOT2" | j "['user']['id']")
[ -n "$ID2" ] && [ "$ID1" = "$ID2" ] && ok "second bootstrap re-adopts same owner" || bad "re-adopt failed. $ID1 vs $ID2"

# 4. Demo camera. created, and idempotent on repeat.
CAM1=$(curl -fsS -X POST -H "$AUTH" "$API/api/cameras/demo")
ID1=$(echo "$CAM1" | j "['id']")
URL1=$(echo "$CAM1" | j "['stream_url']")
[ -n "$ID1" ] && ok "demo camera created ($ID1)" || bad "demo camera not created"
CAM2=$(curl -fsS -X POST -H "$AUTH" "$API/api/cameras/demo")
ID2=$(echo "$CAM2" | j "['id']")
[ "$ID1" = "$ID2" ] && ok "demo camera idempotent (same id)" || bad "demo dup. $ID1 vs $ID2"
echo "$URL1" | grep -q '^https\?://' && ok "demo stream_url is remote http(s). $URL1" || bad "demo stream_url not remote. $URL1"
RMODE=$(echo "$CAM1" | j "['recording_mode']")
[ "$RMODE" = "off" ] && ok "demo recording is off (no disk fill)" || bad "demo recording_mode expected off, got '$RMODE'"

# 5. Camera list shows exactly one demo camera.
N=$(curl -fsS -H "$AUTH" "$API/api/cameras" | python3 -c "import sys,json;c=json.load(sys.stdin);print(sum(1 for x in c if x.get('stream_type')=='file'))" 2>/dev/null)
[ "$N" = "1" ] && ok "exactly one file camera present" || bad "expected 1 file camera, got '$N'"

# 5b. Ingestion picks up the new camera and marks it live (informational.
#     the browser plays the demo directly, so the dashboard shows footage
#     even before this, but this confirms the ingestion path works).
LIVE=""
for _ in $(seq 1 20); do
  ST=$(curl -fsS -H "$AUTH" "$API/api/cameras" | python3 -c "import sys,json;c=json.load(sys.stdin);print(next((x.get('status') for x in c if x.get('stream_type')=='file'),''))" 2>/dev/null)
  if [ "$ST" = "live" ] || [ "$ST" = "recording" ]; then LIVE="$ST"; break; fi
  sleep 3
done
[ -n "$LIVE" ] && ok "ingestion connected the demo feed (status=$LIVE)" || echo "  INFO. demo feed not yet live after ~60s (browser still plays it directly)"

# 6. Ollama status reachable (informational. magic uses this).
OST=$(curl -fsS -H "$AUTH" "$API/api/ollama/status")
INST=$(echo "$OST" | j "['installed']")
RUN=$(echo "$OST" | j "['running']")
echo "  INFO. ollama installed=$INST running=$RUN (magic deploys a model only when reachable)"

# 7. Claim rejects a malformed email (lockout guard).
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"email":"notanemail","password":"supersecret"}' "$API/api/auth/claim")
[ "$CODE" = "422" ] && ok "claim rejects malformed email (422)" || bad "claim bad-email expected 422, got $CODE"

# 8. Claim secures the account and clears the provisional flag.
CLAIM=$(curl -fsS -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"email":"owner@example.com","password":"supersecret","display_name":"Owner"}' "$API/api/auth/claim")
CPROV=$(echo "$CLAIM" | j "['is_provisional']")
CEMAIL=$(echo "$CLAIM" | j "['email']")
[ "$CPROV" = "False" ] && ok "claim cleared provisional flag" || bad "after claim is_provisional expected false, got '$CPROV'"
[ "$CEMAIL" = "owner@example.com" ] && ok "claim set real email" || bad "claim email expected owner@example.com, got '$CEMAIL'"

# 9. The secured email can log in. token still valid (sub is user id).
LOGIN=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
  -d '{"email":"owner@example.com","password":"supersecret"}' "$API/api/auth/login")
[ "$LOGIN" = "200" ] && ok "secured account can log in" || bad "login expected 200, got $LOGIN"

echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
