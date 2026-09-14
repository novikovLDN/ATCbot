import {
  browserSupportsWebAuthn,
  startAuthentication,
  startRegistration,
} from "@simplewebauthn/browser";
import { api, ApiError } from "./api";

/**
 * Browser-side passkey helpers. The server endpoints produce
 * SimpleWebAuthn-compatible options, so we can hand them straight to
 * the SDK.
 */

export const isPasskeySupported = () => browserSupportsWebAuthn();

export async function registerPasskey(label?: string): Promise<void> {
  const { options, challenge_token } = await api.post<{
    options: any;
    challenge_token: string;
  }>("/auth/passkey/register/options");
  let credential;
  try {
    credential = await startRegistration({ optionsJSON: options });
  } catch (e: any) {
    if (e?.name === "NotAllowedError" || e?.name === "AbortError") {
      throw new ApiError(0, "cancelled");
    }
    throw new ApiError(0, e?.message ?? "registration_failed");
  }
  await api.post("/auth/passkey/register/verify", {
    challenge_token,
    credential,
    label,
  });
}

export async function loginWithPasskey(): Promise<void> {
  const { options, challenge_token } = await api.post<{
    options: any;
    challenge_token: string;
  }>("/auth/passkey/auth/options");
  let credential;
  try {
    credential = await startAuthentication({ optionsJSON: options });
  } catch (e: any) {
    if (e?.name === "NotAllowedError" || e?.name === "AbortError") {
      throw new ApiError(0, "cancelled");
    }
    throw new ApiError(0, e?.message ?? "authentication_failed");
  }
  await api.post("/auth/passkey/auth/verify", {
    challenge_token,
    credential,
  });
}

export interface PasskeyRow {
  id: number;
  label?: string;
  aaguid?: string;
  transports?: string[];
  created_at?: string;
  last_used_at?: string | null;
}

export const passkeyList = () =>
  api.get<PasskeyRow[]>("/auth/passkey/list");
export const passkeyDelete = (id: number) =>
  api.del<{ ok: boolean }>(`/auth/passkey/${id}`);
