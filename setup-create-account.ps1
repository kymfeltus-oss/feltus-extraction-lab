$ErrorActionPreference = "Stop"

$root  = "C:\Users\kymfe\OneDrive\Desktop\FELTUS Extraction Lab"
$main  = Join-Path $root "app\main.py"
$index = Join-Path $root "static\index.html"
$route = Join-Path $root "app\signup_routes.py"

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

Copy-Item $main  "$main.before-signup-$stamp.bak"
Copy-Item $index "$index.before-signup-$stamp.bak"

Write-Host "Backups created." -ForegroundColor Green


# ============================================================
# CREATE BACKEND SIGNUP ROUTE
# ============================================================

$signupPython = @'
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from .config import get_settings


router = APIRouter(
    prefix="/api/auth",
    tags=["auth"],
)

settings = get_settings()


class RegisterRequest(BaseModel):
    full_name: str
    organization_name: str | None = None
    email: str
    password: str


def _service_key() -> str:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    if not key:
        raise HTTPException(
            status_code=503,
            detail="Supabase service role key is not configured.",
        )

    return key


def _supabase_request(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    service_role: bool = False,
    prefer: str | None = None,
):
    base_url = (settings.supabase_url or "").rstrip("/")

    if not base_url:
        raise HTTPException(
            status_code=503,
            detail="Supabase URL is not configured.",
        )

    if service_role:
        api_key = _service_key()
    else:
        api_key = settings.supabase_anon_key or ""

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Supabase API key is not configured.",
        )

    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if prefer:
        headers["Prefer"] = prefer

    data = (
        json.dumps(body).encode("utf-8")
        if body is not None
        else None
    )

    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers=headers,
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=30
        ) as response:

            raw = response.read()

            if not raw:
                return {}

            return json.loads(
                raw.decode("utf-8")
            )

    except urllib.error.HTTPError as exc:

        try:
            detail = json.loads(
                exc.read().decode("utf-8")
            )
        except Exception:
            detail = None

        message = "Supabase request failed."

        if isinstance(detail, dict):
            message = (
                detail.get("msg")
                or detail.get("message")
                or detail.get("error_description")
                or detail.get("error")
                or message
            )

        raise HTTPException(
            status_code=exc.code,
            detail=message,
        ) from None


def _slugify(value: str) -> str:

    value = value.lower().strip()

    value = re.sub(
        r"[^a-z0-9]+",
        "-",
        value,
    )

    value = value.strip("-")

    if not value:
        value = "workspace"

    return (
        value[:40]
        + "-"
        + uuid.uuid4().hex[:8]
    )


def _delete_auth_user(user_id: str) -> None:

    try:
        _supabase_request(
            "DELETE",
            f"/auth/v1/admin/users/{user_id}",
            service_role=True,
        )
    except Exception:
        pass


def _delete_organization(
    organization_id: str
) -> None:

    try:
        _supabase_request(
            "DELETE",
            (
                "/rest/v1/organizations"
                f"?id=eq.{organization_id}"
            ),
            service_role=True,
            prefer="return=minimal",
        )
    except Exception:
        pass


@router.post("/register")
def register(
    payload: RegisterRequest,
    response: Response,
) -> dict:

    full_name = payload.full_name.strip()
    email = payload.email.strip().lower()
    password = payload.password

    if len(full_name) < 2:
        raise HTTPException(
            status_code=400,
            detail="Please enter your name.",
        )

    if "@" not in email:
        raise HTTPException(
            status_code=400,
            detail="Please enter a valid email address.",
        )

    if len(password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must contain at least 8 characters.",
        )

    organization_name = (
        (payload.organization_name or "").strip()
        or f"{full_name}'s Workspace"
    )

    # --------------------------------------------------------
    # CREATE SUPABASE AUTH USER
    # --------------------------------------------------------

    signup = _supabase_request(
        "POST",
        "/auth/v1/signup",
        {
            "email": email,
            "password": password,
            "data": {
                "full_name": full_name,
            },
        },
    )

    user = signup.get("user") or {}

    user_id = user.get("id")

    if not user_id:
        raise HTTPException(
            status_code=400,
            detail="Supabase did not create the user account.",
        )

    organization_id = None

    try:

        # ----------------------------------------------------
        # CREATE ORGANIZATION
        # Billing trigger automatically creates
        # organization_billing with 10 lifetime free pages.
        # ----------------------------------------------------

        organization_rows = _supabase_request(
            "POST",
            (
                "/rest/v1/organizations"
                "?select=id,name,slug"
            ),
            {
                "name": organization_name,
                "slug": _slugify(
                    organization_name
                ),
            },
            service_role=True,
            prefer="return=representation",
        )

        if (
            not isinstance(
                organization_rows,
                list
            )
            or not organization_rows
        ):
            raise RuntimeError(
                "Organization creation failed."
            )

        organization = (
            organization_rows[0]
        )

        organization_id = (
            organization["id"]
        )

        # ----------------------------------------------------
        # MAKE NEW USER THE ORGANIZATION OWNER
        # ----------------------------------------------------

        _supabase_request(
            "POST",
            "/rest/v1/organization_members",
            {
                "organization_id":
                    organization_id,
                "user_id":
                    user_id,
                "role":
                    "owner",
            },
            service_role=True,
            prefer="return=minimal",
        )

    except Exception as exc:

        if organization_id:
            _delete_organization(
                organization_id
            )

        _delete_auth_user(
            user_id
        )

        if isinstance(
            exc,
            HTTPException
        ):
            raise

        raise HTTPException(
            status_code=500,
            detail=(
                "Account was not completed. "
                "Please try again."
            ),
        ) from exc


    # --------------------------------------------------------
    # SUPABASE MAY RETURN SESSION IMMEDIATELY
    # --------------------------------------------------------

    session = (
        signup.get("session")
        or signup
    )

    access_token = (
        session.get("access_token")
        if isinstance(session, dict)
        else None
    )

    refresh_token = (
        session.get("refresh_token")
        if isinstance(session, dict)
        else None
    )

    expires_in = (
        session.get("expires_in", 3600)
        if isinstance(session, dict)
        else 3600
    )


    # --------------------------------------------------------
    # IF SESSION EXISTS, LOG USER IN IMMEDIATELY
    # --------------------------------------------------------

    if access_token:

        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            secure=False,
            samesite="lax",
            max_age=int(expires_in),
        )

        if refresh_token:

            response.set_cookie(
                key="refresh_token",
                value=refresh_token,
                httponly=True,
                secure=False,
                samesite="lax",
                max_age=86400 * 30,
            )

        response.set_cookie(
            key="active_organization_id",
            value=organization_id,
            httponly=True,
            secure=False,
            samesite="lax",
            max_age=86400 * 30,
        )


    return {
        "created": True,
        "authenticated": bool(
            access_token
        ),
        "requires_confirmation":
            not bool(access_token),

        "user": {
            "id": user_id,
            "email": email,
            "name": full_name,
        },

        "organization": {
            "id": organization_id,
            "name": organization_name,
            "role": "owner",
        },

        "plan": {
            "code": "free_trial",
            "page_limit": 10,
            "usage_period": "lifetime",
        },
    }
'@

Set-Content `
    $route `
    $signupPython `
    -Encoding UTF8

Write-Host "Created app\signup_routes.py" -ForegroundColor Green


# ============================================================
# CONNECT SIGNUP ROUTER TO FASTAPI
# ============================================================

$mainText = Get-Content $main -Raw

if (
    $mainText -notmatch
    'from \.signup_routes import router as signup_router'
) {

    if (
        $mainText -match
        'from \.billing_routes import router as billing_router'
    ) {

        $mainText = $mainText.Replace(
            'from .billing_routes import router as billing_router',
            'from .billing_routes import router as billing_router' +
            "`r`n" +
            'from .signup_routes import router as signup_router'
        )

    } else {

        throw "Could not locate billing router import."
    }
}


if (
    $mainText -notmatch
    'app\.include_router\(signup_router\)'
) {

    if (
        $mainText -match
        'app\.include_router\(billing_router\)'
    ) {

        $mainText = $mainText.Replace(
            'app.include_router(billing_router)',
            'app.include_router(billing_router)' +
            "`r`n" +
            'app.include_router(signup_router)'
        )

    } else {

        throw "Could not locate billing router registration."
    }
}

Set-Content `
    $main `
    $mainText `
    -Encoding UTF8

Write-Host "Signup API connected to FastAPI." -ForegroundColor Green


# ============================================================
# ADD CREATE ACCOUNT UI TO LOGIN PAGE
# ============================================================

$indexText = Get-Content $index -Raw

# Remove old patch if script is re-run.
$indexText = [regex]::Replace(
    $indexText,
    '(?s)\s*<!-- FELTUS CREATE ACCOUNT UI START -->.*?<!-- FELTUS CREATE ACCOUNT UI END -->',
    ''
)

$signupMarkup = @'

<!-- FELTUS CREATE ACCOUNT UI START -->

<div id="auth-switch" class="feltus-auth-switch">
    <span id="auth-switch-label">
        New to FELTUS?
    </span>

    <button
        type="button"
        id="auth-switch-button"
        class="feltus-auth-switch-button">
        Create Account
    </button>
</div>


<form
    id="signup-form"
    class="auth-form"
    hidden>

    <div class="field-group">
        <label for="signup-name">
            Full Name
        </label>

        <input
            id="signup-name"
            type="text"
            name="full_name"
            placeholder="Your name"
            required
            autocomplete="name">
    </div>


    <div class="field-group">
        <label for="signup-organization">
            Company / Workspace
        </label>

        <input
            id="signup-organization"
            type="text"
            name="organization_name"
            placeholder="Company or workspace name"
            autocomplete="organization">
    </div>


    <div class="field-group">
        <label for="signup-email">
            Email Address
        </label>

        <input
            id="signup-email"
            type="email"
            name="email"
            placeholder="you@company.com"
            required
            autocomplete="email"
            autocapitalize="off"
            inputmode="email">
    </div>


    <div class="field-group">
        <label for="signup-password">
            Password
        </label>

        <input
            id="signup-password"
            type="password"
            name="password"
            placeholder="Minimum 8 characters"
            required
            minlength="8"
            autocomplete="new-password">
    </div>


    <div class="field-group">
        <label for="signup-confirm-password">
            Confirm Password
        </label>

        <input
            id="signup-confirm-password"
            type="password"
            name="confirm_password"
            placeholder="Confirm your password"
            required
            minlength="8"
            autocomplete="new-password">
    </div>


    <p
        id="signup-error"
        class="auth-error"
        role="alert"
        hidden>
    </p>


    <p
        id="signup-success"
        class="feltus-signup-success"
        hidden>
    </p>


    <button
        type="submit"
        id="signup-button"
        class="auth-submit">

        <span
            class="spinner"
            aria-hidden="true"
            hidden>
        </span>

        <span class="button-text">
            Create Account
        </span>

        <svg
            class="arrow"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2.5"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true">

            <path d="M5 12h14M13 5l7 7-7 7"/>
        </svg>

    </button>

</form>

<!-- FELTUS CREATE ACCOUNT UI END -->
'@


$loginPattern = '(?s)(<form id="login-form" class="auth-form">.*?</form>)'

if ($indexText -notmatch $loginPattern) {
    throw "Could not find login form in static\index.html."
}

$indexText = [regex]::Replace(
    $indexText,
    $loginPattern,
    {
        param($match)

        $match.Groups[1].Value +
        $signupMarkup
    },
    1
)


# ============================================================
# ADD SIGNUP STYLING
# ============================================================

$signupCss = @'

<!-- FELTUS SIGNUP STYLE START -->
<style>
.feltus-auth-switch {
    margin-top: 18px;
    text-align: center;
    color: rgba(255,255,255,.72);
    font-size: 14px;
}

.feltus-auth-switch-button {
    border: 0;
    background: transparent;
    color: #44d7ff;
    font-weight: 700;
    cursor: pointer;
    margin-left: 4px;
    padding: 4px;
}

.feltus-auth-switch-button:hover {
    color: #ffffff;
    text-decoration: underline;
}

.feltus-signup-success {
    border: 1px solid rgba(61, 231, 177, .35);
    background: rgba(20, 150, 110, .12);
    color: #9ff7d6;
    border-radius: 8px;
    padding: 12px;
    line-height: 1.45;
    font-size: 14px;
}
</style>
<!-- FELTUS SIGNUP STYLE END -->

'@

$indexText = [regex]::Replace(
    $indexText,
    '(?s)\s*<!-- FELTUS SIGNUP STYLE START -->.*?<!-- FELTUS SIGNUP STYLE END -->',
    ''
)

$indexText = $indexText.Replace(
    "</head>",
    $signupCss + "`r`n</head>"
)


# ============================================================
# ADD SIGNUP JAVASCRIPT
# ============================================================

$signupJs = @'

<!-- FELTUS SIGNUP SCRIPT START -->
<script>
(function () {

    const loginForm =
        document.getElementById(
            "login-form"
        );

    const signupForm =
        document.getElementById(
            "signup-form"
        );

    const switchButton =
        document.getElementById(
            "auth-switch-button"
        );

    const switchLabel =
        document.getElementById(
            "auth-switch-label"
        );

    const headerTitle =
        document.querySelector(
            ".auth-card-header h2"
        );

    const headerSubtitle =
        document.querySelector(
            ".auth-card-header p"
        );

    const signupError =
        document.getElementById(
            "signup-error"
        );

    const signupSuccess =
        document.getElementById(
            "signup-success"
        );

    const signupButton =
        document.getElementById(
            "signup-button"
        );

    let mode = "login";


    function setMode(nextMode) {

        mode = nextMode;

        const signup =
            nextMode === "signup";

        loginForm.hidden =
            signup;

        signupForm.hidden =
            !signup;

        signupError.hidden =
            true;

        signupSuccess.hidden =
            true;


        if (signup) {

            headerTitle.textContent =
                "Create Your Account";

            headerSubtitle.textContent =
                "Start with 10 free lifetime extraction pages";

            switchLabel.textContent =
                "Already have an account?";

            switchButton.textContent =
                "Sign In";

        } else {

            headerTitle.textContent =
                "Welcome Back";

            headerSubtitle.textContent =
                "Sign in to your extraction workspace";

            switchLabel.textContent =
                "New to FELTUS?";

            switchButton.textContent =
                "Create Account";
        }
    }


    switchButton.addEventListener(
        "click",
        function () {

            setMode(
                mode === "login"
                    ? "signup"
                    : "login"
            );
        }
    );


    signupForm.addEventListener(
        "submit",
        async function (event) {

            event.preventDefault();

            signupError.hidden = true;
            signupSuccess.hidden = true;


            const fullName =
                document.getElementById(
                    "signup-name"
                ).value.trim();

            const organizationName =
                document.getElementById(
                    "signup-organization"
                ).value.trim();

            const email =
                document.getElementById(
                    "signup-email"
                ).value.trim();

            const password =
                document.getElementById(
                    "signup-password"
                ).value;

            const confirmPassword =
                document.getElementById(
                    "signup-confirm-password"
                ).value;


            if (
                password !==
                confirmPassword
            ) {

                signupError.textContent =
                    "Passwords do not match.";

                signupError.hidden =
                    false;

                return;
            }


            if (
                password.length < 8
            ) {

                signupError.textContent =
                    "Password must contain at least 8 characters.";

                signupError.hidden =
                    false;

                return;
            }


            const text =
                signupButton.querySelector(
                    ".button-text"
                );

            const spinner =
                signupButton.querySelector(
                    ".spinner"
                );

            signupButton.disabled =
                true;

            text.textContent =
                "Creating Account...";

            spinner.hidden =
                false;


            try {

                const response =
                    await fetch(
                        "/api/auth/register",
                        {
                            method: "POST",

                            credentials:
                                "same-origin",

                            headers: {
                                "Content-Type":
                                    "application/json"
                            },

                            body:
                                JSON.stringify({
                                    full_name:
                                        fullName,

                                    organization_name:
                                        organizationName,

                                    email:
                                        email,

                                    password:
                                        password
                                })
                        }
                    );


                const data =
                    await response.json();


                if (!response.ok) {

                    throw new Error(
                        data.detail ||
                        "Account creation failed."
                    );
                }


                if (
                    data.requires_confirmation
                ) {

                    signupSuccess.textContent =
                        "Your account was created. " +
                        "Check your email to confirm your account, " +
                        "then return here and sign in.";

                    signupSuccess.hidden =
                        false;

                    return;
                }


                /*
                 * Account and organization now exist.
                 *
                 * The selected plan from /pricing remains in
                 * localStorage.
                 *
                 * Existing FELTUS plan-continuation logic will:
                 *
                 * FREE -> dashboard
                 * PAID -> Stripe Checkout
                 */

                window.location.href =
                    "/app";


            } catch (error) {

                signupError.textContent =
                    error.message;

                signupError.hidden =
                    false;

            } finally {

                signupButton.disabled =
                    false;

                text.textContent =
                    "Create Account";

                spinner.hidden =
                    true;
            }
        }
    );

})();
</script>
<!-- FELTUS SIGNUP SCRIPT END -->

'@

$indexText = [regex]::Replace(
    $indexText,
    '(?s)\s*<!-- FELTUS SIGNUP SCRIPT START -->.*?<!-- FELTUS SIGNUP SCRIPT END -->',
    ''
)

$indexText = $indexText.Replace(
    "</body>",
    $signupJs + "`r`n</body>"
)

Set-Content `
    $index `
    $indexText `
    -Encoding UTF8


# ============================================================
# VERIFY PYTHON
# ============================================================

& "$root\.venv\Scripts\python.exe" `
    -m py_compile `
    "$root\app\signup_routes.py"

if ($LASTEXITCODE -ne 0) {
    throw "signup_routes.py syntax check failed."
}

& "$root\.venv\Scripts\python.exe" `
    -m py_compile `
    "$root\app\main.py"

if ($LASTEXITCODE -ne 0) {
    throw "main.py syntax check failed."
}


Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "FELTUS CREATE ACCOUNT FLOW INSTALLED" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Pricing -> Create Account -> Organization" -ForegroundColor White
Write-Host "Free -> 10 lifetime pages -> Dashboard" -ForegroundColor White
Write-Host "Paid -> Stripe Checkout -> Dashboard" -ForegroundColor White
Write-Host ""
Write-Host "New endpoint:" -ForegroundColor Cyan
Write-Host "POST /api/auth/register"
Write-Host ""