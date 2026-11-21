"""Owned service and protected pairing console dispatch."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


UNAVAILABLE = ("pairing-bootstrap-unavailable", "native-agent-unqualified",
               "native-oracle-unavailable", "research-instruction-unreviewed",
               "host-transport-unqualified")


def package_digest():
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for path in sorted(root.glob("*.py")):
        digest.update(path.name.encode("ascii") + b"\0" + path.read_bytes())
    prompt = root.parent / "RESEARCH.md"
    digest.update(prompt.read_bytes())
    lock = root.parent / 'tls-provider-lock.json'
    if lock.is_file():
        digest.update(b'tls-provider-lock.json\0' + lock.read_bytes())
    requirements = root.parent / 'requirements.lock'
    if requirements.is_file():
        digest.update(b'requirements.lock\0' + requirements.read_bytes())
    return digest.hexdigest()


def assess(owner=None):
    # No file receipt, command-line flag or edited configuration can waive these
    # implementation gates. Native adapters require a separately reviewed change.
    result = {"schema": "deep32.relay-check.v1", "ready": False,
            "reasons": list(UNAVAILABLE), "package_sha256": package_digest(),
            "agent_launches": 0, "listeners": 0}
    if owner is not None:
        from .connector import validate_receipt
        from .engine import qualification_status
        receipt = validate_receipt(owner)
        qualification = qualification_status(owner, receipt)
        result['agent'] = receipt['agent']
        result['profile_alias'] = receipt['profile_alias']
        result['qualification'] = qualification
        from .tls import provider_readiness
        result['reasons'] = []
        if qualification.get('state') != 'qualified':
            result['reasons'].append('native-agent-unqualified')
        if not provider_readiness():
            result['reasons'].append('tls-provider-unqualified')
        from .identity import IdentityStore
        if IdentityStore(owner).load_peer() is None:
            result['reasons'].append('pairing-required')
        if receipt['state'] == 'removed' or not owner.read_json('config.json').get('intake_enabled'):
            result['reasons'].append('relay-intake-disabled')
        result['ready'] = not result['reasons']
    return result


class ResearchBridge:
    """Connect a TLS-authenticated Link to the pure supervisor for fake tests.

    Production does not instantiate this bridge while assess().ready is false.
    A Link callback alone cannot authenticate a peer; TLS pin verification and
    committed peer-id state are prerequisites owned by the link transport.
    """
    def __init__(self, link, supervisor):
        self.link, self.supervisor = link, supervisor
        self.pending = {}
        supervisor.emit = self.send_event
        supervisor.on_close = link.close_stream
        link.on_session_open = self.open_stream
        link.on_message = self.message
        link.on_close = self.close_stream

    def send_event(self, stream, event):
        from .wire import ProtocolError
        channel = self.link.streams.get(stream)
        if self.link.state == "active" and channel is not None and not channel.closing:
            try:
                self.link.send_message(stream, event)
            except ProtocolError:
                self.link.disconnect()

    def open_stream(self, stream, session_id):
        self.pending[stream] = session_id

    def message(self, stream, value):
        from .sessions import SessionError
        try:
            if stream in self.pending:
                session_id = self.pending.pop(stream)
                if not self.supervisor.open(stream, session_id, value):
                    self.link.close_stream(stream)
            else:
                self.supervisor.request(stream, value)
        except SessionError:
            self.supervisor.close(stream)
            self.link.close_stream(stream)

    def close_stream(self, stream, reason):
        self.pending.pop(stream, None)
        if self.link.state == "closed":
            self.disconnect()
        else:
            self.supervisor.close(stream, "session-closed")

    def disconnect(self):
        self.pending.clear()
        self.supervisor.disconnect()


def make_bridge(link, agent, scratch_factory, *, clock):
    from .proxy import Gateway, ProxyError
    from .sessions import Supervisor
    def dispatch(session, envelope, deadline, guard):
        remaining = deadline - clock()
        if remaining <= 0:
            raise ProxyError("inactive-turn")
        return link.reverse_request(session, envelope, timeout=min(170, remaining), guard=guard)
    gateway = Gateway(None, clock=clock, guarded_dispatch=dispatch,
                      on_revoke=link.cancel_requests)
    supervisor = Supervisor(agent, gateway, lambda stream, event: None,
                            scratch_factory, clock=clock)
    return ResearchBridge(link, supervisor)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check the Ask relay service prerequisites.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("action", nargs="?", choices=("run", "pair"), default="run")
    args = parser.parse_args(argv)
    from .state import OwnedRoot
    try:
        root = OwnedRoot(Path(args.root))
        install_id = root.validate()
        if args.action == 'pair':
            if args.check:
                raise ValueError('invalid-service-action')
            from .connector import pair
            result = pair(root)
            print(json.dumps(result, sort_keys=True, separators=(',', ':')))
            return 0 if result['paired'] else 10
        result = assess(root)
        if not args.check:
            from .daemon import run_owned
            with root.lock('service.lock'):
                return run_owned(root)
    except Exception as exc:
        if (not args.check and args.action == 'run' and getattr(exc, 'reason', None) in
                ('relay-intake-disabled', 'native-agent-unqualified', 'tls-provider-unqualified', 'pairing-required')):
            # A failed, owned service entry leaves a typed stopped observation;
            # never publish prompts, exceptions or native process output.
            try:
                from .status import publish_status, unavailable_snapshot
                from .connector import validate_receipt, binary_found
                receipt = validate_receipt(root)
                publish_status(root, unavailable_snapshot(install_id, receipt['agent'],
                    receipt['release_id'], agent_found=binary_found(receipt['agent_binary'])))
            except Exception:
                pass
        result = {"schema": "deep32.relay-check.v1", "ready": False,
                  "reasons": ["owned-state-unavailable"], "agent_launches": 0, "listeners": 0}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result.get('ready') else 10
