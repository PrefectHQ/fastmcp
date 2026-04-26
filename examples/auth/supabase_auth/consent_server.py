from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

SUPABASE_URL = os.environ["SUPABASE_PROJECT_URL"]
ANON_KEY = os.environ["SUPABASE_ANON_KEY"]

# "GITHUB_SIGN_IN" (default, correct for OAuth)
# "MAGIC_LINK" (debug only)
AUTH_TYPE = os.environ.get("AUTH_TYPE", "GITHUB_SIGN_IN")


@app.get("/oauth/consent", response_class=HTMLResponse)
async def consent_page(request: Request):
    authorization_id = request.query_params.get("authorization_id")

    if not authorization_id:
        return HTMLResponse("Missing authorization_id", status_code=400)

    return HTMLResponse(f"""
<!DOCTYPE html>
<html>
<head>
  <title>OAuth Consent</title>
  <style>
    body {{ font-family: monospace; }}
    #debug {{ white-space: pre-wrap; background: #111; color: #0f0; padding: 10px; }}
  </style>
</head>
<body>
  <h1>Loading...</h1>
  <pre id="debug">Starting...</pre>

  <script type="module">
    import {{ createClient }} from "https://esm.sh/@supabase/supabase-js@2"

    const AUTH_TYPE = "{AUTH_TYPE}"

    const debugEl = document.getElementById("debug")

    function log(...args) {{
      console.log(...args)
      debugEl.textContent += "\\n" + args.map(a =>
        typeof a === "object" ? JSON.stringify(a, null, 2) : a
      ).join(" ")
    }}

    log("🚀 Consent page loaded")
    log("🔐 AUTH_TYPE:", AUTH_TYPE)
    log("🌍 Location:", window.location.href)
    log("🌐 Origin:", window.location.origin)
    log("🍪 document.cookie:", document.cookie || "(empty)")

    const supabase = createClient(
      "{SUPABASE_URL}",
      "{ANON_KEY}"
    )

    const authorizationId = "{authorization_id}"

    async function run() {{
      log("▶️ Starting OAuth consent flow")

      try {{
        // ---- Session check ----
        const sessionRes = await supabase.auth.getSession()
        log("📦 getSession():", sessionRes)

        const userRes = await supabase.auth.getUser()
        log("👤 getUser():", userRes)

        const user = userRes.data?.user

        if (!user) {{
          log("⚠️ NO USER SESSION")

          if (AUTH_TYPE === "GITHUB_SIGN_IN") {{
            log("🔐 Redirecting to GitHub OAuth login...")

            const {{ data, error }} = await supabase.auth.signInWithOAuth({{
              provider: "github",
              options: {{
                redirectTo: window.location.href
              }}
            }})

            log("GitHub login result:", {{ data, error }})
            return
          }}

          if (AUTH_TYPE === "MAGIC_LINK") {{
            log("📧 Showing magic link UI")

            document.body.innerHTML = `
              <h2>No session</h2>
              <p>Enter email for magic link</p>

              <input id="email" type="email" />
              <button id="login">Send</button>

              <pre id="debug">${{debugEl.textContent}}</pre>
            `

            document.getElementById("login").onclick = async () => {{
              const email = document.getElementById("email").value
              log("📧 Sending magic link:", email)

              const res = await supabase.auth.signInWithOtp({{
                email,
                options: {{
                  emailRedirectTo: window.location.href
                }}
              }})

              log("Magic link result:", res)
            }}

            return
          }}
        }}

        log("✅ User authenticated:", user.id)

        // ---- Fetch authorization details ----
        const authRes =
          await supabase.auth.oauth.getAuthorizationDetails(authorizationId)

        log("📦 Authorization details:", authRes)

        if (authRes.error) {{
          log("❌ Authorization error:", authRes.error)

          document.body.innerHTML = `
            <h2>Error</h2>
            <pre>${{JSON.stringify(authRes.error, null, 2)}}</pre>
            <pre id="debug">${{debugEl.textContent}}</pre>
          `
          return
        }}

        const data = authRes.data

        log("✅ Authorization loaded")
        log("Client:", data.client.name)
        log("Scopes:", data.scope)

        // ---- Render UI ----
        document.body.innerHTML = `
          <h1>Authorize ${{data.client.name}}</h1>
          <p>Scopes: ${{data.scope}}</p>

          <button id="approve">Approve</button>
          <button id="deny">Deny</button>

          <pre id="debug">${{debugEl.textContent}}</pre>
        `

        function logToPage(msg) {{
          console.log(msg)
          document.getElementById("debug").textContent += "\\n" + msg
        }}

        document.getElementById("approve").onclick = async () => {{
          logToPage("🟢 Approve clicked")

          const res =
            await supabase.auth.oauth.approveAuthorization(authorizationId)

          log("Approve result:", res)

          if (res.error) {{
            logToPage("❌ " + JSON.stringify(res.error))
            return
          }}

          logToPage("➡️ Redirect → " + res.data.redirect_to)
          window.location.href = res.data.redirect_to
        }}

        document.getElementById("deny").onclick = async () => {{
          logToPage("🔴 Deny clicked")

          const res =
            await supabase.auth.oauth.denyAuthorization(authorizationId)

          log("Deny result:", res)

          if (res.error) {{
            logToPage("❌ " + JSON.stringify(res.error))
            return
          }}

          logToPage("➡️ Redirect → " + res.data.redirect_to)
          window.location.href = res.data.redirect_to
        }}

      }} catch (err) {{
        log("🔥 FATAL ERROR:", err)

        document.body.innerHTML = `
          <h2>Fatal error</h2>
          <pre>${{err}}</pre>
          <pre id="debug">${{debugEl.textContent}}</pre>
        `
      }}
    }}

    run()
  </script>
</body>
</html>
    """)
