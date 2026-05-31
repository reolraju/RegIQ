/* analytics.js — privacy-conscious, anonymous usage analytics via PostHog.

   This script is a no-op unless a PostHog project key is configured at runtime
   (window.REGIQ_ANALYTICS.posthogKey, injected by the frontend server's
   /env.js endpoint). That keeps local/dev runs working unchanged when no key
   is set.

   What we track:
     - page visits (how many people use RegIQ) — captured automatically
     - a "query_submitted" event carrying only non-identifying metadata
       (chosen regulator filter, whether a date range was used, the agent
       intent and whether the answer was grounded)
   What we deliberately DO NOT track:
     - the question text or any user-entered content
     - names, emails, or any direct identifiers (the app has no login)
   Approximate location (country / region / city) is derived server-side by
   PostHog from the request IP — no geolocation code runs in the browser. */
(function () {
  var cfg = window.REGIQ_ANALYTICS || {};
  var key = cfg.posthogKey;

  // Safe default so callers never need to null-check `regiqTrack`.
  window.regiqTrack = function () {};

  // No key configured → analytics disabled. Leave the no-op tracker in place.
  if (!key) return;

  var host = cfg.posthogHost || "https://us.i.posthog.com";

  // --- PostHog official web snippet (array stub + async loader) ---
  !function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],u.toString=function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e},u.people.toString=function(){return u.toString(1)+".people (stub)"},o="init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagPayload isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug getPageViewId captureTraceFeedback captureTraceMetric".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);

  window.posthog.init(key, {
    api_host: host,
    // Don't create per-person profiles — we only want anonymous usage counts
    // and aggregate location, not individual identities.
    person_profiles: "identified_only",
    capture_pageview: true,       // "how many people used this"
    autocapture: false,           // no blanket click/DOM capture — keep it lean
    disable_session_recording: true,
    respect_dnt: true,            // honour browser "Do Not Track"
  });

  // Lightweight, error-safe wrapper used by the app to record usage events.
  window.regiqTrack = function (event, props) {
    try {
      window.posthog.capture(event, props || {});
    } catch (e) {
      /* analytics must never break the app */
    }
  };
})();
