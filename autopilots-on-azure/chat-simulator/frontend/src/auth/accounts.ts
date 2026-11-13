import type { AccountInfo } from "@azure/msal-browser";

export function selectCachedAccount(
  accounts: readonly AccountInfo[],
  activeAccount: AccountInfo | null,
  config: { tenantId: string; clientId: string },
): AccountInfo | undefined {
  const eligible = accounts.filter(
    (account) => account.tenantId === config.tenantId &&
      account.idTokenClaims?.aud === config.clientId &&
      account.idTokenClaims.roles?.some((role) => role === "User" || role === "Admin"),
  );
  return eligible.find((account) => account.homeAccountId === activeAccount?.homeAccountId)
    ?? (eligible.length === 1 ? eligible[0] : undefined);
}
