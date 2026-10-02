import { useBackendHealth } from "../../hooks/useBackendHealth";

export function BackendStatus() {
  const status = useBackendHealth();

  const colors = {
    checking: "bg-yellow-400",
    connected: "bg-emerald-400",
    disconnected: "bg-red-400",
  };

  const labels = {
    checking: "Checking backend...",
    connected: "Backend connected",
    disconnected: "Backend disconnected",
  };

  return (
    <div className="flex items-center gap-2 text-sm text-zinc-400">
      <span className={`h-2 w-2 rounded-full ${colors[status]}`} />
      <span>{labels[status]}</span>
    </div>
  );
}
