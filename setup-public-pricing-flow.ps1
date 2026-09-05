$ErrorActionPreference = "Stop"

$root = "C:\Users\kymfe\OneDrive\Desktop\FELTUS Extraction Lab"

$main    = Join-Path $root "app\main.py"
$index   = Join-Path $root "static\index.html"
$pricing = Join-Path $root "static\pricing.html"

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

# ============================================================
# BACKUPS
# ============================================================

Copy-Item $main    "$main.before-public-pricing-$stamp.bak"
Copy-Item $index   "$index.before-public-pricing-$stamp.bak"
Copy-Item $pricing "$pricing.before-public-pricing-$stamp.bak"

Write-Host "Backups created." -ForegroundColor Green


# ============================================================
# 1. MAKE / THE PUBLIC PRICING PAGE
#    MOVE THE EXISTING APP TO /app
# ============================================================

$mainText = Get-Content $main -Raw

$oldHome = '@app\.get\("/", response_class=HTMLResponse\)\s*\r?\ndef index\(\) -> FileResponse:'

if ($mainText -notmatch $oldHome) {
    throw "Could not locate the existing / homepage route in app\main.py."
}

$mainText = [regex]::Replace(
    $mainText,
    $oldHome,
    '@app.get("/app", response_class=HTMLResponse)' + "`r`n" +
    'def index() -> FileResponse:',
    1
)

# Make existing /pricing page also serve at /
if ($mainText -match '@app\.get\("/pricing", response_class=HTMLResponse\)') {

    if ($mainText -notmatch '@app\.get\("/", response_class=HTMLResponse\)\s*\r?\n@app\.get\("/pricing"') {

        $mainText = $mainText.Replace(
            '@app.get("/pricing", response_class=HTMLResponse)',
            '@app.get("/", response_class=HTMLResponse)' + "`r`n" +
            '@app.get("/pricing", response_class=HTMLResponse)'
        )
    }

} else {

    throw "Could not locate the /pricing route in app\main.py."
}

Set-Content $main $mainText -Encoding UTF8

Write-Host "Root route now points to public pricing." -ForegroundColor Green
Write-Host "Existing FELTUS app moved to /app." -ForegroundColor Green


# ============================================================
# 2. PRICING PAGE PLAN HANDOFF
# ============================================================

$pricingText = Get-Content $pricing -Raw

# Remove prior copy of our handoff block if script is re-run.
$pricingText = [regex]::Replace(
    $pricingText,
    '(?s)\s*<!-- FELTUS PUBLIC PLAN HANDOFF START -->.*?<!-- FELTUS PUBLIC PLAN HANDOFF END -->',
    ''
)

$pricingScript = @'

<!-- FELTUS PUBLIC PLAN HANDOFF START -->
<script>
(function () {

    const allowedPlans = [
        "free_trial",
        "starter",
        "professional",
        "business"
    ];

    function choosePlan(plan, interval = "month") {

        if (!allowedPlans.includes(plan)) {
            console.error("Invalid FELTUS plan:", plan);
            return;
        }

        localStorage.setItem(
            "feltus_selected_plan",
            plan
        );

        localStorage.setItem(
            "feltus_selected_interval",
            interval
        );

        const params = new URLSearchParams();

        params.set("plan", plan);
        params.set("interval", interval);

        window.location.href =
            "/app?" + params.toString();
    }

    /*
     * Existing paid pricing buttons already call checkout(plan).
     * Override that behavior so unauthenticated users go through
     * FELTUS identity FIRST rather than Stripe first.
     */
    window.checkout = function(plan) {
        choosePlan(plan, "month");
    };

    window.chooseFeltusPlan = choosePlan;


    /*
     * Make sure Free Trial has a live button.
     * This works even if the existing card did not include one.
     */
    function ensureFreeButton() {

        const cards = Array.from(
            document.querySelectorAll(".plan")
        );

        const freeCard = cards.find(card =>
            card.textContent
                .toLowerCase()
                .includes("free trial")
        );

        if (!freeCard) {
            return;
        }

        if (
            freeCard.querySelector(
                "[data-feltus-free-button]"
            )
        ) {
            return;
        }

        const button = document.createElement("button");

        button.type = "button";
        button.textContent = "Get Started Free";
        button.setAttribute(
            "data-feltus-free-button",
            "true"
        );

        button.addEventListener(
            "click",
            function () {
                choosePlan(
                    "free_trial",
                    "month"
                );
            }
        );

        freeCard.appendChild(button);
    }

    if (document.readyState === "loading") {

        document.addEventListener(
            "DOMContentLoaded",
            ensureFreeButton
        );

    } else {

        ensureFreeButton();
    }

})();
</script>
<!-- FELTUS PUBLIC PLAN HANDOFF END -->

'@

$pricingText = $pricingText.Replace(
    "</body>",
    $pricingScript + "`r`n</body>"
)

Set-Content $pricing $pricingText -Encoding UTF8

Write-Host "Pricing plan handoff installed." -ForegroundColor Green
Write-Host "Free Trial button installed." -ForegroundColor Green


# ============================================================
# 3. APP PAGE: REMEMBER SELECTED PLAN AND CONTINUE AFTER LOGIN
# ============================================================

$indexText = Get-Content $index -Raw

$indexText = [regex]::Replace(
    $indexText,
    '(?s)\s*<!-- FELTUS PLAN CONTINUATION START -->.*?<!-- FELTUS PLAN CONTINUATION END -->',
    ''
)

$appScript = @'

<!-- FELTUS PLAN CONTINUATION START -->
<script>
(function () {

    const params =
        new URLSearchParams(
            window.location.search
        );

    const queryPlan =
        params.get("plan");

    const queryInterval =
        params.get("interval") || "month";

    const allowedPlans = [
        "free_trial",
        "starter",
        "professional",
        "business"
    ];


    /*
     * Preserve selection from the public pricing page.
     */
    if (
        queryPlan &&
        allowedPlans.includes(queryPlan)
    ) {

        localStorage.setItem(
            "feltus_selected_plan",
            queryPlan
        );

        localStorage.setItem(
            "feltus_selected_interval",
            queryInterval
        );
    }


    let checkoutInProgress = false;


    async function continueSelectedPlan() {

        const plan =
            localStorage.getItem(
                "feltus_selected_plan"
            );

        const interval =
            localStorage.getItem(
                "feltus_selected_interval"
            ) || "month";

        if (
            !plan ||
            !allowedPlans.includes(plan)
        ) {
            return;
        }


        /*
         * Wait until the existing FELTUS login succeeds.
         *
         * /api/auth/me uses the existing Supabase session cookie.
         */
        let authResponse;

        try {

            authResponse = await fetch(
                "/api/auth/me",
                {
                    credentials: "same-origin",
                    cache: "no-store"
                }
            );

        } catch (error) {

            return;
        }


        if (!authResponse.ok) {

            /*
             * User is still on the login screen.
             * Do nothing. Existing FELTUS login remains untouched.
             */
            return;
        }


        /*
         * FREE:
         * Organization already receives the 10-page lifetime
         * free entitlement from FELTUS.
         *
         * No Stripe call.
         */
        if (plan === "free_trial") {

            localStorage.removeItem(
                "feltus_selected_plan"
            );

            localStorage.removeItem(
                "feltus_selected_interval"
            );

            history.replaceState(
                {},
                "",
                "/app"
            );

            return;
        }


        /*
         * PAID:
         * Once authenticated, continue automatically into the
         * existing Stripe Checkout endpoint.
         */
        if (checkoutInProgress) {
            return;
        }

        checkoutInProgress = true;


        try {

            const response = await fetch(
                "/api/billing/checkout",
                {
                    method: "POST",

                    credentials: "same-origin",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body: JSON.stringify({
                        plan_code: plan,
                        interval: interval
                    })
                }
            );


            const data =
                await response.json();


            if (
                !response.ok ||
                !data.checkout_url
            ) {

                console.error(
                    "FELTUS checkout could not start:",
                    data
                );

                checkoutInProgress = false;

                return;
            }


            localStorage.removeItem(
                "feltus_selected_plan"
            );

            localStorage.removeItem(
                "feltus_selected_interval"
            );


            window.location.href =
                data.checkout_url;


        } catch (error) {

            console.error(
                "FELTUS checkout error:",
                error
            );

            checkoutInProgress = false;
        }
    }


    /*
     * Check immediately for an existing session.
     * If user is logged out, keep checking while they complete
     * the normal FELTUS login form.
     */
    continueSelectedPlan();

    setInterval(
        continueSelectedPlan,
        1200
    );

})();
</script>
<!-- FELTUS PLAN CONTINUATION END -->

'@

$indexText = $indexText.Replace(
    "</body>",
    $appScript + "`r`n</body>"
)

Set-Content $index $indexText -Encoding UTF8

Write-Host "Post-login plan continuation installed." -ForegroundColor Green


# ============================================================
# 4. PYTHON SYNTAX CHECK
# ============================================================

& "$root\.venv\Scripts\python.exe" `
    -m py_compile `
    "$root\app\main.py"

if ($LASTEXITCODE -ne 0) {
    throw "main.py syntax check failed."
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "FELTUS PUBLIC PRICING FLOW INSTALLED" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Public homepage: http://localhost:8000/" -ForegroundColor White
Write-Host "Pricing alias:   http://localhost:8000/pricing" -ForegroundColor White
Write-Host "Login/App:       http://localhost:8000/app" -ForegroundColor White
Write-Host ""
Write-Host "FREE: pricing -> login -> dashboard" -ForegroundColor White
Write-Host "PAID: pricing -> login -> Stripe -> dashboard" -ForegroundColor White
Write-Host ""
Write-Host "NOTE: Existing project currently has login but no create-account route." -ForegroundColor Yellow