import { useEffect, useState } from "react";
import { getMarketPrice, type MarketQuote } from "../services/marketApi";

export function useMarketPrice(symbol: string) {
  const [data, setData] = useState<MarketQuote | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function loadPrice() {
      try {
        const quote = await getMarketPrice(symbol);

        if (active) {
          setData(quote);
          setError(null);
        }
      } catch {
        if (active) {
          setError("Failed to load market data");
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    loadPrice();

    const interval = setInterval(loadPrice, 30000);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [symbol]);

  return {
    data,
    loading,
    error,
  };
}
