(function () {
  "use strict";

  // No production asset is embedded in distributed code. Same-origin configuration
  // must explicitly enable measurement; consent remains independently required.
  var pixelId = "";
  var googleTagId = "";
  var measurementConfigUrl = "/api/marketing/measurement-config/";
  var consentKey = "nc_privacy_consent_v1";
  var consentGranted = "granted";
  var consentDenied = "denied";
  var attributionKey = "nc_ad_attribution_v1";
  var googleSessionAttributesKey = "nc_google_session_attributes_v1";
  var attributionTtlMs = 90 * 24 * 60 * 60 * 1000;
  var attributionFields = [
    "fbclid", "gclid", "gbraid", "wbraid",
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"
  ];
  var pixelInitialized = false;
  var googleConsentInitialized = false;
  var googleTagInitialized = false;
  var measurementConfigLoaded = false;
  var lastTrackedLocation = "";

  var copy = {
    zh: {
      title: "隐私选项",
      body: "仅在追踪已启用且您同意后，我们才使用广告测量及归因。提交询盘时，经配置的服务器回传可能包含散列联系方式、浏览器及广告活动标识。拒绝不影响提交询盘。",
      accept: "同意", reject: "拒绝", details: "隐私说明", settings: "隐私选项"
    },
    en: {
      title: "Privacy choices",
      body: "With your permission, we use Meta Pixel, Google advertising measurement, campaign attribution and advertising personalization to measure campaigns and improve ad relevance. If you submit an inquiry, contact identifiers are hashed before server-side transmission where required; browser, device and campaign data may also be included.",
      accept: "Accept",
      reject: "Reject",
      details: "Privacy Notice",
      settings: "Privacy choices"
    },
    ar: {
      title: "خيارات الخصوصية",
      body: "بعد موافقتك، نستخدم Meta Pixel وقياس إعلانات Google وإسناد الحملات وتخصيص الإعلانات لقياس الأداء وتحسين ملاءمة الإعلانات. عند إرسال استفسار، تُشفّر معرّفات التواصل تجزئياً قبل الإرسال من الخادم عند الحاجة، وقد تُضمّن بيانات المتصفح والجهاز والحملة.",
      accept: "موافقة",
      reject: "رفض",
      details: "إشعار الخصوصية",
      settings: "خيارات الخصوصية"
    }
  };

  function getLanguage() {
    var requested = new URLSearchParams(window.location.search).get("lang");
    if (requested === "ar" || requested === "en" || requested === "zh") return requested;
    if (document.documentElement.lang.indexOf("zh") === 0) return "zh";
    return document.documentElement.lang === "ar" ? "ar" : "en";
  }

  function getConsent() {
    try {
      return window.localStorage.getItem(consentKey) || "";
    } catch (_error) {
      return "";
    }
  }

  function hasGoogleTag() {
    return /^(AW-\d+|GT-[A-Z0-9]+)$/i.test(googleTagId);
  }

  function initializeGoogleConsent() {
    if (!hasGoogleTag() || googleConsentInitialized) return hasGoogleTag();
    window.dataLayer = window.dataLayer || [];
    window.gtag = window.gtag || function () {
      window.dataLayer.push(arguments);
    };
    window.gtag("consent", "default", {
      ad_storage: "denied",
      analytics_storage: "denied",
      ad_user_data: "denied",
      ad_personalization: "denied",
      wait_for_update: 500
    });
    googleConsentInitialized = true;
    return true;
  }

  function updateGoogleConsent(granted) {
    if (!initializeGoogleConsent()) return;
    var status = granted ? "granted" : "denied";
    window.gtag("consent", "update", {
      ad_storage: status,
      analytics_storage: status,
      ad_user_data: status,
      ad_personalization: status
    });
  }

  function loadGoogleTag() {
    if (!initializeGoogleConsent() || getConsent() !== consentGranted) return;
    updateGoogleConsent(true);
    if (googleTagInitialized) return;

    var script = document.createElement("script");
    script.async = true;
    script.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(googleTagId);
    document.head.appendChild(script);

    window.gtag("js", new Date());
    window.gtag("config", googleTagId);
    googleTagInitialized = true;
  }

  function loadMeasurementConfig() {
    if (measurementConfigLoaded || typeof window.fetch !== "function") return;
    measurementConfigLoaded = true;
    window.fetch(measurementConfigUrl, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" }
    }).then(function (response) {
      return response.ok ? response.json() : null;
    }).then(function (payload) {
      if (!payload || !payload.ok) return;
      var configuredPixelId = String(payload.meta_pixel_id || "").trim();
      if (payload.meta_pixel_enabled && /^\d{5,25}$/.test(configuredPixelId)) {
        pixelId = configuredPixelId;
        if (getConsent() === consentGranted) loadPixel();
      }
      var configuredTagId = String(payload.google_tag_id || "").trim().toUpperCase();
      if (payload.google_tag_enabled && /^(AW-\d+|GT-[A-Z0-9]+)$/.test(configuredTagId)) {
        googleTagId = configuredTagId;
        initializeGoogleConsent();
        if (getConsent() === consentGranted) loadGoogleTag();
      }
      if (pixelId || hasGoogleTag()) {
        ensurePrivacyControls();
        if (!getConsent()) showConsent();
      }
    }).catch(function () {
      // Measurement remains disabled when the same-origin config is unavailable.
    });
  }

  function setConsent(value) {
    try {
      window.localStorage.setItem(consentKey, value);
    } catch (_error) {
      // Browsing still works when storage is unavailable.
    }
    if (value === consentGranted) {
      captureAdAttribution();
      captureGoogleSessionAttributes();
      updateGoogleConsent(true);
    } else {
      clearAdAttribution();
      updateGoogleConsent(false);
    }
    document.dispatchEvent(new CustomEvent("nc:privacy-consent", {
      detail: { status: value }
    }));
  }

  function clearAdAttribution() {
    try {
      window.localStorage.removeItem(attributionKey);
      window.localStorage.removeItem(googleSessionAttributesKey);
    } catch (_error) {
      // Browsing and form submission still work without persistent storage.
    }
  }

  function readAdAttribution() {
    if (getConsent() !== consentGranted) return {};
    try {
      var stored = JSON.parse(window.localStorage.getItem(attributionKey) || "{}");
      if (!stored.expires_at || Number(stored.expires_at) <= Date.now()) {
        clearAdAttribution();
        return {};
      }
      return stored.values && typeof stored.values === "object" ? stored.values : {};
    } catch (_error) {
      clearAdAttribution();
      return {};
    }
  }

  function captureAdAttribution() {
    if (getConsent() !== consentGranted) return {};
    var values = Object.assign({}, readAdAttribution());
    var params = new URLSearchParams(window.location.search);
    var changed = false;
    attributionFields.forEach(function (key) {
      var maxLength = key.indexOf("utm_") === 0 ? 200 : 500;
      var value = String(params.get(key) || "").trim().slice(0, maxLength);
      if (value) {
        values[key] = value;
        changed = true;
      }
    });
    if (changed) {
      try {
        window.localStorage.setItem(attributionKey, JSON.stringify({
          captured_at: new Date().toISOString(),
          expires_at: Date.now() + attributionTtlMs,
          values: values
        }));
      } catch (_error) {
        // Current-page attribution is still returned when storage is unavailable.
      }
    }
    return values;
  }

  function base64UrlEncode(value) {
    var bytes;
    if (typeof window.TextEncoder === "function") {
      bytes = new window.TextEncoder().encode(value);
      var binary = "";
      for (var index = 0; index < bytes.length; index += 1) {
        binary += String.fromCharCode(bytes[index]);
      }
      return window.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    }
    return window.btoa(unescape(encodeURIComponent(value)))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  }

  function readGoogleSessionAttributes() {
    if (getConsent() !== consentGranted) return "";
    try {
      var stored = JSON.parse(window.localStorage.getItem(googleSessionAttributesKey) || "{}");
      if (!stored.expires_at || Number(stored.expires_at) <= Date.now()) {
        window.localStorage.removeItem(googleSessionAttributesKey);
        return "";
      }
      return typeof stored.encoded === "string" ? stored.encoded : "";
    } catch (_error) {
      try {
        window.localStorage.removeItem(googleSessionAttributesKey);
      } catch (_storageError) {
        // Form submission still works without persistent measurement data.
      }
      return "";
    }
  }

  function captureGoogleSessionAttributes() {
    if (getConsent() !== consentGranted) return "";
    var searchParams = new URLSearchParams(window.location.search);
    var hasGoogleAdSignal = Array.from(searchParams.keys()).some(function (key) {
      return key.indexOf("gad_") === 0;
    }) || searchParams.has("gclid") || searchParams.has("gbraid") || searchParams.has("wbraid");
    if (!hasGoogleAdSignal) return readGoogleSessionAttributes();

    var userAgent = String(window.navigator.userAgent || "");
    if (!userAgent || userAgent.length > 1024) return readGoogleSessionAttributes();

    var values = {};
    searchParams.forEach(function (value, key) {
      if (key.indexOf("gad_") === 0 && key.length <= 80) {
        values[key] = String(value || "").slice(0, 500);
      }
    });
    if (!/^\d{1,30}$/.test(values.gad_source || "") || !/^\d{1,30}$/.test(values.gad_campaignid || "")) {
      try {
        window.localStorage.removeItem(googleSessionAttributesKey);
      } catch (_error) {
        // The separate click ID can still be submitted when storage is unavailable.
      }
      return "";
    }
    values.session_start_time_usec = String(Date.now() * 1000);
    if (/^https?:\/\//i.test(window.location.href) && window.location.href.length <= 2000) {
      values.landing_page_url = window.location.href;
    }
    if (document.referrer && /^https?:\/\//i.test(document.referrer) && document.referrer.length <= 2000) {
      values.landing_page_referrer = document.referrer;
    }
    values.landing_page_user_agent = userAgent;

    var encoded = base64UrlEncode(JSON.stringify(values));
    if (!encoded || encoded.length > 8192) return readGoogleSessionAttributes();
    try {
      window.localStorage.setItem(googleSessionAttributesKey, JSON.stringify({
        captured_at: new Date().toISOString(),
        expires_at: Date.now() + attributionTtlMs,
        encoded: encoded
      }));
    } catch (_error) {
      // The current-page value can still accompany the form submission.
    }
    return encoded;
  }

  function readCookie(name) {
    var prefix = name + "=";
    var cookies = document.cookie ? document.cookie.split(";") : [];
    for (var index = 0; index < cookies.length; index += 1) {
      var item = cookies[index].trim();
      if (item.indexOf(prefix) === 0) return decodeURIComponent(item.slice(prefix.length));
    }
    return "";
  }

  function metaIdentifiers() {
    if (getConsent() !== consentGranted) return {};
    var params = new URLSearchParams(window.location.search);
    var attribution = captureAdAttribution();
    var fbclid = (params.get("fbclid") || attribution.fbclid || "").slice(0, 500);
    var fbc = readCookie("_fbc");
    if (!fbc && fbclid) fbc = "fb.1." + Date.now() + "." + fbclid;
    return {
      fbp: readCookie("_fbp").slice(0, 255),
      fbc: fbc.slice(0, 255),
      fbclid: fbclid
    };
  }

  function getPageContext() {
    var body = document.body || {};
    var dataset = body.dataset || {};
    var params = new URLSearchParams(window.location.search);
    var pageType = "home";

    if (dataset.productItem) pageType = "product_detail";
    else if (dataset.productPage) pageType = "product_family";
    else if (dataset.articlePage) pageType = "technical_article";
    else if (dataset.sitePage) pageType = dataset.sitePage;

    var context = {
      content_name: document.title,
      content_category: pageType,
      page_path: window.location.pathname,
      site_language: getLanguage()
    };

    if (dataset.productSystem) context.product_family = dataset.productSystem;
    if (dataset.productPage) context.product_family = dataset.productPage;
    if (dataset.productItem) context.product_item = dataset.productItem;
    if (dataset.articlePage) context.article_slug = dataset.articlePage;

    ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"].forEach(function (key) {
      var value = params.get(key);
      if (value) context[key] = value.slice(0, 200);
    });

    return context;
  }

  function send(eventName, params, options) {
    if (!pixelInitialized || getConsent() !== consentGranted || typeof window.fbq !== "function") return;
    if (options) window.fbq("track", eventName, params || {}, options);
    else window.fbq("track", eventName, params || {});
  }

  function sendCustom(eventName, params) {
    if (!pixelInitialized || getConsent() !== consentGranted || typeof window.fbq !== "function") return;
    window.fbq("trackCustom", eventName, params || {});
  }

  function trackLocation(force) {
    if (!pixelInitialized || getConsent() !== consentGranted) return;
    var marker = window.location.pathname + window.location.search + "|" + getLanguage();
    if (!force && marker === lastTrackedLocation) return;
    lastTrackedLocation = marker;

    var context = getPageContext();
    send("PageView", context);

    if (context.content_category === "product_detail" || context.content_category === "product_family") {
      send("ViewContent", Object.assign({}, context, {
        content_type: context.content_category === "product_detail" ? "product" : "product_group",
        content_ids: [context.product_family, context.product_item].filter(Boolean)
      }));
    } else if (context.content_category === "technical_article") {
      send("ViewContent", context);
    }
  }

  function loadPixel() {
    if (!pixelId || getConsent() !== consentGranted) return;
    if (pixelInitialized) {
      trackLocation(false);
      return;
    }

    if (!window.fbq) {
      (function (f, b, e, v, n, t, s) {
        if (f.fbq) return;
        n = f.fbq = function () {
          n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments);
        };
        if (!f._fbq) f._fbq = n;
        n.push = n;
        n.loaded = true;
        n.version = "2.0";
        n.queue = [];
        t = b.createElement(e);
        t.async = true;
        t.src = v;
        s = b.getElementsByTagName(e)[0];
        s.parentNode.insertBefore(t, s);
      })(window, document, "script", "https://connect.facebook.net/en_US/fbevents.js");
    }

    window.fbq("init", pixelId);
    pixelInitialized = true;
    trackLocation(true);
  }

  function injectConsentStyles() {
    if (document.getElementById("nc-consent-styles")) return;
    var style = document.createElement("style");
    style.id = "nc-consent-styles";
    style.textContent = [
      ".nc-consent{position:fixed;inset:auto 0 0;z-index:10000;background:#fff;border-top:1px solid #d8dee8;box-shadow:0 -12px 32px rgba(10,24,48,.12);font-family:Arial,sans-serif;color:#0b172a}",
      ".nc-consent[hidden]{display:none}",
      ".nc-consent__inner{max-width:1180px;margin:0 auto;padding:18px 24px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:24px;align-items:center}",
      ".nc-consent strong{display:block;font-size:16px;line-height:1.3;margin:0 0 6px}",
      ".nc-consent p{font-size:13px;line-height:1.55;margin:0;max-width:820px;color:#43516a}",
      ".nc-consent a{color:#0a45d8;text-decoration:underline;text-underline-offset:3px}",
      ".nc-consent__actions{display:flex;gap:10px;align-items:center}",
      ".nc-consent button{min-height:40px;padding:0 18px;border:1px solid #0a45d8;border-radius:4px;background:#fff;color:#0a45d8;font:700 13px/1 Arial,sans-serif;cursor:pointer}",
      ".nc-consent button[data-consent-accept]{background:#0a45d8;color:#fff}",
      ".nc-privacy-settings{border:0;background:transparent;padding:0;color:inherit;font:inherit;cursor:pointer;text-align:inherit}",
      ".nc-privacy-settings:hover{text-decoration:underline}",
      "@media(max-width:720px){.nc-consent__inner{grid-template-columns:1fr;padding:12px 14px;gap:9px}.nc-consent strong{font-size:14px;margin-bottom:3px}.nc-consent p{font-size:12px;line-height:1.4}.nc-consent__actions{display:grid;grid-template-columns:1fr 1fr;gap:8px}.nc-consent button{width:100%;min-height:38px}}"
    ].join("");
    document.head.appendChild(style);
  }

  function privacyUrl() {
    return "/privacy/?lang=" + getLanguage();
  }

  function ensurePrivacyControls() {
    if (!pixelId && !hasGoogleTag()) return;
    injectConsentStyles();
    var language = getLanguage();
    var text = copy[language] || copy.en;
    var footerLinks = document.querySelector(".site-footer .footer-links, .footer-main");

    if (footerLinks && !footerLinks.querySelector("[data-privacy-settings]")) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "nc-privacy-settings";
      button.setAttribute("data-privacy-settings", "");
      footerLinks.appendChild(button);
    }

    document.querySelectorAll("[data-privacy-settings]").forEach(function (node) {
      node.textContent = text.settings;
    });
    document.querySelectorAll("[data-privacy-notice-link]").forEach(function (node) {
      node.setAttribute("href", privacyUrl());
    });
  }

  function showConsent() {
    if (!pixelId && !hasGoogleTag()) return;
    injectConsentStyles();
    var current = document.getElementById("nc-consent");
    if (current) current.remove();

    var language = getLanguage();
    var text = copy[language] || copy.en;
    var section = document.createElement("section");
    section.id = "nc-consent";
    section.className = "nc-consent";
    section.setAttribute("role", "dialog");
    section.setAttribute("aria-label", text.title);
    section.dir = language === "ar" ? "rtl" : "ltr";
    section.innerHTML =
      '<div class="nc-consent__inner">' +
        '<div><strong>' + text.title + '</strong><p>' + text.body + ' <a data-privacy-notice-link href="' + privacyUrl() + '">' + text.details + '</a></p></div>' +
        '<div class="nc-consent__actions">' +
          '<button type="button" data-consent-reject>' + text.reject + '</button>' +
          '<button type="button" data-consent-accept>' + text.accept + '</button>' +
        '</div>' +
      '</div>';

    section.querySelector("[data-consent-accept]").addEventListener("click", function () {
      setConsent(consentGranted);
      section.remove();
      loadPixel();
      loadGoogleTag();
    });
    section.querySelector("[data-consent-reject]").addEventListener("click", function () {
      setConsent(consentDenied);
      section.remove();
    });
    document.body.appendChild(section);
  }

  function handleRouteChange() {
    window.setTimeout(function () {
      ensurePrivacyControls();
      if (getConsent() === consentGranted) trackLocation(false);
    }, 0);
  }

  ["pushState", "replaceState"].forEach(function (methodName) {
    var original = window.history[methodName];
    if (typeof original !== "function") return;
    window.history[methodName] = function () {
      var result = original.apply(this, arguments);
      handleRouteChange();
      return result;
    };
  });
  window.addEventListener("popstate", handleRouteChange);

  document.addEventListener("click", function (event) {
    var settingsButton = event.target && event.target.closest ? event.target.closest("[data-privacy-settings]") : null;
    if (settingsButton) {
      event.preventDefault();
      showConsent();
      return;
    }

    var link = event.target && event.target.closest ? event.target.closest("a") : null;
    if (!link) return;
    var href = link.getAttribute("href") || "";
    var context = getPageContext();

    if (href.indexOf("mailto:") === 0) {
      send("Contact", Object.assign({}, context, { contact_method: "email" }));
    } else if (href.indexOf("tel:") === 0) {
      send("Contact", Object.assign({}, context, { contact_method: "phone" }));
    } else if (href.indexOf("wa.me/") !== -1 || href.indexOf("api.whatsapp.com") !== -1) {
      send("Contact", Object.assign({}, context, { contact_method: "whatsapp" }));
    }
  }, true);

  document.addEventListener("nc:form-started", function (event) {
    var detail = event.detail || {};
    sendCustom("FormStart", Object.assign({}, getPageContext(), {
      content_category: "project_inquiry",
      form_code: detail.formCode || "project-inquiry",
      inquiry_type: detail.inquiryType || "",
      product_category: detail.productCategory || ""
    }));
  });

  document.addEventListener("nc:lead-accepted", function (event) {
    var detail = event.detail || {};
    var eventId = detail.eventId || "";
    var params = Object.assign({}, getPageContext(), {
      content_category: "project_inquiry",
      form_code: detail.formCode || "project-inquiry",
      inquiry_type: detail.inquiryType || "",
      product_category: detail.productCategory || ""
    });
    send("Lead", params, eventId ? { eventID: eventId } : null);
  });

  window.ncMetaTrack = send;
  window.ncPrivacyChoices = showConsent;
  window.ncMetaIdentifiers = metaIdentifiers;
  window.ncAdAttribution = captureAdAttribution;
  window.ncGoogleSessionAttributes = captureGoogleSessionAttributes;
  window.ncPrivacyConsentGranted = function () {
    return getConsent() === consentGranted;
  };
  window.ncGoogleTagStatus = function () {
    return {
      configured: hasGoogleTag(),
      loaded: googleTagInitialized,
      consent: getConsent() || "unset"
    };
  };

  function initialize() {
    initializeGoogleConsent();
    loadMeasurementConfig();
    ensurePrivacyControls();
    var consent = getConsent();
    if (consent === consentGranted) {
      captureAdAttribution();
      captureGoogleSessionAttributes();
      loadPixel();
      loadGoogleTag();
    }
    else if (consent !== consentDenied) showConsent();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true });
  else initialize();
})();
