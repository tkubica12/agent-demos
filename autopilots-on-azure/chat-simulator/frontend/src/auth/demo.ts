import type { ApiError } from "../api";

export const DEMO_EXPIRED = "Demo expired. Login and access to test data are no longer available.";
export const DEMO_SESSION_EXPIRED = "Demo session expired. Sign in again if the demo is still available.";
export const DEMO_REFUSED = "Demo session refused by the server. It may have expired. Reload to check availability.";

export function expiryTime(value: unknown): number {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)$/.test(value)
    || !Number.isFinite(Date.parse(value))) throw new Error("Invalid demo expiry configuration.");
  return Date.parse(value);
}

export function assertDemoActive(expiresAt: string, message = DEMO_EXPIRED) {
  if (Date.now() >= expiryTime(expiresAt)) throw new Error(message);
}

export function demoApiRefusal(error: ApiError): string | undefined {
  const detail = `${error.message} ${JSON.stringify(error.diagnostics ?? "")}`;
  if ([401, 403, 410].includes(error.status) && /expir|demo[_ -](disabled|unavailable)/i.test(detail)) {
    return DEMO_EXPIRED;
  }
  if ([401, 410].includes(error.status)) return DEMO_REFUSED;
  return undefined;
}

export function demoSocketRefusal(code: number, reason: string): string | undefined {
  if (/expir/i.test(reason)) return DEMO_EXPIRED;
  if ([1008, 4001, 4003, 4010, 4401, 4403, 4410].includes(code)) return DEMO_REFUSED;
  return undefined;
}

export class DemoSession {
  private refusal?: string;
  private listeners = new Set<(message: string) => void>();

  constructor(readonly expiresAt: string) { expiryTime(expiresAt); }

  subscribe(listener: (message: string) => void): () => void {
    this.listeners.add(listener);
    if (this.refusal) listener(this.refusal);
    return () => { this.listeners.delete(listener); };
  }

  refuse(message: string) {
    if (this.refusal) return;
    this.refusal = message;
    for (const listener of this.listeners) listener(message);
  }

  assertActive() {
    if (this.refusal) throw new Error(this.refusal);
    try { assertDemoActive(this.expiresAt, DEMO_SESSION_EXPIRED); }
    catch (error) {
      this.refuse(error instanceof Error ? error.message : DEMO_SESSION_EXPIRED);
      throw error;
    }
  }
}
