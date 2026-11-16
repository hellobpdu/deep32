# 66-ship-node (ship lane): the node's RAG (production deep32-engine pub-9a2d58df01fc on CPython 3.12.14, deep32-gateway pub-9a2d58df01fc on 3.14,
# exact_local + libd32vec instead of Qdrant, the shims' d32-sockexec, launchers, rc.d and classes) and asklink. Then the
# token files the services need (root 0400) with empty tokens: d32-firstboot makes them at the first start.
# Zero trust: remote callers get queries only (relayd front OFF), the GPU forwarder is OFF, asklink is NOT enabled.
. /etc/d32/site-lib.sh
. /etc/d32/ship-lib.sh
f=$(pack_file d32-node)
[ -n "$f" ] || die "no d32-node pack in PACKS.tsv"
d32 "item unpacking d32-node"
tar -xzpf $M/$f -C /
pack_check d32-node
umask 077
install -d -o root -g wheel -m 0700 /etc/d32/state
cat >/etc/d32/core.env <<EOT
# /etc/d32/core.env (root 0400): written at install by 66-ship-node; d32-firstboot makes the token at the first start.
RAG_ROOT=/d32/libcontent/kb/root
RAG_CORE_API_TOKEN=
RAG_QDRANT_URL=file:/d32/libcontent/kb/dense
RAG_EMBEDDING_URL=http://gpu-embed.d32.invalid/v1/embeddings
RAG_RERANK_URL=http://gpu-rerank.d32.invalid/v1/rerank
EOT
cat >/etc/d32/edge.env <<EOT
# /etc/d32/edge.env (root 0400): written at install by 66-ship-node; d32-firstboot makes the tokens at the first start.
RAG_CORE_URL=http://localhost
RAG_CORE_API_TOKEN=
TAILSCALE_DNS_NAME=deep32.invalid
RAG_MCP_TOKENS=
EOT
[ -f /etc/d32/accel.env ] || cp /etc/d32/accel.env.example /etc/d32/accel.env
chown root:wheel /etc/d32/core.env /etc/d32/edge.env /etc/d32/accel.env
chmod 0400 /etc/d32/core.env /etc/d32/edge.env /etc/d32/accel.env
umask 022
for s in gpu_ext edge_front; do
	print off >/etc/d32/state/$s
	chmod 0600 /etc/d32/state/$s
done
install -d -o root -g wheel -m 0755 /var/d32/run /var/d32/lamps
print off >/var/d32/run/gpu_ext
chmod 0644 /var/d32/run/gpu_ext
install -d -o _d32core -g _d32core -m 0700 /var/d32/run/core-tmp
d32 "item core.env, edge.env (root 0400, no tokens yet); GPU_EXT off; remote front off; asklink installed, not enabled"
