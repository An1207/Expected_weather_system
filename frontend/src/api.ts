import type { Dashboard } from "./types";

type ApiErrorBody = { detail?: string };

export async function fetchDashboard(url: string): Promise<Dashboard> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new Error(body.detail ?? `데이터를 불러오지 못했습니다 (${response.status})`);
  }
  return response.json() as Promise<Dashboard>;
}

