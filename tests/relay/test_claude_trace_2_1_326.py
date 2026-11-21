"""Event shapes captured from Claude Code 2.1.326 on the lab headend (2026-11-19), sanitised.

Every value that named an account, a session, a path or a credential is replaced with a synthetic one; the keys,
the nesting and the values the relay reads are as captured. Three traces: one against the install-check gateway
(which answered initialize without serverInfo), one against a gateway shaped like the Deep32 gateway with a
sign-in the API had revoked, and one with the profile's tokens gone.
"""
import json
import unittest

from support import OfflineCase, Agent, Scratch, request, rpc
from deep32_relay import native, proxy, selftest, status
from deep32_relay.sessions import Supervisor

TOOLS = sorted(proxy.TOOLS)
CONFIG = dict(type='http', url='http://127.0.0.1:34567/mcp', headers=dict(Authorization='Bearer SYNTHETIC-BEARER'))
UI_INVALIDATE = dict(type='system', subtype='ui_invalidate', event='ui.render',
                     uuid='synthetic-uuid', session_id='synthetic-session')
MODELS = [dict(value=name, resolvedModel='claude-synthetic-1', displayName='Synthetic', description='Synthetic',
               supportsEffort=True, supportedEffortLevels=['low', 'medium', 'high'], supportsAdaptiveThinking=True,
               supportsAutoMode=True, supportsThinkingOff=False) for name in ('default', 'opus', 'fable', 'sonnet', 'haiku')]
INITIALIZE = dict(commands=[], agents=[], output_style='default', available_output_styles=[],
    user_output_styles_dir='/synthetic/output-styles', models=MODELS,
    account=dict(email='synthetic@example.invalid', organization='Synthetic organization',
                 subscriptionType='Synthetic plan', apiProvider='firstParty'),
    pid=4242, claude_code_version='2.1.326', current_permission_mode='dontAsk', hook_on_failure_supported=True,
    hooks_when_unanswered_kept=0, feedback_mode=dict(kind='synthetic'), analytics_disabled=True,
    remote_control_auto_enable=False, remote_control_available=True, ide_rc_auto_enable_gate=False,
    fast_mode_state='off', fast_mode_disabled_reason='synthetic', session_state='idle', capabilities=[])
PENDING = dict(name='deep32', status='pending', config=CONFIG, transport='http', scope='dynamic', source='dynamic')
FAILED = dict(PENDING, status='failed', error='Invalid result for initialize: [ { "expected": "object", '
              '"code": "invalid_type", "path": [ "serverInfo" ], '
              '"message": "Invalid input: expected object, received undefined" } ]')
CONNECTED = dict(PENDING, status='connected',
                 serverInfo=dict(name='deep32-gateway', title='Synthetic title', version='1.0.0'),
                 tools=[dict(name=name, annotations=dict(readOnly=True)) for name in TOOLS])
STATUS = dict(type='system', subtype='status', status='requesting', session_id='synthetic-session', uuid='synthetic-uuid')
ZERO_USAGE = dict(input_tokens=0, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0,
                  server_tool_use=dict(web_search_requests=0, web_fetch_requests=0), service_tier=None,
                  cache_creation=dict(ephemeral_1h_input_tokens=0, ephemeral_5m_input_tokens=0),
                  inference_geo=None, iterations=None, speed=None, fallback_credit=None, output_tokens_details=None)
REVOKED_TEXT = 'Failed to authenticate: OAuth token revoked. Please log in again or contact your administrator.'
REVOKED_ASSISTANT = dict(type='assistant', parent_tool_use_id=None, session_id='synthetic-session',
    uuid='synthetic-uuid', timestamp='2026-11-19T00:00:00.000Z', error='authentication_failed',
    request_id='synthetic-request', is_api_error_message=True, api_error_status=401,
    api_error_type='authentication_error', api_error_origin='first_party',
    message=dict(diagnostics=None, id='synthetic-message', container=None, model='<synthetic>', role='assistant',
                 stop_details=None, stop_reason='stop_sequence', stop_sequence='', type='message', usage=ZERO_USAGE,
                 content=[dict(type='text', text=REVOKED_TEXT)], context_management=None))
REVOKED_RESULT = dict(type='result', subtype='success', is_error=True, num_turns=1, duration_ms=289, duration_api_ms=0,
    stop_reason='stop_sequence', session_id='synthetic-session', total_cost_usd=0, modelUsage={},
    usage=dict(ZERO_USAGE, service_tier='standard', inference_geo='', iterations=[], speed='standard',
               output_tokens_details=dict(thinking_tokens=0)),
    permission_denials=[], terminal_reason='api_error', fast_mode_state='off', safety_stops=0,
    api_error_status=401, api_error_type='authentication_error', api_error_origin='first_party',
    result=REVOKED_TEXT, uuid='synthetic-uuid', queued_turn_count=0, result_index=0)


# The same profile once its tokens were gone: the account names no one, and the error carries no API status.
SIGNED_OUT_ACCOUNT = dict(apiProvider='firstParty', tokenSource='synthetic')
EXPIRED_TEXT = 'Failed to authenticate: OAuth session expired and could not be refreshed'
EXPIRED_ASSISTANT = {key: value for key, value in REVOKED_ASSISTANT.items()
                     if key not in ('request_id', 'api_error_status', 'api_error_type', 'api_error_origin')}
EXPIRED_ASSISTANT['message'] = dict(REVOKED_ASSISTANT['message'], content=[dict(type='text', text=EXPIRED_TEXT)])
EXPIRED_RESULT = {key: value for key, value in REVOKED_RESULT.items()
                  if key not in ('api_error_type', 'api_error_origin')}
EXPIRED_RESULT.update(api_error_status=None, result=EXPIRED_TEXT)


def line(value):
    return json.dumps(value).encode() + b'\n'


def control(ident, value):
    return line(dict(type='control_response', response=dict(subtype='success', request_id=ident, response=value),
                     pending_permission_requests=[]))


def init(folder):
    return dict(type='system', subtype='init', cwd=str(folder), original_cwd=str(folder), session_id='synthetic-session',
        tools=['mcp__deep32__' + name for name in TOOLS],
        mcp_servers=[dict(name='deep32', status='connected', source='dynamic')], model='claude-opus-5-5',
        permissionMode='dontAsk', slash_commands=['synthetic'], terminal_slash_commands=[], apiKeySource='none',
        claude_code_version='2.1.326', output_style='default', agents=['synthetic'], skills=['synthetic'],
        plugins=[], capabilities=[], analytics_disabled=True, product_feedback_disabled=True, uuid='synthetic-uuid',
        additional_directories=[], fast_mode_state='off', per_turn_effort_active=True, view_mode='default')


class CapturedStartup(OfflineCase):
    def protocol(self, **options):
        protocol = native.ClaudeProtocol(self.root, 'default', '', **options)
        self.addCleanup(lambda: None)
        return protocol

    def to_status(self, protocol):
        self.assertIsNone(protocol.attest(line(UI_INVALIDATE)))
        self.assertIsNone(protocol.attest(control('relay-init', INITIALIZE)))
        self.assertEqual([json.loads(v)['request']['subtype'] for v in protocol.replies()], ['mcp_status'])
        self.assertEqual((protocol.version, protocol.model, protocol.stage), ('2.1.326', 'default', 'mcp-status'))

    def test_pending_then_connected_then_init_opens_the_turn(self):
        protocol = self.protocol()
        self.to_status(protocol)
        self.assertIsNone(protocol.attest(control('relay-mcp-status', dict(mcpServers=[PENDING]))))
        self.assertEqual([json.loads(v)['request']['subtype'] for v in protocol.replies()], ['mcp_status'])
        self.assertTrue(protocol.attest(control('relay-mcp-status', dict(mcpServers=[CONNECTED]))))
        self.assertEqual(protocol.stage, 'pre-turn')
        protocol.turn('synthetic question', ())
        self.assertTrue(protocol.attest_turn(line(init(self.root))))
        self.assertEqual(protocol.stage, 'running')
        self.assertEqual(protocol.events(line(STATUS)), [])

    def test_server_that_failed_initialize_is_refused_on_a_live_turn(self):
        protocol = self.protocol()
        self.to_status(protocol)
        self.assertIsNone(protocol.attest(control('relay-mcp-status', dict(mcpServers=[PENDING]))))
        protocol.replies()
        with self.assertRaisesRegex(native.NativeError, '^native-startup-unverified$'):
            protocol.attest(control('relay-mcp-status', dict(mcpServers=[FAILED])))
        self.assertEqual(protocol.stage, 'mcp-status')

    def test_account_that_names_no_one_needs_sign_in(self):
        for account in (SIGNED_OUT_ACCOUNT, dict(apiProvider='firstParty'), {}, None):
            with self.subTest(account=account):
                protocol = self.protocol()
                with self.assertRaisesRegex(native.NativeError, '^claude-sign-in-required$'):
                    protocol.attest(control('relay-init', dict(INITIALIZE, account=account)))
                self.assertEqual((protocol.stage, protocol.replies()), ('initialize', []))

    def test_init_with_a_twelfth_tool_or_a_failed_server_is_still_refused(self):
        for change in (dict(tools=init(self.root)['tools'] + ['ToolSearch']),
                       dict(tools=init(self.root)['tools'][:-1]),
                       dict(mcp_servers=[dict(name='deep32', status='failed', source='dynamic')]),
                       dict(mcp_servers=init(self.root)['mcp_servers'] + [dict(name='other', status='connected')])):
            with self.subTest(change=sorted(change)):
                protocol = self.protocol()
                self.to_status(protocol)
                protocol.attest(control('relay-mcp-status', dict(mcpServers=[CONNECTED])))
                protocol.turn('synthetic question', ())
                with self.assertRaisesRegex(native.NativeError, '^native-startup-unverified$'):
                    protocol.attest_turn(line(dict(init(self.root), **change)))


class InstallCheckGateway(unittest.TestCase):
    """Claude Code 2.1.326 reported the install-check gateway as failed: its initialize answer had no serverInfo."""

    def test_initialize_answer_is_a_complete_mcp_result(self):
        metadata = selftest.MetadataGateway()
        cap = metadata.gateway.grant('synthetic-check', metadata.gateway.clock() + 20)
        metadata.gateway.activate(cap)
        answer = metadata.gateway.call(cap, rpc('initialize', protocolVersion='2025-11-25',
            capabilities=dict(roots={}, elicitation={}), clientInfo=dict(name='claude-code', version='2.1.326')))
        result = answer['body']['result']
        self.assertEqual(set(result), {'protocolVersion', 'capabilities', 'serverInfo'})
        self.assertEqual(result['capabilities'], {'tools': {}})
        self.assertEqual(set(result['serverInfo']), {'name', 'version'})
        self.assertTrue(all(isinstance(v, str) and v for v in result['serverInfo'].values()))
        metadata.gateway.call(cap, dict(jsonrpc='2.0', method='notifications/initialized'))
        listed = metadata.gateway.call(cap, rpc('tools/list'))['body']['result']['tools']
        self.assertEqual(sorted(tool['name'] for tool in listed), TOOLS)
        self.assertEqual(metadata.calls, ['initialize', 'notifications/initialized', 'tools/list'])


class SignedOut(OfflineCase):
    def running(self):
        protocol = native.ClaudeProtocol(self.root, 'default', '')
        protocol.stage = 'running'
        return protocol

    def test_revoked_sign_in_is_named_and_its_text_is_not_an_answer(self):
        protocol = self.running()
        self.assertEqual(protocol.events(line(STATUS)), [])
        with self.assertRaisesRegex(native.NativeError, '^claude-sign-in-required$'):
            protocol.events(line(REVOKED_ASSISTANT))
        with self.assertRaisesRegex(native.NativeError, '^claude-sign-in-required$'):
            self.running().events(line(REVOKED_RESULT))

    def test_expired_sign_in_is_named_before_its_text_is_passed_on(self):
        protocol = self.running()
        with self.assertRaisesRegex(native.NativeError, '^claude-sign-in-required$'):
            protocol.events(line(EXPIRED_ASSISTANT))
        # The result of that variant carries no API status; alone it keeps the fixed reason.
        with self.assertRaisesRegex(native.NativeError, '^native-turn-failed$'):
            self.running().events(line(EXPIRED_RESULT))

    def test_other_failed_results_keep_the_fixed_reason(self):
        for change in (dict(api_error_status=500, api_error_type='api_error'),
                       dict(api_error_status=401, api_error_type='api_error'),
                       dict(api_error_status=429, api_error_type='rate_limit_error'),
                       dict(api_error_status=None, api_error_type=None, subtype='error_during_execution')):
            with self.subTest(change=change):
                with self.assertRaisesRegex(native.NativeError, '^native-turn-failed$'):
                    self.running().events(line(dict(REVOKED_RESULT, **change)))

    def test_an_ordinary_assistant_text_is_still_passed_on(self):
        ordinary = dict(REVOKED_ASSISTANT, error=None, is_api_error_message=False, api_error_status=None,
                        api_error_type=None)
        self.assertEqual(self.running().events(line(ordinary)), [dict(type='delta', text=REVOKED_TEXT)])
        other = dict(REVOKED_ASSISTANT, error='rate_limit', api_error_status=429, api_error_type='rate_limit_error')
        self.assertEqual(self.running().events(line(other)), [dict(type='delta', text=REVOKED_TEXT)])

    def test_rate_limit_event_between_messages_is_not_an_answer_and_does_not_stop_the_turn(self):
        # Shape seen on the lab headend 2026-11-19 after a tool call, before the tool result (values synthetic).
        event = dict(type='rate_limit_event', session_id='synthetic-session', uuid='synthetic-uuid',
                     rate_limit_info=dict(status='allowed', resetsAt=1795694400, rateLimitType='five_hour',
                                          overageStatus='disabled', overageDisabledReason='synthetic-reason',
                                          isUsingOverage=False, unifiedWindows=dict(synthetic=dict(used=0))))
        self.assertEqual(self.running().events(line(event)), [])
        with self.assertRaises(native.NativeError):
            self.running().events(line(dict(event, type='unlisted_event_kind')))


class Process:
    def __init__(self, reason):
        self.reason, self.cancelled = reason, 0
    def poll(self):
        raise native.NativeError(self.reason)
    def cancel(self):
        self.cancelled += 1
        return True


class SupervisorReason(OfflineCase):
    def supervisor(self, agent):
        events = []
        supervisor = Supervisor(agent, self.gateway, lambda stream, event: events.append(event),
                                lambda ident: Scratch(self.root, ident), clock=self.clock)
        supervisor.open(1, 'synthetic-1', request())
        return supervisor, events

    def test_signed_out_turn_has_its_own_local_reason(self):
        for reason, expected in (('claude-sign-in-required', 'agent-sign-in-required'),
                                 ('native-turn-failed', 'agent-output-failed'),
                                 ('native-output-failed', 'agent-output-failed')):
            with self.subTest(reason=reason):
                agent = Agent()
                supervisor, events = self.supervisor(agent)
                supervisor.start(1, 'synthetic question')
                handle = Process(reason)
                supervisor.sessions[1].process = handle
                supervisor.tick()
                self.assertEqual(supervisor.last_result, dict(status='failed', reason=expected, usage_known=False,
                                                                model='synthetic-model', effort='synthetic-effort'))
                self.assertIn(expected, status.REASONS)
                self.assertEqual(handle.cancelled, 1)
                self.assertEqual([e['status'] for e in events if e['op'] == 'turn.completed'], ['failed'])
                self.assertFalse(any(e['op'] in ('agent.delta', 'agent.final') for e in events))
                supervisor.close(1)
                self.gateway.capabilities.clear()

    def test_signed_out_start_has_the_same_local_reason(self):
        for reason, expected in (('claude-sign-in-required', 'agent-sign-in-required'),
                                 ('native-startup-unverified', 'agent-start-failed')):
            with self.subTest(reason=reason):
                agent = Agent()
                def start(*args, reason=reason, **choice):
                    from deep32_relay.engine import EngineError
                    raise EngineError(reason)
                agent.start = start
                supervisor, events = self.supervisor(agent)
                supervisor.start(1, 'synthetic question')
                self.assertEqual(supervisor.last_result, dict(status='failed', reason=expected, usage_known=False,
                                                                model='synthetic-model', effort='synthetic-effort'))
                supervisor.close(1)
                self.gateway.capabilities.clear()


if __name__ == '__main__':
    unittest.main()
