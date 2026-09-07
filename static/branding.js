// Default FELTUS branding (used before fetch and when unauthenticated)
const BRANDING = {
  appName: "FELTUS Extraction Lab",
  brandName: "FELTUS",
  brandInitial: "F",
  brandTagline: "Extraction Lab",
  appTitle: "FELTUS Universal Evidence Extraction Lab",
  appVersion: "3.1.0",
  maxUploadMB: 250,
  logoUrl: "/images/logo.png",
  faviconUrl: null,
  primaryColor: "#07172a",
  secondaryColor: "#40bb90",
  accentColor: "#50d6a6",
  companyName: "FELTUS",
  supportEmail: null,
  footerText: "FELTUS Extraction Lab",
  privacyUrl: null,
  termsUrl: null,
};

function applyBranding(brand) {
  Object.assign(BRANDING, brand);

  document.title = brand.appName || BRANDING.appName;

  const brandMark = document.getElementById("brand-mark");
  const brandName = document.getElementById("brand-name");
  const brandTagline = document.getElementById("brand-tagline");
  const brandLogo = document.getElementById("brand-logo");
  const brandMarkSidebar = document.getElementById("brand-mark-sidebar");
  const brandLogoSidebar = document.getElementById("brand-logo-sidebar");
  const brandMarkMobile = document.getElementById("brand-mark-mobile");
  const brandLogoMobile = document.getElementById("brand-logo-mobile");
  const loginBrandMark = document.getElementById("login-brand-mark");
  const loginAppName = document.getElementById("login-app-name");
  const loginLogo = document.getElementById("login-logo");
  const heroLogo = document.getElementById("hero-logo");

  const initial = brand.brandInitial || (brand.brandName || "F").charAt(0).toUpperCase();
  const name = brand.brandName || "FELTUS";
  const tagline = brand.appName ? brand.appName.replace(brand.brandName || "", "").trim() : "Extraction Lab";

  if (brandMark) brandMark.textContent = initial;
  if (brandName) brandName.textContent = name;
  if (brandTagline) brandTagline.textContent = tagline;
  if (loginBrandMark) loginBrandMark.textContent = initial;
  if (loginAppName) loginAppName.textContent = brand.appName || "FELTUS Extraction Lab";

  // Logo images
  if (brand.logoUrl) {
    if (brandLogo) { brandLogo.src = brand.logoUrl; brandLogo.hidden = false; }
    if (brandLogoSidebar) { brandLogoSidebar.src = brand.logoUrl; brandLogoSidebar.hidden = false; }
    if (brandLogoMobile) { brandLogoMobile.src = brand.logoUrl; brandLogoMobile.hidden = false; }
    if (loginLogo) { loginLogo.src = brand.logoUrl; loginLogo.hidden = false; }
    if (heroLogo) { heroLogo.src = brand.logoUrl; heroLogo.hidden = false; }
    if (brandMark) brandMark.hidden = true;
    if (brandMarkSidebar) brandMarkSidebar.hidden = true;
    if (brandMarkMobile) brandMarkMobile.hidden = true;
    if (loginBrandMark) loginBrandMark.hidden = true;
  } else {
    if (brandLogo) brandLogo.hidden = true;
    if (brandLogoSidebar) brandLogoSidebar.hidden = true;
    if (brandLogoMobile) brandLogoMobile.hidden = true;
    if (loginLogo) loginLogo.hidden = true;
    if (heroLogo) heroLogo.hidden = true;
    if (brandMark) brandMark.hidden = false;
    if (brandMarkSidebar) brandMarkSidebar.hidden = false;
    if (brandMarkMobile) brandMarkMobile.hidden = false;
    if (loginBrandMark) loginBrandMark.hidden = false;
  }

  // Update CSS variables
  const root = document.documentElement;
  if (brand.primaryColor) {
    root.style.setProperty("--navy", brand.primaryColor);
    root.style.setProperty("--brand-primary", brand.primaryColor);
  }
  if (brand.secondaryColor) {
    root.style.setProperty("--green", brand.secondaryColor);
    root.style.setProperty("--brand-secondary", brand.secondaryColor);
  }
  if (brand.accentColor) {
    root.style.setProperty("--green2", brand.accentColor);
    root.style.setProperty("--brand-accent", brand.accentColor);
  }

  // Favicon
  if (brand.faviconUrl) {
    let link = document.querySelector("link[rel='icon']");
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      document.head.appendChild(link);
    }
    link.href = brand.faviconUrl;
  }

  // Support links
  const loginSupport = document.getElementById("login-support");
  const loginSupportWrap = document.getElementById("login-support-wrap");
  const dashboardSupport = document.getElementById("support-link");
  const mobileDashboardSupport = document.getElementById("mobile-support-link");
  if (brand.supportEmail) {
    if (loginSupport) {
      loginSupport.href = `mailto:${brand.supportEmail}`;
      loginSupport.hidden = false;
    }
    if (loginSupportWrap) loginSupportWrap.hidden = false;
    if (dashboardSupport) dashboardSupport.href = `mailto:${brand.supportEmail}`;
    if (mobileDashboardSupport) mobileDashboardSupport.href = `mailto:${brand.supportEmail}`;
  } else {
    if (loginSupport) loginSupport.hidden = true;
    if (loginSupportWrap) loginSupportWrap.hidden = true;
    if (dashboardSupport) dashboardSupport.href = "#";
    if (mobileDashboardSupport) mobileDashboardSupport.href = "#";
  }

  // Logo could be applied to brand mark if logo_url is provided (overrides text)
  [brandMark, brandMarkSidebar, brandMarkMobile, loginBrandMark].forEach(mark => {
    if (brand.logoUrl && mark) {
      mark.textContent = "";
      mark.classList.add("has-logo");
      mark.style.backgroundImage = `url(${brand.logoUrl})`;
      mark.style.backgroundSize = "contain";
      mark.style.backgroundRepeat = "no-repeat";
      mark.style.backgroundPosition = "center";
    } else if (mark) {
      mark.classList.remove("has-logo");
      mark.style.backgroundImage = "";
    }
  });
}

// Fetch public tenant branding before login (resolved by hostname or ?slug=)
(async function loadBranding() {
  try {
    const params = new URLSearchParams(window.location.search);
    const slug = params.get("slug");
    const url = slug ? `/api/branding/public?slug=${encodeURIComponent(slug)}` : "/api/branding/public";
    const response = await fetch(url);
    if (response.ok) {
      const brand = await response.json();
      applyBranding({
        appName: brand.app_name,
        brandName: brand.brand_name,
        brandInitial: brand.brand_name ? brand.brand_name.charAt(0).toUpperCase() : "F",
        brandTagline: brand.app_name,
        logoUrl: brand.logo_url,
        faviconUrl: brand.favicon_url,
        primaryColor: brand.primary_color,
        secondaryColor: brand.secondary_color,
        accentColor: brand.accent_color,
        companyName: brand.company_name,
        supportEmail: brand.support_email,
        footerText: brand.footer_text,
        privacyUrl: brand.privacy_url,
        termsUrl: brand.terms_url,
      });
    }
  } catch (e) {
    // Keep default FELTUS branding on error / unknown tenant
  }
})();

// Apply the default FELTUS branding once the DOM is ready, then let the API override it
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => applyBranding(BRANDING));
} else {
  applyBranding(BRANDING);
}
