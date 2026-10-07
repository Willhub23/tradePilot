export interface MarketQuote {
  symbol: string;
  price: number;
  currency: string;
  timestamp: string;
  source: string;
}

export async function getMarketPrice(symbol: string): Promise<MarketQuote> {
  const response = await fetch(`/api/market/${symbol}`);

  if (!response.ok) {
    throw new Error("Failed to fetch market price");
  }

  return response.json();
}
