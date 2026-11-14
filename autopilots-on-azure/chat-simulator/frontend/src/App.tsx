import { useEffect, useState } from "react";
import { Button, FluentProvider, webDarkTheme, webLightTheme } from "@fluentui/react-components";
import { ApiError, type AuthConfig } from "./api";
import { assertDemoActive, DEMO_EXPIRED, expiryTime } from "./auth/demo";
import { initializeAuth, type AuthController, type Session } from "./auth/session";
import { EmojiProvider } from "./emoji";
import { Workspace } from "./Workspace";

export function App() {
  const [auth, setAuth] = useState<AuthController | null>(null);
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [accessCode, setAccessCode] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [demoExpired, setDemoExpired] = useState(false);
  const [dark, setDark] = useState(localStorage.getItem("tsim.theme") === "dark");
  const demo = config?.authMode === "demo" ? config.demo : undefined;
  const serverExpired = auth?.mode === "demo" && auth.expired;
  useEffect(() => { document.documentElement.dataset.theme = dark ? "dark" : "light"; }, [dark]);
  useEffect(() => {
    void initializeAuth(setConfig).then((value) => { setAuth(value); setSession(value.session); })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Authentication initialization failed."));
  }, []);
  useEffect(() => {
    if (!demo) return;
    let timer: ReturnType<typeof setTimeout>;
    const unsubscribe = session?.demo?.subscribe((message) => {
      setSession(null); setError(message);
      if (message === DEMO_EXPIRED) setDemoExpired(true);
    });
    const check = () => {
      clearTimeout(timer);
      try {
        if (serverExpired) throw new Error(DEMO_EXPIRED);
        assertDemoActive(demo.expiresAt);
        session?.demo?.assertActive();
        const deadline = Math.min(expiryTime(demo.expiresAt),
          session?.demo ? expiryTime(session.demo.expiresAt) : Infinity);
        timer = setTimeout(check, Math.min(Math.max(0, deadline - Date.now()), 2_147_483_647));
      } catch (reason) {
        setDemoExpired(serverExpired || Date.now() >= Date.parse(demo.expiresAt));
        setSession(null);
        setError(reason instanceof Error ? reason.message : DEMO_EXPIRED);
      }
    };
    check();
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    return () => {
      clearTimeout(timer); unsubscribe?.();
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", check);
    };
  }, [demo, session, serverExpired]);
  function theme() {
    setDark((current) => { localStorage.setItem("tsim.theme", current ? "light" : "dark"); return !current; });
  }
  const demoMode = config?.authMode === "demo";
  return <FluentProvider theme={dark ? webDarkTheme : webLightTheme}
    className={`app-provider ${dark ? "dark" : ""} ${demoMode ? "demo-mode" : ""}`}>
    {demoMode && <div className="demo-warning" role="alert" data-testid="demo-warning">
      {demo?.banner || "DEMO MODE – no real authentication – test data only"}
    </div>}
    <div className="app-content"><EmojiProvider>
      {session ? <Workspace key={`${session.mode}:${session.user.id}`} session={session} dark={dark} onTheme={theme}
        onSwitchAccount={() => {
          sessionStorage.removeItem("tsim.testPersona"); setSession(null); setError(""); setAccessCode("");
        }} />
        : <div className="login-page"><section><div className="simulator-wordmark">AGENT LAB</div>
          <h1>Agent Chat Simulator</h1><p>A separate collaboration space for exploring agents with test data.</p>
          {demoMode ? <form className="demo-login" onSubmit={(event) => {
            event.preventDefault();
            if (auth?.mode !== "demo" || busy || demoExpired) return;
            setBusy(true); setError("");
            const code = accessCode;
            setAccessCode("");
            void auth.demoSignIn(code, displayName).then(setSession)
              .catch((reason: unknown) => {
                if (reason instanceof ApiError && reason.status === 410) setDemoExpired(true);
                setError(reason instanceof Error ? reason.message : "Demo login refused.");
              })
              .finally(() => setBusy(false));
          }}>
            <label htmlFor="demo-access-code">Access code</label>
            <input id="demo-access-code" type="password" autoComplete="off" required
              value={accessCode} onChange={(event) => setAccessCode(event.target.value)} disabled={busy || demoExpired}
              aria-describedby="auth-status" />
            <label htmlFor="demo-display-name">Display name</label>
            <input id="demo-display-name" autoComplete="off" required
              value={displayName} onChange={(event) => setDisplayName(event.target.value)} disabled={busy || demoExpired} />
            <Button type="submit" appearance="primary" disabled={auth?.mode !== "demo" || busy || demoExpired
              || !accessCode.trim() || !displayName.trim()}>Enter demo</Button>
          </form> : config?.authMode === "entra" && <Button id="sign-in" appearance="primary" disabled={auth?.mode !== "entra" || busy}
            onClick={() => {
              if (auth?.mode !== "entra") return;
              setBusy(true); setError("");
              void auth.signIn().catch((reason: unknown) => {
                setError(reason instanceof Error ? reason.message : "Entra sign-in failed."); setBusy(false);
              });
            }}>Sign in with Microsoft Entra</Button>}
          <p role="status" id="auth-status" data-testid="auth-status">{!auth && !error ? "Initializing sign-in…"
            : demoMode ? demoExpired ? "Demo expired. Access is closed." : "Use the shared demo access code. Test data only."
              : "Sign in with an assigned simulator account."}</p>
          {error && <div role="alert" data-testid="auth-error">{error}<Button onClick={() => location.reload()}>Reload sign-in</Button></div>}
          {auth?.mode === "entra" && auth.config.testAuth && <div className="test-login"><h2>Local test identities</h2>
            <p>Explicitly enabled development-only OIDC. Not a substitute for real Entra authentication.</p>
            {auth.config.testAuth.personas.map((persona) => <Button key={persona.id} disabled={busy}
              data-testid={`test-login-${persona.id}`} onClick={() => {
                setBusy(true); setError("");
                void auth.testSignIn(persona).then((value) => {
                  sessionStorage.setItem("tsim.testPersona", persona.id); setSession(value);
                }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Local test sign-in failed."))
                  .finally(() => setBusy(false));
              }}>{persona.displayName}</Button>)}
          </div>}
        </section></div>}
    </EmojiProvider></div>
  </FluentProvider>;
}
