import { useEffect, useState } from "react";
import { getBackendHealth } from "../services/healthService";

type ConnectionStatus = "checking" | "connected" | "disconnected";

export function useBackendHealth() {
  const [status, setStatus] = useState<ConnectionStatus>("checking");

  useEffect(() => {
    let active = true;

    async function checkConnection() {
      try {
        const response = await getBackendHealth();

        if (active) {
          setStatus(response.status === "ok" ? "connected" : "disconnected");
        }
      } catch {
        if (active) {
          setStatus("disconnected");
        }
      }
    }

    checkConnection();

    const interval = setInterval(checkConnection, 10000);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  return status;
}
