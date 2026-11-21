"""Fixed D2 role fixes and sources; never populated from an observation.

review role-specific substitutions before J09 delivery.
"""

POLICIES = {'forge.container': ('Restore the named service with the retained Forge resume command and its '
                     'verified image, model and data inputs before checking it again.',
                     ('forms/forge_docker_role.py::Lifecycle.status:446-455, prepare_config:53-92; '
                      'containers/full/health.py::check:28-33; NEW per-row parser/device '
                      'snapshot',)),
 'forge.disk': ('Free the reported number of bytes on the Forge data or recorded Docker backing '
                'filesystem before resuming work.',
                ('forms/host-requirements.py::check_storage:148, available_path:142, Windows '
                 'backing-dir checks :262-289; NEW active-runtime budget from receipt',)),
 'forge.docker-compose': ('Install Docker Compose at the reported pinned minimum version or later, '
                          'then run deep32 doctor again.',
                          ('Same check:226-234',)),
 'forge.docker-engine': ("Start the approved Linux Docker engine, or select Docker Desktop's Linux "
                         'containers and WSL 2 backend on Windows, then run deep32 doctor again.',
                         ('Same check:226-261; common docker_role.py::call:275',)),
 'forge.driver': ('Install the NVIDIA driver at the reported minimum version or later for this '
                  'host platform, then check the same Forge again.',
                  ('forms/host-requirements.py::check:209-224, version:85, pin:95, at_least:117; '
                   'signed pins.json runtime floors',)),
 'forge.first-start': ('For a waiting or failed stage, resolve the reported stage prerequisite and '
                       "run the role's existing first-start resume command; no local first-start "
                       'action is needed on a headend.',
                       ('Workstation d32-setup-status:17-28 and pure d32ui_c6:116; Forge C6 '
                        'status.py::encode:34, portable pair_local:435; NEW sanitised C6 parser',)),
 'forge.gpu-hold': ('Finish the recorded Forge job or run its recorded recovery action before '
                    'resuming the model services.',
                    ('forms/forge_docker_role.py::job_guard:190-197, '
                     'forms/forge_jobs.py::Jobs.pending:65, Jobs.recovery:73; integration must '
                     'expose a read-only job snapshot (NEW seam)',)),
 'forge.gpu-runtime': ('Install and configure the NVIDIA Container Toolkit on Linux, or restore '
                       "Docker Desktop's GPU support on Windows, then check the same Forge again.",
                       ('Same nvidia_cdi:126, check:235-261',)),
 'forge.gpu-visible': ("Restore GPU access for the named model service using this release's Docker "
                       'GPU configuration and driver floor, then check the same Forge again.',
                       ('containers/reranker/reranker_api.py::health:48, model startup :33-38; NEW '
                        'embedder device evidence and shared attestation snapshot',)),
 'forge.https-available': ('Enable HTTPS Certificates in Tailscale admin console > DNS and restore '
                           "the selected peer's valid HTTPS publication before running deep32 "
                           'doctor again.',
                           ('Workstation publication.py::identity:64; Forge share that pure '
                            'CertDomains check; adapters/connect.py::Gateway.rpc:384, '
                            'NoRedirect:433',)),
 'forge.installation': ("Restore this role's original verified installation record and run deep32 "
                        'doctor again.',
                        ('Forge forms/forge_docker_role.py::Lifecycle.state:208; headend '
                         'adapters/connect.py::isolated_profile:598; Workstation '
                         'src/workstation/distrib/site/usr/local/d32/sbin/d32-firstboot:20; NEW '
                         'receipt validator',)),
 'forge.magicdns': ('Enable MagicDNS in Tailscale admin console > DNS > MagicDNS and restore the '
                    'enrolled peer name before running deep32 doctor again.',
                    ('Workstation publication.py::identity:61, dns_name:45; headend also uses '
                     'adapters/connect.py::endpoint:174; NEW bounded paired-name resolution',)),
 'forge.model': ('Restore the named model revision from verified release media and complete '
                 'Workstation attestation before checking the same Forge again.',
                 ('prepare_config:38-41, embedding command :54-57; reranker health:48; Workstation '
                  'serve_accel.py::Attestor:217; NEW shared identity observation seam',)),
 'forge.pairing': ('Complete pairing for the selected roles using d32-pairdesk -forge or '
                   'd32-pairdesk -headend, then check the same installation again.',
                   ('deep32net/store.py::load_record:45, _validate:29; Forge invariants '
                    'firstboot.py::pairing_committed:175-203; Workstation marker contract '
                    'deep32net/enrol.py::enrol_forge:113; headend state '
                    'adapters/connect.py::_connect_one:638-645; NEW no-create identity/record '
                    'observer',)),
 'forge.peer-clock': ("Synchronise the local clock and the selected peer's clock with their "
                      'configured time services, then run deep32 doctor again.',
                      ('NEW peer-time contract above; existing Guard:232, door Handler.reply:20 '
                       'are extension anchors',)),
 'forge.peer-reachability': ('Keep the enrolled peer on and signed in, and restore its recorded '
                             'service port in the tailnet access rules before running deep32 '
                             'doctor again.',
                             ('deep32net/discovery.py::parse_status:23 for status parsing only; '
                              'Workstation forwarder paired-host/port validation '
                              'serve_accel.py::Settings:148-178; headend Gateway.rpc:384; NEW '
                              'bounded service-level peer TLS probe',)),
 'forge.port': ('Review the listener using the reported port and change the named C7 forge_ports '
                'setting if another application owns it, then resume the retained Forge '
                'installation.',
                ('forms/forge_publication.py::ports:18, check_local:262; '
                 'forge_docker_role.py::owned_local_ports:148',)),
 'forge.serve-routes': ('Restore the retained Forge publication journal and run the recorded Forge '
                        'resume command; for a port conflict, change the named C7 forge_ports '
                        'setting after the operator reviews ownership.',
                        ('src/gpu-pack/forms/forge_publication.py::check',)),
 'forge.snapshot-changed': ('Run deep32 doctor again after the installation or import completes.',
                            ('src/diagnostics/deep32_diag/runner.py::ExistingFiles.unchanged',)),
 'forge.tailnet-path': ('Allow outbound UDP between the two machines so Tailscale can use a direct '
                        'path; relayed links add tens of milliseconds to every search.',
                        ('src/diagnostics/deep32_diag/models.py::tailnet_path',)),
 'forge.tailscale-installed': ('Install Tailscale on this host, then run deep32 doctor again.',
                               ('forms/host-requirements.py::tailscale_status:42, '
                                'install/deep32-inventory.sh::tool_ver:174; PowerShell inventory '
                                'presence/version probes are supporting inventory only '
                                '(install/deep32-inventory.ps1:5-16), not a daemon test',)),
 'forge.tailscale-running': ('Start the Tailscale service on this host, then run deep32 doctor '
                             'again.',
                             ('Same tailscale_status:42; Workstation '
                              'publication.py::Platform.status:163',)),
 'forge.tailscale-signin': ('Run tailscale login on this host, or approve this device in the '
                            'Tailscale admin console when approval is required, then run deep32 '
                            'doctor again.',
                            ('tailscale_status:58-81, Workstation '
                             'publication.py::identity:54-60',)),
 'forge.windows-pairing': ('Install a release containing the qualified protected Windows pairing '
                           'adapter and complete pairing from the Workstation.',
                           ('forge_docker_role.py::pair_local:417-421; T-0198 GATES.md, gate 1',)),
 'workstation.dense': ("Restore Forge embeddings and the Workstation's enrolled endpoint, then "
                       'allow the dense breaker to recover and run deep32 doctor again.',
                       ('Engine status.py:270-297, 315-327, 365-382; gateway rag_status:1423',)),
 'workstation.disk': ('Free the reported number of bytes on the Workstation content or state '
                      'filesystem before importing more content.',
                      ('install/deep32-inventory.sh::probe_linux_disks:936 provides read-only '
                       'free-space/dedup semantics; reuse '
                       'forms/host-requirements.py::check_storage:148 arithmetic; NEW OpenBSD '
                       'statvfs adapter and Workstation runtime/import budgets',)),
 'workstation.engine': ('Restore the original generation inputs, then run doas rcctl restart '
                        'd32core and deep32 doctor again.',
                        ('src/workstation/ship/apps/etc/rc.d/d32core:16-27, engine '
                         'StatusService.status:203; NEW allowed rcctl check d32core and local '
                         'socket read',)),
 'workstation.first-start': ('For a waiting or failed stage, resolve the reported stage '
                             "prerequisite and run the role's existing first-start resume command; "
                             'no local first-start action is needed on a headend.',
                             ('Workstation d32-setup-status:17-28 and pure d32ui_c6:116; Forge C6 '
                              'status.py::encode:34, portable pair_local:435; NEW sanitised C6 '
                              'parser',)),
 'workstation.forge-auth': ('Pair this Workstation with the intended Forge using d32-pairdesk '
                            '-forge and restore the recorded Forge endpoints before checking it '
                            'again.',
                            ('deep32net/enrol.py::enrol_forge:65-117; '
                             'serve_accel.py::Settings:143-206, protected credential loader '
                             'load_forge_token:91; door forward:30-36; NEW in-process read-only '
                             'attestation status',)),
 'workstation.gateway': ("Restore the gateway's original inputs, then run doas rcctl restart "
                         'd32edge and d32-publish resume.',
                         ('src/workstation/node/shims/rc.d/d32edge:15-28, '
                          'src/gateway/rag_edge.py::health:1434; NEW allowed rcctl check d32edge '
                          'and bounded local MCP',)),
 'workstation.https-available': ('Enable HTTPS Certificates in Tailscale admin console > DNS and '
                                 "restore the selected peer's valid HTTPS publication before "
                                 'running deep32 doctor again.',
                                 ('Workstation publication.py::identity:64; Forge share that pure '
                                  'CertDomains check; adapters/connect.py::Gateway.rpc:384, '
                                  'NoRedirect:433',)),
 'workstation.index-fingerprint': ('Reimport or verify the compatible signed disc 2 with an '
                                   'importer that records the expected corpus fingerprint, then '
                                   'run deep32 doctor again.',
                                   ('Same live status.py:413 and NEW importer expected fingerprint '
                                    'receipt',)),
 'workstation.index-generation': ('Import the compatible signed disc 2 with d32-content-import and '
                                  'restore its recorded active generation before checking the '
                                  'Workstation again.',
                                  ('Engine status status.py:410-419; importer '
                                   'imp_journal.py::Registry.load:324, '
                                   'imp_switch.py::read_pointer:27; NEW signed expected-generation '
                                   'receipt',)),
 'workstation.index-health': ('Restore the compatible signed content generation with '
                              'd32-content-import before checking the Workstation again.',
                              ('Engine status.py:304-356 and degraded:367; same structured status '
                               'result',)),
 'workstation.installation': ("Restore this role's original verified installation record and run "
                              'deep32 doctor again.',
                              ('Forge forms/forge_docker_role.py::Lifecycle.state:208; headend '
                               'adapters/connect.py::isolated_profile:598; Workstation '
                               'src/workstation/distrib/site/usr/local/d32/sbin/d32-firstboot:20; '
                               'NEW receipt validator',)),
 'workstation.magicdns': ('Enable MagicDNS in Tailscale admin console > DNS > MagicDNS and restore '
                          'the enrolled peer name before running deep32 doctor again.',
                          ('Workstation publication.py::identity:61, dns_name:45; headend also '
                           'uses adapters/connect.py::endpoint:174; NEW bounded paired-name '
                           'resolution',)),
 'workstation.model-attestation': ('Restore the reported Forge model revision and check d32accel '
                                   'with deep32 doctor after its configured '
                                   'GPU_EXT_RECHECK_SECONDS interval.',
                                   ('serve_accel.py::Attestor:217, Settings:187-205; NEW qualified '
                                    'attestor snapshot',)),
 'workstation.pairing': ('Complete pairing for the selected roles using d32-pairdesk -forge or '
                         'd32-pairdesk -headend, then check the same installation again.',
                         ('deep32net/store.py::load_record:45, _validate:29; Forge invariants '
                          'firstboot.py::pairing_committed:175-203; Workstation marker contract '
                          'deep32net/enrol.py::enrol_forge:113; headend state '
                          'adapters/connect.py::_connect_one:638-645; NEW no-create '
                          'identity/record observer',)),
 'workstation.peer-clock': ("Synchronise the local clock and the selected peer's clock with their "
                            'configured time services, then run deep32 doctor again.',
                            ('NEW peer-time contract above; existing Guard:232, door '
                             'Handler.reply:20 are extension anchors',)),
 'workstation.peer-reachability': ('Keep the enrolled peer on and signed in, and restore its '
                                   'recorded service port in the tailnet access rules before '
                                   'running deep32 doctor again.',
                                   ('deep32net/discovery.py::parse_status:23 for status parsing '
                                    'only; Workstation forwarder paired-host/port validation '
                                    'serve_accel.py::Settings:148-178; headend Gateway.rpc:384; '
                                    'NEW bounded service-level peer TLS probe',)),
 'workstation.reranker': ("Restore Forge reranking and the Workstation's enrolled endpoint, then "
                          'allow the reranker breaker to recover and run deep32 doctor again.',
                          ('Engine status.py:273-302, 328-343, 379-382',)),
 'workstation.serve-routes': ('Ask the operator to reconcile the recorded Serve route before '
                              'resuming publication with d32-publish resume.',
                              ('Workstation publication.py::Publication.load:267, '
                               'routes_present:135, PROXY:29, PAIR:30; Forge '
                               'forms/forge_publication.py::load:93, check:158, expected:133, '
                               'slice:140',)),
 'workstation.snapshot-changed': ('Run deep32 doctor again after the installation or import '
                                  'completes.',
                                  ('src/diagnostics/deep32_diag/runner.py::ExistingFiles.unchanged',)),
 'workstation.tailnet-path': ('Allow outbound UDP between the two machines so Tailscale can use a '
                              'direct path; relayed links add tens of milliseconds to every '
                              'search.',
                              ('src/diagnostics/deep32_diag/models.py::tailnet_path',)),
 'workstation.tailscale-installed': ('Install Tailscale on this host, then run deep32 doctor '
                                     'again.',
                                     ('forms/host-requirements.py::tailscale_status:42, '
                                      'install/deep32-inventory.sh::tool_ver:174; PowerShell '
                                      'inventory presence/version probes are supporting inventory '
                                      'only (install/deep32-inventory.ps1:5-16), not a daemon '
                                      'test',)),
 'workstation.tailscale-running': ('Start the Tailscale service on this host, then run deep32 '
                                   'doctor again.',
                                   ('Same tailscale_status:42; Workstation '
                                    'publication.py::Platform.status:163',)),
 'workstation.tailscale-signin': ('Run tailscale login on this host, or approve this device in the '
                                  'Tailscale admin console when approval is required, then run '
                                  'deep32 doctor again.',
                                  ('tailscale_status:58-81, Workstation '
                                   'publication.py::identity:54-60',))}


# D2 uses these exact shared-role fixes and source anchors on a headend too.
for _check in ('installation', 'tailscale-installed', 'tailscale-running',
               'tailscale-signin', 'magicdns', 'https-available', 'pairing',
               'peer-reachability', 'tailnet-path', 'peer-clock', 'first-start',
               'snapshot-changed'):
    POLICIES['headend.' + _check] = POLICIES['workstation.' + _check]

POLICIES.update({
 # explicit inapplicable headend route sentence for review.
 'headend.serve-routes': ('No local Serve-route action is needed on a headend.',
     ('DESIGN-DOCTOR.md::all-roles/serve-routes; headend owns no Serve routes',)),
 'headend.agent-profile': ('Use the original enrolled --agent and --profile-dir, or connect this '
     'agent to a new user-created empty profile.',
     ('adapters/connect.py::isolated_profile:598-616, state_root:588, '
      'profile_environment:592, read:65; deep32_diag/profile.py::read_selected_profile',)),
 'headend.agent-connector': ("Rerun Connect for this agent's enrolled --profile-dir and Workstation "
     'URL after reviewing its owned connector entry.',
     ('adapters/connect.py::codex_merge:189-212, claude_merge:215-227, launcher_text:619; '
      'deep32_diag/profile.py::_config, _launcher',)),
 'headend.agent-installed': ('Install or update the selected agent, then check its enrolled profile again.',
     ('adapters/connect.py::detect_agent:438-463; deep32_diag/headend.py::observe_agent',)),
 'headend.agent-signin': ("Use this profile's retained launcher to run the agent's native sign-in "
     'command, then launch it again.',
     ('adapters/connect.py::detect_agent:453-460, profile_environment:592; '
      'deep32_diag/headend.py::observe_agent',)),
 'headend.gateway-auth': ('Open d32-pairdesk -headend on the Workstation and rerun Connect for this '
     'selected agent and --profile-dir.',
     ('adapters/connect.py::PrivateStore.get:166, Gateway.probe:406, rpc:384, verify:415; '
      'deep32_diag/headend.py::observe_gateway',)),
 'headend.mcp-rag-status': ('Run deep32 doctor on the Workstation and repair its reported retrieval '
     'services before checking this headend again.',
     ('adapters/connect.py::Gateway.verify:415-430; src/gateway/rag_edge.py::rag_status:1423; '
      'deep32_diag/mcp.py::MCPClient, _status',)),
})
