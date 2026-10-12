#!/usr/bin/env bash
# Linux VPS only. Apply BEFORE turning on CRAWL4AI_ENABLED.
# Re-run after compose container recreation; `check` fails if IPs/rules drift.
set -euo pipefail

action="${1:-check}"
case "$action" in apply|check) ;; *) echo 'usage: crawl4ai_firewall.sh apply|check' >&2; exit 2 ;; esac
if [ "$(id -u)" -ne 0 ]; then echo 'root is required' >&2; exit 2; fi

container_ip() {
  docker inspect --format "{{with index .NetworkSettings.Networks \"$2\"}}{{.IPAddress}}{{end}}" "$1"
}
crawler_ip="$(container_ip fcai-crawl4ai fcai-crawl4ai-proxy)"
crawler_api_ip="$(container_ip fcai-crawl4ai fcai-crawl4ai-api)"
crawler_dns_ip="$(container_ip fcai-crawl4ai fcai-crawl4ai-dns)"
bot_api_ip="$(container_ip fcai-bot fcai-crawl4ai-api)"
proxy_ip="$(container_ip fcai-crawl4ai-proxy fcai-crawl4ai-proxy)"
proxy_public_ip="$(container_ip fcai-crawl4ai-proxy fcai-crawl4ai-uplink)"
dns_ip="$(container_ip fcai-crawl4ai-dns fcai-crawl4ai-dns)"
dns_public_ip="$(container_ip fcai-crawl4ai-dns fcai-crawl4ai-uplink)"
if ! [[ "$crawler_ip" =~ ^[0-9.]+$ && "$crawler_api_ip" =~ ^[0-9.]+$ && "$crawler_dns_ip" =~ ^[0-9.]+$ && "$bot_api_ip" =~ ^[0-9.]+$ && "$proxy_ip" =~ ^[0-9.]+$ && "$proxy_public_ip" =~ ^[0-9.]+$ && "$dns_ip" =~ ^[0-9.]+$ && "$dns_public_ip" =~ ^[0-9.]+$ ]]; then
  echo 'missing crawler/proxy/dns network attachment: refusing to enable' >&2
  exit 1
fi
if [ "$crawler_ip" = "$proxy_ip" ]; then echo 'invalid network identity' >&2; exit 1; fi

for network in fcai-crawl4ai-api fcai-crawl4ai-proxy fcai-crawl4ai-dns fcai-crawl4ai-uplink; do
  if [ "$(docker network inspect --format '{{.EnableIPv6}}' "$network")" != false ]; then
    echo "IPv6 must be disabled on $network" >&2
    exit 1
  fi
  if [ "$(docker network inspect --format '{{.Driver}}' "$network")" != bridge ]; then
    echo "only Docker bridge networks are supported: $network" >&2
    exit 1
  fi
done

bridge_for() {
  local network_id configured
  network_id="$(docker network inspect --format '{{.Id}}' "$1")"
  configured="$(docker network inspect --format '{{index .Options "com.docker.network.bridge.name"}}' "$1")"
  if [ -n "$configured" ] && [ "$configured" != '<no value>' ]; then
    printf '%s' "$configured"
  else
    printf 'br-%s' "${network_id:0:12}"
  fi
}
api_bridge="$(bridge_for fcai-crawl4ai-api)"
proxy_bridge="$(bridge_for fcai-crawl4ai-proxy)"
dns_bridge="$(bridge_for fcai-crawl4ai-dns)"
uplink_bridge="$(bridge_for fcai-crawl4ai-uplink)"
for bridge in "$api_bridge" "$proxy_bridge" "$dns_bridge" "$uplink_bridge"; do
  if [ ! -d "/sys/class/net/$bridge" ]; then
    echo "missing Docker bridge $bridge: refusing to enable" >&2
    exit 1
  fi
done
if [ "$(sysctl -n net.bridge.bridge-nf-call-iptables 2>/dev/null || true)" != 1 ]; then
  echo 'bridge-netfilter must route bridged IPv4 through firewall' >&2
  exit 1
fi
for network in fcai-crawl4ai-api fcai-crawl4ai-proxy fcai-crawl4ai-dns; do
  if [ "$(docker network inspect --format '{{.Internal}}' "$network")" != true ]; then
    echo "network $network must be internal" >&2
    exit 1
  fi
done

# Explicitly deny special-use and non-public IPv4 ranges; fail on ANY other
# port/protocol. Docker DNS at 127.0.0.11 is local to the proxy namespace.
blocked=(0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8
  169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 192.0.2.0/24
  192.88.99.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24
  203.0.113.0/24 224.0.0.0/4 240.0.0.0/4)

backend="$(docker info --format '{{json .FirewallBackend}}')"
case "$backend" in
  *nftables*) mode=nftables ;;
  *iptables*) mode=iptables ;;
  *) echo 'unknown Docker firewall backend; refusing to enable' >&2; exit 1 ;;
esac

if [ "$mode" = iptables ]; then
  command -v iptables >/dev/null
  # DOCKER-USER is evaluated before Docker's forwarding rules. INPUT covers
  # traffic destined for the host instead of a forwarded container.
  chain=FCAI_CRAWL4AI
  host_chain=FCAI_CRAWL4AI_HOST
  if [ "$action" = apply ]; then
    iptables -N "$chain" 2>/dev/null || true
    iptables -N "$host_chain" 2>/dev/null || true
    iptables -C DOCKER-USER -j "$chain" 2>/dev/null && iptables -D DOCKER-USER -j "$chain" || true
    iptables -I DOCKER-USER 1 -j "$chain"
    iptables -C INPUT -j "$host_chain" 2>/dev/null && iptables -D INPUT -j "$host_chain" || true
    iptables -I INPUT 1 -j "$host_chain"
    iptables -F "$chain"
    iptables -A "$chain" -s "$crawler_ip" -d "$proxy_ip" -p tcp --dport 3128 -j RETURN
    iptables -A "$chain" -s "$crawler_ip" -j DROP
    iptables -A "$chain" -s "$crawler_dns_ip" -d "$dns_ip" -p udp --dport 53 -j RETURN
    iptables -A "$chain" -s "$crawler_dns_ip" -d "$dns_ip" -p tcp --dport 53 -j RETURN
    iptables -A "$chain" -s "$crawler_dns_ip" -j DROP
    iptables -A "$chain" -s "$dns_ip" -d "$crawler_dns_ip" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    iptables -A "$chain" -s "$dns_ip" -j DROP
    # The resolver may use Docker's local 127.0.0.11 DNS only. All raw
    # uplink traffic from its own namespace is forbidden.
    iptables -A "$chain" -s "$dns_public_ip" -j DROP
    iptables -A "$chain" -s "$crawler_api_ip" -d "$bot_api_ip" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    iptables -A "$chain" -s "$crawler_api_ip" -j DROP
    iptables -A "$chain" -s "$bot_api_ip" -d "$crawler_api_ip" -p tcp --dport 11235 -j RETURN
    iptables -A "$chain" -s "$bot_api_ip" -j DROP
    iptables -A "$chain" -s "$proxy_ip" -d "$crawler_ip" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    # Proxy cannot connect to crawler, any internal range, or a non-web port.
    for cidr in "${blocked[@]}"; do
      iptables -A "$chain" -s "$proxy_public_ip" -d "$cidr" -j DROP
      iptables -A "$chain" -s "$proxy_ip" -d "$cidr" -j DROP
    done
    iptables -A "$chain" -s "$proxy_public_ip" -p tcp -m multiport --dports 80,443 -j RETURN
    iptables -A "$chain" -s "$proxy_public_ip" -j DROP
    iptables -A "$chain" -s "$proxy_ip" -j DROP
    # Forwarded replies arrive on the uplink bridge with destination set to
    # Squid's uplink address. Without this rule the bridge catch-all below
    # also drops replies from permitted public HTTP/HTTPS connections.
    iptables -A "$chain" -i "$uplink_bridge" -d "$proxy_public_ip" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    # Catch unknown container IPs on the same bridges (restart/address churn).
    for bridge in "$api_bridge" "$proxy_bridge" "$dns_bridge" "$uplink_bridge"; do
      iptables -A "$chain" -i "$bridge" -j DROP
    done
    iptables -A "$chain" -j RETURN
    iptables -F "$host_chain"
    iptables -A "$host_chain" -s "$crawler_ip" -j DROP
    iptables -A "$host_chain" -s "$crawler_api_ip" -j DROP
    iptables -A "$host_chain" -s "$crawler_dns_ip" -j DROP
    iptables -A "$host_chain" -s "$dns_ip" -j DROP
    iptables -A "$host_chain" -s "$dns_public_ip" -j DROP
    iptables -A "$host_chain" -s "$proxy_ip" -j DROP
    iptables -A "$host_chain" -s "$proxy_public_ip" -j DROP
    for bridge in "$api_bridge" "$proxy_bridge" "$dns_bridge" "$uplink_bridge"; do
      iptables -A "$host_chain" -i "$bridge" -j DROP
    done
    iptables -A "$host_chain" -j RETURN
  fi
  iptables -C DOCKER-USER -j "$chain"
  iptables -C INPUT -j "$host_chain"
  [ "$(iptables -S DOCKER-USER | sed -n '2p')" = "-A DOCKER-USER -j $chain" ]
  [ "$(iptables -S INPUT | sed -n '2p')" = "-A INPUT -j $host_chain" ]
  iptables -C "$chain" -s "$crawler_ip" -d "$proxy_ip" -p tcp --dport 3128 -j RETURN
  iptables -C "$chain" -s "$crawler_ip" -j DROP
  iptables -C "$chain" -s "$crawler_dns_ip" -d "$dns_ip" -p udp --dport 53 -j RETURN
  iptables -C "$chain" -s "$crawler_dns_ip" -d "$dns_ip" -p tcp --dport 53 -j RETURN
  iptables -C "$chain" -s "$crawler_dns_ip" -j DROP
  iptables -C "$chain" -s "$dns_ip" -d "$crawler_dns_ip" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  iptables -C "$chain" -s "$dns_ip" -j DROP
  iptables -C "$chain" -s "$dns_public_ip" -j DROP
  iptables -C "$chain" -s "$crawler_api_ip" -d "$bot_api_ip" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  iptables -C "$chain" -s "$crawler_api_ip" -j DROP
  iptables -C "$chain" -s "$bot_api_ip" -d "$crawler_api_ip" -p tcp --dport 11235 -j RETURN
  iptables -C "$chain" -s "$bot_api_ip" -j DROP
  iptables -C "$chain" -s "$proxy_ip" -d "$crawler_ip" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  for cidr in "${blocked[@]}"; do
    iptables -C "$chain" -s "$proxy_public_ip" -d "$cidr" -j DROP
    iptables -C "$chain" -s "$proxy_ip" -d "$cidr" -j DROP
  done
  iptables -C "$chain" -s "$proxy_public_ip" -p tcp -m multiport --dports 80,443 -j RETURN
  iptables -C "$chain" -s "$proxy_public_ip" -j DROP
  iptables -C "$chain" -s "$proxy_ip" -j DROP
  iptables -C "$chain" -i "$uplink_bridge" -d "$proxy_public_ip" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  iptables -C "$host_chain" -s "$crawler_ip" -j DROP
  iptables -C "$host_chain" -s "$crawler_api_ip" -j DROP
  iptables -C "$host_chain" -s "$crawler_dns_ip" -j DROP
  iptables -C "$host_chain" -s "$dns_ip" -j DROP
  iptables -C "$host_chain" -s "$dns_public_ip" -j DROP
  iptables -C "$host_chain" -s "$proxy_ip" -j DROP
  iptables -C "$host_chain" -s "$proxy_public_ip" -j DROP
  for bridge in "$api_bridge" "$proxy_bridge" "$dns_bridge" "$uplink_bridge"; do
    iptables -C "$chain" -i "$bridge" -j DROP
    iptables -C "$host_chain" -i "$bridge" -j DROP
  done
else
  command -v nft >/dev/null
  if [ "$action" = apply ]; then
    rules="$(mktemp)"
    trap 'rm -f "$rules"' EXIT
    if nft list table inet fcai_crawl4ai >/dev/null 2>&1; then
      printf 'delete table inet fcai_crawl4ai\n' > "$rules"
    fi
    cat >> "$rules" <<EOF
table inet fcai_crawl4ai {
  chain forward {
    type filter hook forward priority -201; policy accept;
    ip saddr $crawler_ip ip daddr $proxy_ip tcp dport 3128 accept
    ip saddr $crawler_ip drop
    ip saddr $crawler_dns_ip ip daddr $dns_ip udp dport 53 accept
    ip saddr $crawler_dns_ip ip daddr $dns_ip tcp dport 53 accept
    ip saddr $crawler_dns_ip drop
    ip saddr $dns_ip ip daddr $crawler_dns_ip ct state { established, related } accept
    ip saddr $dns_ip drop
    ip saddr $dns_public_ip drop
    ip saddr $crawler_api_ip ip daddr $bot_api_ip ct state { established, related } accept
    ip saddr $crawler_api_ip drop
    ip saddr $bot_api_ip ip daddr $crawler_api_ip tcp dport 11235 accept
    ip saddr $bot_api_ip drop
    ip saddr $proxy_ip ip daddr $crawler_ip ct state { established, related } accept
EOF
    for cidr in "${blocked[@]}"; do
      printf '    ip saddr %s ip daddr %s drop\n' "$proxy_public_ip" "$cidr" >> "$rules"
      printf '    ip saddr %s ip daddr %s drop\n' "$proxy_ip" "$cidr" >> "$rules"
    done
    cat >> "$rules" <<EOF
    ip saddr $proxy_public_ip tcp dport { 80, 443 } accept
    ip saddr $proxy_public_ip drop
    ip saddr $proxy_ip drop
    iifname "$uplink_bridge" ip daddr $proxy_public_ip ct state { established, related } accept
    iifname "$api_bridge" drop
    iifname "$proxy_bridge" drop
    iifname "$dns_bridge" drop
    iifname "$uplink_bridge" drop
  }
  chain input {
    type filter hook input priority -201; policy accept;
    ip saddr $crawler_ip drop
    ip saddr $crawler_api_ip drop
    ip saddr $crawler_dns_ip drop
    ip saddr $dns_ip drop
    ip saddr $dns_public_ip drop
    ip saddr $proxy_ip drop
    ip saddr $proxy_public_ip drop
    iifname "$api_bridge" drop
    iifname "$proxy_bridge" drop
    iifname "$dns_bridge" drop
    iifname "$uplink_bridge" drop
  }
}
EOF
    nft -f "$rules"
  fi
  listing="$(nft list table inet fcai_crawl4ai)"
  for expected in "ip saddr $crawler_ip ip daddr $proxy_ip tcp dport 3128 accept" \
    "ip saddr $crawler_ip drop" "ip saddr $crawler_api_ip drop" \
    "ip saddr $crawler_dns_ip ip daddr $dns_ip udp dport 53 accept" \
    "ip saddr $crawler_dns_ip ip daddr $dns_ip tcp dport 53 accept" \
    "ip saddr $crawler_dns_ip drop" \
    "ip saddr $dns_ip ip daddr $crawler_dns_ip ct state { established, related } accept" \
    "ip saddr $dns_ip drop" "ip saddr $dns_public_ip drop" \
    "ip saddr $bot_api_ip ip daddr $crawler_api_ip tcp dport 11235 accept" \
    "ip saddr $bot_api_ip drop" \
    "ip saddr $proxy_ip ip daddr $crawler_ip ct state { established, related } accept" \
    "ip saddr $proxy_public_ip drop" \
    "ip saddr $proxy_ip drop" "tcp dport { 80, 443 } accept" \
    "iifname \"$uplink_bridge\" ip daddr $proxy_public_ip ct state { established, related } accept" \
    "type filter hook input priority -201" \
    "type filter hook forward priority -201"; do
    if ! grep -Fq "$expected" <<< "$listing"; then
      echo "nftables rule missing: $expected" >&2; exit 1
    fi
  done
  for bridge in "$api_bridge" "$proxy_bridge" "$dns_bridge" "$uplink_bridge"; do
    if [ "$(grep -Fc "iifname \"$bridge\" drop" <<< "$listing")" -lt 2 ]; then
      echo "nftables bridge guard missing: $bridge" >&2; exit 1
    fi
  done
  for cidr in "${blocked[@]}"; do
    if ! grep -Fq "ip saddr $proxy_public_ip ip daddr $cidr drop" <<< "$listing"; then
      echo "nftables denied range missing: $cidr" >&2; exit 1
    fi
    if ! grep -Fq "ip saddr $proxy_ip ip daddr $cidr drop" <<< "$listing"; then
      echo "nftables internal denied range missing: $cidr" >&2; exit 1
    fi
  done
fi
echo "Crawl4AI firewall verified ($mode): crawler=$crawler_ip proxy=$proxy_ip dns=$dns_ip outbound=$proxy_public_ip"
