#!/bin/sh
# dns-proxy entrypoint: render the resolver list, then run dnsmasq in the
# foreground so the container keeps PID 1 alive.
#
# The rendered file lives under /etc/dnsmasq.d so the image's own dnsmasq.conf
# stays a static policy file (no-resolv, logging, blacklist directory) and the
# volatile input — which resolvers this environment can reach — is one file.
set -eu

/etc/aw/render-upstreams.sh /etc/dnsmasq.d/upstream.conf

# -k: stay in the foreground; --conf-file: the policy file baked into the image.
exec dnsmasq -k --conf-file=/etc/dnsmasq.conf
