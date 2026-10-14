from datetime import datetime, timezone


class MarketSessionService:
    MAX_QUOTE_AGE_SECONDS = 120

    def is_market_open(
        self,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)

        weekday = now.weekday()

        # Saturday
        if weekday == 5:
            return False

        # Sunday before 22:00 UTC
        if weekday == 6 and now.hour < 22:
            return False

        # Friday after 22:00 UTC
        if weekday == 4 and now.hour >= 22:
            return False

        return True

    def is_quote_fresh(
        self,
        quote_timestamp: datetime,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)

        age = (now - quote_timestamp).total_seconds()

        return age <= self.MAX_QUOTE_AGE_SECONDS
