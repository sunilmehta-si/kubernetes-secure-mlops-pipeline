#!/usr/bin/env bash
# Routing tests for deploy/monitoring/alertmanager.yml: each alert in alerts.yaml must reach exactly the receivers
# below. Drift alerts that start retraining go to the relay and to on-call; everything else to on-call only.
# Usage: scripts/test-alert-routing.sh [AMTOOL]   (default: amtool on PATH)
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/versions.env
amtool="${1:-amtool}"
config=deploy/monitoring/alertmanager.yml

"$amtool" check-config "$config" >/dev/null
failed=0
expect() {   # expect RECEIVERS label=value...
  local want="$1"; shift
  if "$amtool" config routes test --config.file "$config" --verify.receivers="$want" "$@" >/dev/null; then
    echo "PASS: $* -> $want"
  else
    echo "FAIL: $* -> $("$amtool" config routes test --config.file "$config" "$@"), expected $want"; failed=1
  fi
}

# every alert in alerts.yaml, with the labels it carries ("name key=value ...", one per line)
while read -r name labels; do
  # shellcheck disable=SC2086
  if [[ " $labels " == *" action=retrain "* ]]; then
    expect retrain-relay,on-call "alertname=$name" $labels
  else
    expect on-call "alertname=$name" $labels
  fi
done < <(python3 -c '
import yaml
for group in yaml.safe_load(open("deploy/monitoring/alerts.yaml"))["groups"]:
    for rule in group["rules"]:
        print(rule["alert"], *(f"{k}={v}" for k, v in sorted(rule["labels"].items())))
')

# a retrain label on any other alert still reaches the relay; nothing without it ever does
expect retrain-relay,on-call alertname=SomethingNew action=retrain
expect on-call alertname=SomethingNew severity=critical
exit "$failed"
