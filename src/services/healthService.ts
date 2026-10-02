export interface HealthResponse {
  status: string;
  service: string;
}

export async function getBackendHealth(): Promise<HealthResponse> {
  const response = await fetch("/api/health");

  if (!response.ok) {
    throw new Error("Backend unavailable");
  }

  return response.json();
}
