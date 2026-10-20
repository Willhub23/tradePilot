"""Read-only broker projections. Decimal amounts stay strings at the API boundary."""
from datetime import datetime, timezone
from decimal import Decimal
from threading import RLock
from time import monotonic, sleep

from ctrader_open_api.messages import OpenApiMessages_pb2 as M
from sqlalchemy import select, or_
from app.database import SessionLocal
from app.db_models import BrokerExecutionRecord as B, StrategyEvaluationRecord as S


def field(message, name):
    return getattr(message, name) if message.HasField(name) else None


def money(message, name):
    amount, digits = field(message, name), field(message, 'moneyDigits')
    if amount is None or digits is None or not 0 <= digits <= 12: return None
    return Decimal(amount) / Decimal(10) ** digits


def text(value):
    return str(value) if value is not None else None


def stamp(value):
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat() if value is not None else None


def milliseconds_ceil(value):
    delta=value-datetime(1970,1,1,tzinfo=timezone.utc)
    microseconds=(delta.days*86400+delta.seconds)*1000000+delta.microseconds
    return (microseconds+999)//1000


class AccountUnavailable(RuntimeError):
    pass


class ReadOnlyEvents:
    def process(self, *args, **kwargs):
        pass  # Account inspection must not mutate execution history on incidental events.


class AccountDashboardService:
    def __init__(self, client, store):
        self.client, self.store = client, store
        self.lock = RLock()
        self.cache = {}
        self.started = False
        self.last_history_request = None
        self.error = None

    def start(self):
        self.store.acquire()  # Same lease as the autonomous runtime; before authentication.
        try:
            self.client.order_guard = lambda: False
            self.client.close_guard = lambda: False
            self.client.start()
            if not self.client.wait_until_authenticated(30):
                raise AccountUnavailable('DEMO authentication failed')
            self.check()
            self.started = True
        except Exception:
            try: self.client.stop()
            finally: self.store.release()
            raise

    def stop(self):
        with self.lock:
            self.started = False
            self.cache.clear()
            try: self.client.stop()
            finally: self.store.release()

    def check(self):
        if self.client.environment != 'demo' or not self.client.demo_verified:
            raise AccountUnavailable('Verified DEMO account required')
        if not self.client.authenticated:
            raise AccountUnavailable('Broker disconnected or unauthenticated')
        self.store.assert_owner()

    def request(self, name, response, **kwargs):
        self.check()
        if name == 'ProtoOADealListReq':
            if self.last_history_request is not None:
                sleep(max(0, .25-(monotonic()-self.last_history_request)))
            self.last_history_request = monotonic()
        result = self.client.send(getattr(M, name)(ctidTraderAccountId=self.client.account_id, **kwargs))
        if isinstance(result, M.ProtoOAErrorRes):
            raise AccountUnavailable('Broker request unavailable: ' + result.errorCode)
        if not isinstance(result, getattr(M, response)) or not result.IsInitialized() or result.ctidTraderAccountId != self.client.account_id:
            raise AccountUnavailable('Incomplete or mismatched broker response')
        return result

    def cached(self, key, loader):
        with self.lock:
            if not self.started: raise AccountUnavailable(self.error or 'Account dashboard is not connected')
            self.check()  # Never serve cached values after disconnect or lost ownership.
            generation = self.client.generation
            saved = self.cache.get(key)
            if saved and saved[0] == generation and monotonic()-saved[1] < 30:
                return {**saved[2], 'cached': True, 'age_seconds': monotonic()-saved[1]}
            self.cache.pop(key, None)
            value = loader()
            self.check()
            if self.client.generation != generation: raise AccountUnavailable('Connection changed during account retrieval')
            result = {**value, 'account_id': str(self.client.account_id), 'environment': 'demo',
                      'retrieved_at': datetime.now(timezone.utc).isoformat(),
                      'timestamp_semantics': 'Local response receipt; not broker market-event time',
                      'cached': False, 'age_seconds': 0}
            if len(self.cache) >= 16:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (generation, monotonic(), result)
            return result

    def symbols(self):
        res = self.request('ProtoOASymbolsListReq', 'ProtoOASymbolsListRes')
        return {s.symbolId: field(s,'symbolName') for s in res.symbol}

    def currency(self, trader):
        res = self.request('ProtoOAAssetListReq','ProtoOAAssetListRes')
        asset = next((a for a in res.asset if a.assetId == trader.depositAssetId), None)
        return asset.name if asset is not None else None

    def overview(self):
        def load():
            trader = self.request('ProtoOATraderReq','ProtoOATraderRes').trader
            if trader.ctidTraderAccountId != self.client.account_id: raise AccountUnavailable('Unexpected trader identity')
            currency = self.currency(trader)
            balance = money(trader,'balance')
            unavailable = {}
            unrealized, equity = None, None
            try:
                positions = self.request('ProtoOAReconcileReq','ProtoOAReconcileRes').position
                pnl = self.request('ProtoOAGetPositionUnrealizedPnLReq','ProtoOAGetPositionUnrealizedPnLRes')
                ids = [p.positionId for p in pnl.positionUnrealizedPnL]
                if len(ids) != len(set(ids)) or set(ids) != {p.positionId for p in positions}:
                    raise AccountUnavailable('Position/P&L snapshots do not match')
                if not 0 <= pnl.moneyDigits <= 12: raise AccountUnavailable('Invalid P&L scaling')
                unrealized = sum((Decimal(p.netUnrealizedPnL) / Decimal(10)**pnl.moneyDigits for p in pnl.positionUnrealizedPnL), Decimal(0))
            except Exception:
                unavailable['unrealized_pnl'] = 'Broker P&L unavailable or position snapshots changed'
            bonuses = [field(trader,n) for n in ('managerBonus','ibBonus','nonWithdrawableBonus')]
            if balance is not None and unrealized is not None and all(v == 0 for v in bonuses): equity = balance + unrealized
            else: unavailable['equity'] = 'Cannot verify balance + net P&L equity: missing scaling/P&L or nonzero/unknown bonuses'
            if balance is None: unavailable['balance'] = 'Missing or invalid broker moneyDigits'
            if currency is None: unavailable['currency'] = 'Deposit asset not found in broker asset list'
            return dict(currency=currency, balance=text(balance), unrealized_pnl=text(unrealized), equity=text(equity),
                equity_source='balance + broker net unrealized P&L; verified zero bonuses' if equity is not None else None,
                unavailable=unavailable)
        return self.cached('overview',load)

    def positions(self):
        def load():
            rows = self.request('ProtoOAReconcileReq','ProtoOAReconcileRes').position
            symbols = self.symbols()
            return {'currency': self.overview()['currency'], 'items':[dict(position_id=str(p.positionId), symbol=symbols.get(p.tradeData.symbolId),
                symbol_id=str(p.tradeData.symbolId), direction='long' if p.tradeData.tradeSide==1 else 'short',
                volume_units=str(Decimal(p.tradeData.volume)/100), volume_lots=None,
                entry_price=field(p,'price'), stop_loss=field(p,'stopLoss'), take_profit=field(p,'takeProfit'),
                opened_at=stamp(field(p.tradeData,'openTimestamp')), swap=text(money(p,'swap')),
                commission=text(money(p,'commission')), currency_note='Account deposit currency for monetary fields; lots unavailable without symbol lotSize') for p in rows]}
        return self.cached('positions',load)

    def history(self, since, until):
        def load():
            pending=[(milliseconds_ceil(since), milliseconds_ceil(until)-1)]
            deals={}
            calls=0
            retrieval_started=monotonic()
            while pending:
                if monotonic()-retrieval_started > 20:
                    raise AccountUnavailable("History request time budget exceeded; narrow date range")
                if calls >= 31: raise AccountUnavailable('History exceeds bounded request budget; narrow date range')
                lo,hi=pending.pop();calls+=1
                if hi<lo: continue
                response=self.request('ProtoOADealListReq','ProtoOADealListRes',fromTimestamp=lo,toTimestamp=hi,maxRows=1000)
                if response.hasMore:
                    if lo>=hi: raise AccountUnavailable('Broker truncated deals at the same millisecond; history incomplete')
                    mid=(lo+hi)//2
                    pending.extend([(lo,mid),(mid+1,hi)])
                    continue
                for d in response.deal:
                    if lo <= d.executionTimestamp <= hi: deals[d.dealId]=d
            symbols=self.symbols()
            currency=self.overview()['currency']
            rows=[]
            for deal in deals.values():
                if not deal.HasField('closePositionDetail') or deal.dealStatus not in (2,3) or deal.filledVolume <= 0: continue
                close=deal.closePositionDetail
                gross,swap,commission = (money(close,n) for n in ('grossProfit','swap','commission'))
                fee=money(close,'pnlConversionFee')
                net=gross+swap-abs(commission)-(abs(fee) if fee is not None else Decimal(0)) if all(v is not None for v in (gross,swap,commission)) else None
                rows.append(dict(deal_id=str(deal.dealId),order_id=str(deal.orderId),position_id=str(deal.positionId),
                    symbol=symbols.get(deal.symbolId),symbol_id=str(deal.symbolId),
                    closing_side='buy' if deal.tradeSide==1 else 'sell',
                    volume_units=str(Decimal(close.closedVolume)/100) if close.HasField('closedVolume') else None,
                    filled_volume_units=str(Decimal(deal.filledVolume)/100), volume_lots=None,
                    closed_at=stamp(deal.executionTimestamp), entry_price=close.entryPrice, exit_price=field(deal,'executionPrice'),
                    gross_profit=text(gross),swap=text(swap),commission=text(commission),conversion_fee=text(fee),
                    net_after_reported_costs=text(net),costs_complete=fee is not None,
                    linkage=[], attribution='broker_only'))
            rows.sort(key=lambda r:(r['closed_at'],int(r['deal_id'])),reverse=True)
            self.link(rows)
            return dict(items=rows,currency=currency,since=since.isoformat(),until=until.isoformat(),history_complete=True,
                basis='Closing deals, including partial closes; not inferred full-position round trips',
                cost_note='Gross + signed swap - commission charge magnitude - available conversion fee. Closing detail commission used once; deal commission not double counted. Missing optional fees are not confirmed zero.')
        return self.cached(('history',since,until),load)

    def link(self, rows):
        # Broker IDs only; no attribution from labels, symbols, timestamps or direction.
        if not rows: return
        with SessionLocal() as db:
            matches=db.execute(select(B,S.id).outerjoin(S,S.request_id==B.request_id).where(or_(
                B.broker_order_id.in_([r['order_id'] for r in rows]),
                B.broker_position_id.in_([r['position_id'] for r in rows])))).all()
        for row in rows:
            for execution,evaluation_id in matches:
                exact_order=execution.broker_order_id==row['order_id']
                account_id=execution.request_data.get('account_id',execution.request_data.get('ctidTraderAccountId'))
                if account_id is not None and str(account_id)!=str(self.client.account_id): continue
                if exact_order or execution.broker_position_id==row['position_id']:
                    row['linkage'].append(dict(request_id=execution.request_id,decision_id=execution.agent_decision_id,
                        evaluation_id=evaluation_id,match='order' if exact_order else 'position',account_verified=account_id is not None))
            if row['linkage']:
                row['attribution']='tradepilot_linked_position_or_order' if any(link['account_verified'] for link in row['linkage']) else 'unverified_tradepilot_id_match'

    def trades(self, since, until, page=1, page_size=20):
        data=self.history(since,until)
        return {**data,'items':data['items'][(page-1)*page_size:page*page_size],
                'total':len(data['items']),'page':page,'page_size':page_size}

    def performance(self,since,until):
        data=self.history(since,until)
        grouped={}
        unknown=set()
        for row in data['items']:
            key=row['position_id']
            if row['net_after_reported_costs'] is None: unknown.add(key)
            else: grouped[key]=grouped.get(key,Decimal(0))+Decimal(row['net_after_reported_costs'])
        values=[v for k,v in grouped.items() if k not in unknown]
        wins=[v for v in values if v>0];losses=[v for v in values if v<0]
        valid=not unknown
        return {k:v for k,v in data.items() if k!='items'} | dict(
            basis='Per-position realized closing-deal P&L within selected window, including partial closes; not whole-lifetime trade statistics',
            realized_net=text(sum(values,Decimal(0))) if valid else None,
            positions_with_realizations=len(grouped.keys()|unknown),closing_deals=len(data['items']),
            win_rate=float(Decimal(len(wins))/len(values)*100) if values and valid else None,
            average_win=text(sum(wins)/len(wins)) if wins and valid else None,
            average_loss=text(sum(losses)/len(losses)) if losses and valid else None,
            profit_factor=text(sum(wins)/abs(sum(losses))) if wins and losses and valid else None,
            costs_complete=all(r['costs_complete'] for r in data['items']),
            unavailable_reason='Missing monetary scaling or required P&L fields' if not valid else 'Profit factor needs both gains and losses; optional unreported costs excluded')
