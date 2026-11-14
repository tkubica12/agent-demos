import { Api, ApiError, readApiResponse, type AuthConfig, type DemoAuthConfig, type EntraAuthConfig, type User } from "../api";
import { selectCachedAccount } from "./accounts";
import { assertDemoActive, DEMO_EXPIRED, DemoSession, demoApiRefusal, expiryTime } from "./demo";

export type Session = { user: User; api: Api; mode: "entra" | "test-oidc" | "demo"; demo?: DemoSession };
type EntraController = {
  config: EntraAuthConfig; session: Session | null; mode: "entra";
  signIn: () => Promise<void>; testSignIn: (persona: User) => Promise<Session>;
};
type DemoController = {
  config: DemoAuthConfig; session: null; mode: "demo"; expired: boolean;
  demoSignIn: (accessCode: string, displayName: string) => Promise<Session>;
};
export type AuthController = EntraController | DemoController;

export async function initializeAuth(onConfig?: (config: AuthConfig) => void): Promise<AuthController> {
  const response = await fetch("/api/config");
  if (!response.ok && response.status !== 410) throw new Error(`Backend configuration unavailable (${response.status}).`);
  const config: AuthConfig = await response.json();
  if (!response.ok && config.authMode !== "demo") {
    throw new Error(`Backend configuration unavailable (${response.status}).`);
  }
  onConfig?.(config);
  if (config.authMode === "demo") {
    if (!config.demo || typeof config.demo.banner !== "string" || !config.demo.banner.trim()) {
      throw new Error("Invalid demo warning configuration.");
    }
    expiryTime(config.demo.expiresAt);
    return {
      config, session: null, mode: "demo", expired: response.status === 410,
      async demoSignIn(accessCode, displayName) {
        if (response.status === 410) throw new Error(DEMO_EXPIRED);
        assertDemoActive(config.demo.expiresAt);
        if (!accessCode.trim() || !displayName.trim()) throw new Error("Enter an access code and display name.");
        let token: { accessToken: string; expiresAt: number };
        try {
          token = await readApiResponse(await fetch("/api/demo/login", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ accessCode, displayName: displayName.trim() }),
          }));
        } catch (error) {
          if (error instanceof ApiError) {
            const refusal = demoApiRefusal(error);
            if (refusal && (error.status !== 401 || /expir/i.test(JSON.stringify(error.diagnostics)))) {
              error.message = refusal;
            }
          }
          throw error;
        }
        if (!token || typeof token.accessToken !== "string" || !token.accessToken.trim()
          || typeof token.expiresAt !== "number" || !Number.isFinite(token.expiresAt)
          || !Number.isFinite(new Date(token.expiresAt * 1000).getTime())) {
          throw new Error("Invalid demo login response.");
        }
        const demo = new DemoSession(new Date(Math.min(
          expiryTime(config.demo.expiresAt), token.expiresAt * 1000,
        )).toISOString());
        const api = new Api(async () => { demo.assertActive(); return token.accessToken; }, (error) => {
          const refusal = demoApiRefusal(error);
          if (refusal) { error.message = refusal; demo.refuse(refusal); }
        });
        return { user: await api.request<User>("/me"), api, mode: "demo", demo };
      },
    };
  }
  if (config.authMode !== "entra") throw new Error("Unsupported authentication mode.");
  if (!config.tenantId || !config.clientId || !config.scope) throw new Error("Invalid authentication configuration.");
  const { PublicClientApplication } = await import("@azure/msal-browser");
  const client = new PublicClientApplication({
    auth: {
      clientId: config.clientId, authority: `https://login.microsoftonline.com/${config.tenantId}`,
      redirectUri: config.redirectUri, navigateToLoginRequestUrl: false,
    },
    cache: { cacheLocation: "sessionStorage" },
    system: { loggerOptions: { piiLoggingEnabled: false, loggerCallback: () => undefined } },
  });
  await client.initialize();
  const result = await client.handleRedirectPromise();
  const savedPersona = config.testAuth ? sessionStorage.getItem("tsim.testPersona") : null;
  const account = result?.account ?? (savedPersona ? null
    : selectCachedAccount(client.getAllAccounts(), client.getActiveAccount(), config));
  let session: Session | null = null;
  if (account) {
    if (account.tenantId !== config.tenantId) throw new Error("Sign-in belongs to another tenant.");
    client.setActiveAccount(account);
    const api = new Api(async () => (
      await client.acquireTokenSilent({ scopes: [config.scope], account })
    ).accessToken);
    session = { user: await api.request<User>("/me"), api, mode: "entra" };
  }
  const controller: EntraController = {
    mode: "entra",
    config,
    session,
    signIn: () => client.loginRedirect({ scopes: [config.scope], prompt: "select_account" }),
    async testSignIn(persona: User): Promise<Session> {
      const testAuth = config.testAuth;
      if (!testAuth) throw new Error("The backend has not enabled the local test IdP.");
      const issuer = new URL(testAuth.issuer);
      if (issuer.protocol !== "http:" || !["localhost", "127.0.0.1", "[::1]"].includes(issuer.hostname)) {
        throw new Error("The test IdP must be a loopback service.");
      }
      const endpoint = new URL(testAuth.tokenEndpoint);
      if (endpoint.origin !== issuer.origin) throw new Error("Test token endpoint is outside its configured issuer.");
      const api = new Api(async () => {
        const tokenResponse = await fetch(endpoint, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ userId: persona.id }),
        });
        if (!tokenResponse.ok) throw new Error(`Local test sign-in failed (${tokenResponse.status}).`);
        const token: unknown = await tokenResponse.json();
        if (typeof token !== "object" || token === null || !("access_token" in token)
          || typeof token.access_token !== "string") throw new Error("Invalid test IdP response.");
        return token.access_token;
      });
      return { user: await api.request<User>("/me"), api, mode: "test-oidc" };
    },
  };
  if (savedPersona) {
    const persona = config.testAuth?.personas.find((value) => value.id === savedPersona);
    if (!persona) throw new Error("Saved test persona is not configured by this backend.");
    controller.session = await controller.testSignIn(persona);
  }
  return controller;
}
