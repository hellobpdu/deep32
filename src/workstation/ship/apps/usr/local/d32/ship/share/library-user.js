// The Library's private Firefox ESR profile (ship lane, First light PREVIEW): loopback encyclopedia and the
// Library's own files only. Update 1 (U20, U33): d32lib re-copies this file into the profile at EVERY launch, so a
// changed pref never survives a restart; the network lockdown keeps only what a
// reader needs (PDF.js stays on for papers opened in the browser). pf is still the real barrier: the desktop user's processes
// reach nothing but loopback.
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage", "http://127.0.0.1:8850/");
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("startup.homepage_welcome_url", "");
user_pref("startup.homepage_welcome_url.additional", "");
user_pref("startup.homepage_override_url", "");
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.uitour.enabled", false);
user_pref("browser.messaging-system.whatsNewPanel.enabled", false);
user_pref("browser.newtabpage.enabled", false);
user_pref("browser.newtab.preload", false);
user_pref("browser.tabs.firefox-view", false);
user_pref("extensions.pocket.enabled", false);

// --- the window: dtwm draws the only title bar and its buttons (U20: no second set in the tab strip)
user_pref("browser.tabs.inTitlebar", 0);

// --- no network beyond loopback (U33)
user_pref("network.dns.disabled", true);
user_pref("network.proxy.type", 0);
user_pref("network.trr.mode", 5);
user_pref("network.predictor.enabled", false);
user_pref("network.prefetch-next", false);
user_pref("network.dns.disablePrefetch", true);
user_pref("network.http.speculative-parallel-limit", 0);
user_pref("network.captive-portal-service.enabled", false);
user_pref("network.connectivity-service.enabled", false);
user_pref("captivedetect.canonicalURL", "");
user_pref("security.OCSP.enabled", 0);
user_pref("security.remote_settings.crlite_filters.enabled", false);
user_pref("services.settings.server", "http://127.0.0.1:1/");
user_pref("browser.region.update.enabled", false);
user_pref("browser.region.network.url", "");
user_pref("browser.search.update", false);
user_pref("browser.search.suggest.enabled", false);
user_pref("browser.urlbar.suggest.searches", false);
user_pref("geo.enabled", false);
user_pref("media.gmp-manager.url", "http://127.0.0.1:1/");
user_pref("media.gmp-provider.enabled", false);
user_pref("media.eme.enabled", false);
user_pref("media.peerconnection.enabled", false);
user_pref("dom.push.enabled", false);
user_pref("dom.push.serverURL", "");
user_pref("extensions.update.enabled", false);
user_pref("extensions.getAddons.cache.enabled", false);
user_pref("extensions.systemAddon.update.enabled", false);
user_pref("extensions.blocklist.enabled", false);
user_pref("app.update.enabled", false);
user_pref("app.update.auto", false);
user_pref("app.normandy.enabled", false);
user_pref("app.normandy.api_url", "");
user_pref("app.shield.optoutstudies.enabled", false);
user_pref("browser.discovery.enabled", false);
user_pref("browser.safebrowsing.malware.enabled", false);
user_pref("browser.safebrowsing.phishing.enabled", false);
user_pref("browser.safebrowsing.downloads.enabled", false);
user_pref("browser.safebrowsing.downloads.remote.enabled", false);
user_pref("browser.safebrowsing.blockedURIs.enabled", false);
user_pref("browser.safebrowsing.update.enabled", false);
user_pref("identity.fxaccounts.enabled", false);
user_pref("browser.translations.enable", false);
user_pref("browser.ml.enable", false);
user_pref("browser.ml.chat.enabled", false);

// --- telemetry and reporting off
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("datareporting.usage.uploadEnabled", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("toolkit.telemetry.unified", false);
user_pref("toolkit.telemetry.archive.enabled", false);
user_pref("toolkit.telemetry.server", "");
user_pref("toolkit.coverage.opt-out", true);
user_pref("browser.crashReports.unsubmittedCheck.enabled", false);
user_pref("browser.tabs.crashReporting.sendReport", false);
user_pref("browser.newtabpage.activity-stream.feeds.telemetry", false);
user_pref("browser.newtabpage.activity-stream.telemetry", false);
user_pref("browser.ping-centre.telemetry", false);

// --- permissions: nothing a reader needs
user_pref("permissions.default.camera", 2);
user_pref("permissions.default.microphone", 2);
user_pref("permissions.default.geo", 2);
user_pref("permissions.default.desktop-notification", 2);
user_pref("permissions.default.xr", 2);
user_pref("dom.webnotifications.enabled", false);
user_pref("signon.rememberSignons", false);
user_pref("devtools.debugger.remote-enabled", false);
