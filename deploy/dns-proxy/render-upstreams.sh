#!/bin/sh
# Render the DNS upstream list from EGRESS_DNS_UPSTREAMS into a dnsmasq conf file.
#
#   render-upstreams.sh <output-path>
#
# EGRESS_DNS_UPSTREAMS is a comma-separated list of resolver addresses, e.g.
#   EGRESS_DNS_UPSTREAMS=223.5.5.5,119.29.29.29
#
# Why this exists: the upstream list used to be hardcoded in dnsmasq.conf, so a
# deployment could not pick resolvers that its network actually reaches. The
# acceptance stack runs on CI hosts where the default mainland resolvers answer
# in 70-670 ms, which is close enough to the mediator's per-query budget that a
# transient upstream hiccup aborts a user execution with `upstream_timeout`.
# Making the list an input keeps the fail-closed behaviour (an unreachable
# resolver still fails the query) while letting each environment name resolvers
# it can actually reach.
#
# `all-servers` is emitted whenever more than one resolver is configured:
# dnsmasq then forwards to every resolver at once and uses the first answer,
# instead of waiting out one slow resolver before trying the next one. That
# removes the single-slow-upstream tail from every query.
set -eu

out=${1:?usage: render-upstreams.sh <output-path>}
list=${EGRESS_DNS_UPSTREAMS:-223.5.5.5,119.29.29.29}
# Pin upstream queries to the proxy's primary interface. Workers attach this
# container to a fresh per-execution bridge for every sandbox run; while that
# bridge is present the kernel may route the proxy's own upstream packets out
# of it, where the execution's egress rules drop them. The query is then
# retried and only answers once the bridge is gone - measured as a 15 s cold
# lookup on the CI hosts. Naming the interface keeps upstream traffic on the
# primary network regardless of what is attached.
iface=${EGRESS_DNS_UPSTREAM_INTERFACE:-}

tmp="$out.tmp"
: > "$tmp"

count=0
old_ifs=$IFS
IFS=','
for raw in $list; do
    addr=$(printf '%s' "$raw" | tr -d ' \t')
    [ -n "$addr" ] || continue
    case "$addr" in
        *[!0-9a-fA-F:.]*)
            printf 'invalid resolver address: %s\n' "$addr" >&2
            rm -f "$tmp"
            exit 1
            ;;
    esac
    if [ -n "$iface" ]; then
        printf 'server=%s@%s\n' "$addr" "$iface" >> "$tmp"
    else
        printf 'server=%s\n' "$addr" >> "$tmp"
    fi
    count=$((count + 1))
done
IFS=$old_ifs

if [ "$count" -eq 0 ]; then
    echo 'no resolver configured in EGRESS_DNS_UPSTREAMS' >&2
    rm -f "$tmp"
    exit 1
fi
if [ "$count" -gt 1 ]; then
    # Must precede the server= lines? dnsmasq reads the whole file, order does
    # not matter, but keep it first for readability.
    { printf 'all-servers\n'; cat "$tmp"; } > "$tmp.all" && mv "$tmp.all" "$tmp"
fi
mv "$tmp" "$out"
